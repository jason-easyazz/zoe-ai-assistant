"""[lab] Offline real-adapter tests: children execute only FakeInstrument."""
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime

import pytest
import loop as sl
import replay as rp


class OpenWindows:
    def remaining(self, now): return 3600


class QuietGuards:
    windows = OpenWindows()
    clock = sl.Clock()
    def require(self): pass


@pytest.fixture
def adapter(tmp_path):
    return rp.ReplayBackend(QuietGuards(), fake=True, lock=tmp_path / 'lock')


def clips(n=8):
    return [(str(i), Path('synthetic.wav')) for i in range(n)]


def experiment(adapter, candidate, reuse=False, early=True):
    with adapter.experiment(20) as deadline:
        a, b, reason = rp.paired(adapter, {}, candidate, clips(), deadline,
                                 reuse=reuse, early=early)
    return reason or sl.verdict(rp.measurements(a), rp.measurements(b))[0], a, b


def test_child_env_interleaving_and_privacy(adapter, tmp_path):
    before = os.environ.get('ZOE_FLUE_PREPOST_CONCURRENT')
    decision, a, b = experiment(adapter, {'ZOE_FLUE_PREPOST_CONCURRENT': True})
    assert decision == 'KEEP' and adapter.calls == 32
    assert os.environ.get('ZOE_FLUE_PREPOST_CONCURRENT') == before
    path = tmp_path / 'ledger.jsonl'
    path.write_text(json.dumps(dict(before=a, after=b)))
    assert all('PLANTED_SYNTHETIC_PRIVATE_TEXT' not in p.read_text()
               for p in tmp_path.iterdir() if p.is_file())
    assert a[0]['e2e_ms'] - b[0]['e2e_ms'] == 30


def test_lock_exclusive_and_release(adapter):
    with adapter.experiment(1):
        with adapter.lock.open('a') as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with adapter.experiment(1): pass


def test_timeout_kills_and_unlocks(adapter, monkeypatch, tmp_path):
    monkeypatch.setenv('SPEED_LOOP_FAKE_SLEEP', '1')
    pidfile = tmp_path / 'descendant.pid'
    monkeypatch.setenv('SPEED_LOOP_FAKE_PID_FILE', str(pidfile))
    start = time.monotonic()
    with pytest.raises(sl.BudgetExpired):
        with adapter.experiment(.5) as deadline:
            adapter.measure({}, 0, Path('synthetic.wav'), deadline)
    assert time.monotonic() - start < 2
    assert pidfile.exists()
    status = Path('/proc') / pidfile.read_text() / 'stat'
    assert not status.exists() or status.read_text().split()[2] == 'Z'
    with adapter.experiment(1): pass


def test_new_activity_cancels(adapter):
    class Closing(QuietGuards):
        calls = 0
        def require(self):
            self.calls += 1
            if self.calls > 3: raise PermissionError('active')
    adapter.guards = Closing()
    with pytest.raises(PermissionError):
        with adapter.experiment(5) as deadline:
            adapter.measure({}, 0, Path('synthetic.wav'), deadline)
    adapter.guards = QuietGuards()
    with adapter.experiment(1): pass


@pytest.mark.parametrize('reuse', [False, True])
@pytest.mark.parametrize('early', [False, True])
def test_optimisations_preserve_keeps(adapter, reuse, early):
    if reuse:
        experiment(adapter, {})
    winner = experiment(adapter, {'ZOE_FLUE_PREPOST_CONCURRENT': True}, reuse, early)[0]
    loser = experiment(adapter, {'ZOE_BRAIN_VOLATILE_AFTER_USER': True}, reuse, early)[0]
    assert winner == 'KEEP' and loser in {'DISCARD', 'losing'}


def test_order_and_stage_filter_preserve_eligible_winners():
    surface = sl.load_surface()
    all_ideas = [(k, v) for k, v in sl.candidates(surface, 7) if k in rp.AUTO]
    ordered = rp.ordered(surface, {'brain', 'prepost', 'memory'})
    assert set(all_ideas) == set(ordered)
    assert not rp.ordered(surface, set())
    assert all(surface[k]['stage'] == 'memory' for k, _ in rp.ordered(surface, {'memory'}))


