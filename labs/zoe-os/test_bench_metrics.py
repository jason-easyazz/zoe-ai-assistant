"""Socket-free checks of JSON plumbing, partial soak evidence and SQL instrumentation."""
import json
import sqlite3
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import bench_kernel as bench
from bench_support import Database, Meter


def test_cli_profile_is_saved_at_top_level(tmp_path, monkeypatch):
    profile = {v: {'phases_us': {'decode': 1, 'consent': 2, 'SQL': 3,
                                 'commit': 4, 'receipt': 5}} for v in bench.VERBS}
    rows = {v: bench.stats([1]) for v in bench.VERBS}
    monkeypatch.setattr(bench, 'latency', lambda *a, **kw: {**rows, 'profile': profile})
    out = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['bench', '--mode', 'latency', '--profile', '--out', str(out)])
    assert bench.main() == 0
    saved = json.loads(out.read_text())
    assert saved['profile'] == profile == saved['latency']['profile']


def test_database_meter_includes_cursor_steps():
    raw, meter = sqlite3.connect(':memory:', isolation_level=None), Meter()
    try:
        db = Database(raw, meter)
        db.execute('CREATE TABLE sample(n)')
        db.execute('BEGIN IMMEDIATE')
        db.executemany('INSERT INTO sample VALUES(?)', [(1,), (2,)])
        db.execute('COMMIT')
        meter.values.clear()
        assert list(db.execute('SELECT n FROM sample')) == [(1,), (2,)]
        assert meter.values['sql_execute'][0] == 4  # execute, two rows, exhaustion
    finally:
        raw.close()


def test_checkpoint_has_its_own_profile_counter(tmp_path):
    raw, meter = sqlite3.connect(tmp_path / 'meter.db', isolation_level=None), Meter()
    try:
        raw.execute('PRAGMA journal_mode=WAL')
        raw.execute('CREATE TABLE sample(n)')
        raw.execute('INSERT INTO sample VALUES(1)')
        db = Database(raw, meter)
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone() == (0, 0, 0)
        assert meter.values['sql_checkpoint_truncate'][0] == 1
        assert meter.values['sql_checkpoint_truncate'][1] > 0
        assert 'sql_commit_sync_inclusive' not in meter.values
    finally:
        raw.close()


def fake_soak(monkeypatch):
    clock, position = [0], [0]
    box = SimpleNamespace(proc=SimpleNamespace(pid=0), sup=lambda op: position[0])

    @contextmanager
    def box_at(*args, **kwargs):
        yield box

    class Work:
        def __init__(self, box):
            pass

        def run(self, verb):
            clock[0] += 1
            position[0] += 2
            return clock[0] * 100

    monkeypatch.setattr(bench, 'box_at', box_at)
    monkeypatch.setattr(bench, 'Work', Work)
    monkeypatch.setattr(bench, 'rss', lambda pid: 100)
    monkeypatch.setattr(bench, 'db_bytes', lambda box: 200)
    monkeypatch.setattr(bench.time, 'perf_counter', lambda: clock[0])


def test_soak_quarters_expose_drift(tmp_path, monkeypatch):
    fake_soak(monkeypatch)
    result = bench.soak(tmp_path, 8, 7)
    assert result['mixed_calls'] == 8 and 'failed' not in result
    assert [q['overall']['p50_us'] for q in result['quarters']] == [100, 300, 500, 700]
    assert [q['start_seq'] for q in result['quarters']] == [0, 4, 8, 12]
    assert [q['end_seq'] for q in result['quarters']] == [4, 8, 12, 16]
    assert all(q['by_verb'] for q in result['quarters'])


def test_timeout_keeps_partial_quarter_and_verb_numbers(tmp_path, monkeypatch):
    fake_soak(monkeypatch)
    result = bench.soak(tmp_path, 20, 7, limit_s=2.5)
    assert result['failed'] == 'wall_budget' and result['mixed_calls'] == 3
    assert result['quarters'][0]['n'] == 3 and result['quarters'][0]['end_seq'] == 6
    assert sum(row['n'] for row in result['by_verb'].values()) == 3
    assert result['quarters'][1]['overall'] is None
