"""[code] Small offline checks of each evidence mode and its failure detectors."""
from __future__ import annotations

import json
import tempfile

import pytest

from bench_kernel import (VERBS, assert_isolated, cold, concurrency, crash, latency,
                          soak, stats)
from bench_support import HERE, MEMBERS


@pytest.fixture
def tmp_path():
    # Short Unix socket paths, with every generated file inside the allowed worktree.
    with tempfile.TemporaryDirectory(prefix='bt-', dir=HERE) as path:
        from pathlib import Path
        yield Path(path)


def test_percentiles_small_n():
    assert stats([7])['p99_us'] == 7
    assert stats([3, 1, 2])['p50_us'] == 2
    with pytest.raises(ValueError):
        stats([])


def test_latency_and_profile(tmp_path):
    result = latency(tmp_path, 2, profile=True)
    assert set(VERBS) <= set(result)
    assert all(result[v]['n'] == 2 for v in VERBS)
    assert result['profile_one_list_add']['consent'][0] == 1
    assert result['profile_one_list_add']['sql_commit_sync_inclusive'][0] == 2
    assert set(result['profile']) == set(VERBS)
    for row in result['profile'].values():
        assert set(row['phases_us']) == {'decode', 'consent', 'SQL', 'commit', 'receipt', 'checkpoint'}
        assert row['phases_us']['decode'] > 0 and row['phases_us']['SQL'] > 0
    assert 'synthetic-' not in json.dumps(result)


def test_concurrent_member_isolation(tmp_path):
    result = concurrency(tmp_path, 2, counts=(1, 4, 16))
    for clients, row in result.items():
        assert row['isolated'] and row['cross_member_denied'] == int(clients) * 2
        assert row['calls_per_s'] > 0


def test_cross_member_negative_control_checks_actual_store(tmp_path):
    with pytest.raises(AssertionError, match='cross-member'):
        concurrency(tmp_path, 2, counts=(4,), misroute=True)


def test_isolation_detector_rejects_extra_and_missing_rows():
    expected = {(MEMBERS[0], 'key')}
    assert_isolated([(MEMBERS[0], 'list', 'key')], expected)
    for rows in ([], [(MEMBERS[1], 'list', 'key')],
                 [(MEMBERS[0], 'list', 'key'), (MEMBERS[1], 'list', 'extra')]):
        with pytest.raises(AssertionError):
            assert_isolated(rows, expected)


def test_small_soak(tmp_path):
    result = soak(tmp_path, 90, 4721)
    assert result['mixed_calls'] == 90 and len(result['by_verb']) == len(VERBS)
    assert result['rss_end_bytes'] > 0 and result['db_end_bytes'] >= result['db_start_bytes']
    assert result['first_decile']['n'] == result['last_decile']['n'] == 9
    assert sum(q['n'] for q in result['quarters']) == 90


def test_soak_timeout_preserves_measurements(tmp_path):
    result = soak(tmp_path, 90, 7, limit_s=0)
    assert result['failed'] == 'wall_budget' and result['mixed_calls'] == 0


def test_crash_recovery(tmp_path):
    result = crash(tmp_path, 6, 4721)
    assert result['kills'] == 6 and result['undo_after_restart']
    assert all(count == 1 for count in result['stages'].values())
    assert result['acknowledged_survive'] and result['no_half_receipt']


def test_crash_negative_control_catches_acknowledged_volatile_effect(tmp_path):
    with pytest.raises(AssertionError, match='lost acknowledged write'):
        crash(tmp_path, 1, 4721, volatile=True)


def test_cold_start_first_answer(tmp_path):
    result = cold(tmp_path, 2)
    assert result['n'] == 2 and result['p50_us'] > 0