def test_calibration_noise_and_false_keep(adapter):
    cal = rp.calibrate(adapter, {}, clips(), 20, 'a'*64, repeats=2)
    assert cal['false_keep_rate'] == 0 and cal['sigma_ms'] == 0
    assert cal['turns_15'] == cal['turns_30'] == 7
    assert rp.sample_size(100, 15) == 701
    assert rp.sample_size(100, 30) == 176


@pytest.mark.parametrize('bad', [float('nan'), -1, 'private text', True])
def test_parser_negative_controls(bad):
    row = dict(turn=0, memory_recall='ok', stt_ms=bad, resolve_ms=0,
               brain_ms=0, e2e_ms=0, verdict='OK', route={'lane':'brain'}, heard_hash='aa')
    with pytest.raises(ValueError): rp.clean(row, 0)


def test_timestamp_logger_only(tmp_path):
    log = tmp_path / 'activity'
    log.write_text('2026-11-16 09:00:00 INFO routers.voice_tts PLANTED_SYNTHETIC_PRIVATE_TEXT\n')
    now = datetime.fromisoformat('2026-11-16T10:00:00').astimezone()
    assert rp.conversation_age(log, now) == 3600
    log.write_text('2026-11-16 09:59:00 INFO unrelated.logger anything\n')
    with pytest.raises(ValueError): rp.conversation_age(log, now)


def test_guards_walk_every_check(tmp_path, monkeypatch):
    log = tmp_path / 'log'
    log.write_text('2026-11-16 09:00:00 INFO routers.voice_tts synthetic\n')
    class Result: returncode = 1
    monkeypatch.setattr(rp.subprocess, 'run', lambda *a, **kw: Result())
    clock = sl.FakeClock(datetime.fromisoformat('2026-11-16T10:00:00+00:00'))
    guards = rp.Guards(OpenWindows(), log, min_mem=0, clock=clock)
    checks = guards.inspect()
    assert set(checks) == {'window', 'night', 'memory', 'idle'}
    assert all(checks.values())
    Result.returncode = 0
    assert guards.inspect()['night'] is False
    Result.returncode = 2
    assert guards.inspect()['night'] is False


def test_cache_really_reduces_children(adapter):
    experiment(adapter, {}, early=False)
    first = adapter.calls
    experiment(adapter, {'ZOE_FLUE_PREPOST_CONCURRENT': True}, reuse=True)
    assert adapter.calls - first == 24


def test_surface_classification():
    surface = sl.load_surface()
    classes = {c: sum(rp.classification(k) == c for k in surface.values()) for c in 'abc'}
    assert classes == {'a':8, 'b':5, 'c':3}


def test_real_log_prefix_offset(tmp_path):
    log = tmp_path / 'activity'
    log.write_text('2026-11-16T09:00:00+0000 INFO     routers.voice_tts: synthetic\n')
    now = datetime.fromisoformat('2026-11-16T10:00:00+00:00')
    assert rp.conversation_age(log, now) == 3600


def test_end_to_end_session_fake_only(tmp_path, monkeypatch):
    import argparse
    surface = sl.load_surface()
    monkeypatch.setattr(rp, 'HERE', tmp_path)
    monkeypatch.setattr(rp, 'Guards', lambda *a, **kw: QuietGuards())
    monkeypatch.setattr(rp, 'select_corpus', lambda *a: clips())
    monkeypatch.setattr(rp, 'corpus_default', lambda: tmp_path)
    monkeypatch.setattr(rp, 'revision', lambda: 'a'*64)
    monkeypatch.setattr(rp.sl, 'Windows', lambda: OpenWindows())
    real = rp.ReplayBackend
    # HERE must still locate the fake script; only artifacts relocate to tmp_path.
    class Fake(real):
        def __init__(self, guards, **kw):
            super().__init__(guards, fake=True, lock=tmp_path/'voice.lock')
        def measure(self, *a, **kw):
            with monkeypatch.context() as child_patch:
                child_patch.setattr(rp, 'HERE', Path(__file__).parent)
                return super().measure(*a, **kw)
    monkeypatch.setattr(rp, 'ReplayBackend', Fake)
    monkeypatch.setattr(rp, 'ordered', lambda *a: [('ZOE_FLUE_PREPOST_CONCURRENT', True)])
    args = argparse.Namespace(activity_log=tmp_path/'log', min_mem=1500, idle_minutes=10,
        python=sys.executable, service_dir=tmp_path, env_source=None, dry_run=False,
        output=tmp_path/'runs/window', turns=8, seed=7, calibrate=False, cap=20,
        max_experiments=1, refresh_every=3)
    rp.session(args)
    out = args.output
    ledger = json.loads((out/'replay-ledger.jsonl').read_text())
    assert ledger['verdict'] == 'KEEP'
    assert (out/'morning.md').exists()
    assert (out/'calibration.json').exists()
    assert list(out.glob('*.patch')) and list(out.glob('*.pr.md'))
    assert all('PLANTED_SYNTHETIC_PRIVATE_TEXT' not in p.read_text()
               for p in out.iterdir() if p.is_file())
    original_ledger = (out/'replay-ledger.jsonl').read_bytes()
    monkeypatch.setattr(rp, 'revision', lambda: 'b'*64)
    def unavailable(*a, **kw): raise ValueError('calibration_required')
    monkeypatch.setattr(rp, 'calibrate', unavailable)
    with pytest.raises(ValueError, match='calibration_required'):
        rp.session(args)
    assert (out/'replay-ledger.jsonl').read_bytes() == original_ledger


def test_proposal_is_applyable_data_only(tmp_path):
    surface = sl.load_surface()
    source = rp.ROOT / 'services/zoe-data/flag_registry.py'
    before = source.read_bytes()
    rp.proposal(tmp_path, 'ZOE_FLUE_PREPOST_CONCURRENT', True, surface,
                dict(delta_ms=-30, p=.001))
    patch = next(tmp_path.glob('*.patch')).read_text()
    assert "_f('ZOE_FLUE_PREPOST_CONCURRENT', 'bool', True" in patch
    assert source.read_bytes() == before
    assert next(tmp_path.glob('*.pr.md')).exists()


def test_interleaving_is_abab(adapter, monkeypatch):
    seen = []
    real = adapter.measure
    def record(config, turn, clip, deadline):
        seen.append((turn, bool(config.get('ZOE_FLUE_PREPOST_CONCURRENT'))))
        return real(config, turn, clip, deadline)
    monkeypatch.setattr(adapter, 'measure', record)
    experiment(adapter, {'ZOE_FLUE_PREPOST_CONCURRENT': True})
    assert seen == [(i, arm) for i in range(8) for arm in (False, True, False, True)]




def test_memory_wrapper_with_synthetic_pipeline(tmp_path):
    service = tmp_path / 'service'
    (service/'routers').mkdir(parents=True)
    (service/'tests').mkdir()
    (service/'routers/__init__.py').write_text('')
    (service/'routers/voice_tts.py').write_text(
        'async def _transcribe_audio_impl(path):\n    return "synthetic"\n')
    (service/'tests/replay_samples.py').write_text('''
import json
async def _run(args):
    with open(args.files_from) as stream:
        assert stream.read().strip() == 'synthetic.wav'
    row = dict(stt_ms=100, resolve_ms=20, brain_ms=500, verdict='OK',
               route={'lane':'brain'}, transcript='PLANTED_SYNTHETIC_PRIVATE_TEXT')
    with open(args.json, 'w') as stream:
        json.dump(dict(rows=[row], memory_recall='ok'), stream)
    print('PLANTED_SYNTHETIC_PRIVATE_TEXT')
    return 0
''')
    backend = rp.ReplayBackend(QuietGuards(), service_dir=service, lock=tmp_path/'lock')
    with backend.experiment(5) as deadline:
        row = backend.measure({}, 0, Path('synthetic.wav'), deadline)
    output = tmp_path/'results.json'
    output.write_text(json.dumps(row))
    assert row['e2e_ms'] == 620
    assert 'PLANTED_SYNTHETIC_PRIVATE_TEXT' not in output.read_text()
    assert not list(service.rglob('__speed_loop*'))


def test_ledger_rejects_free_text(tmp_path):
    path = tmp_path/'ledger.jsonl'
    path.write_text(json.dumps(dict(key='a'*64, knob='PLANTED_SYNTHETIC_PRIVATE_TEXT')))
    with pytest.raises(ValueError):
        rp.read_ledger(path, 'a'*64, sl.load_surface())
