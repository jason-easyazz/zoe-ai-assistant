"""[lab] Synthetic only; hand-run lab net, intentionally not marked ci_safe."""
import ast
import inspect
import json
from datetime import datetime
from pathlib import Path

import pytest
import loop as sl

ROOT = Path(__file__).resolve().parents[2]


def _utc_windows() -> "Path":
    """The committed windows with the zone pinned to UTC, so these tests do not depend on the machine's own zone."""
    import json as _json, tempfile as _tempfile
    from pathlib import Path as _Path
    data = _json.loads((_Path(sl.__file__).parent / 'windows.json').read_text())
    data['timezone'] = 'UTC'
    out = _Path(_tempfile.mkdtemp(prefix='speed-loop-test-')) / 'windows.json'
    out.write_text(_json.dumps(data))
    return out


UTC_WINDOWS = _utc_windows()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    surface = sl.load_surface()
    clock = sl.FakeClock(datetime.fromisoformat('2026-11-16T10:00:00+00:00'))
    monkeypatch.setattr(sl, 'HERE', tmp_path)
    return surface, clock, sl.Windows(UTC_WINDOWS), tmp_path


def execute(setup, **kwargs):
    surface, clock, windows, output = setup
    backend = sl.FakeBackend(surface, clock)
    rows = sl.run(surface, backend, clock, windows, output, **kwargs)
    return rows, backend


@pytest.mark.parametrize('index,decision,reason', [
    (0, 'KEEP', 'improved'), (1, 'DISCARD', 'below_threshold'),
    (2, 'DISCARD', 'below_threshold'), (3, 'DISCARD', 'outcome_changed')])
def test_effect_classes(setup, monkeypatch, index, decision, reason):
    surface = setup[0]
    name = list(surface)[index]
    value = next(v for v in surface[name]['values'] if v != surface[name]['default'])
    monkeypatch.setattr(sl, 'candidates', lambda *_: [(name, value)])
    rows, _ = execute(setup)
    assert (rows[0]['verdict'], rows[0]['reason']) == (decision, reason)
    assert rows[0]['n'] == 64
    assert len(list(setup[3].glob('*.patch.json'))) == (decision == 'KEEP')


def test_real_references_and_defaults():
    tree = ast.parse((ROOT / 'services/zoe-data/flag_registry.py').read_text())
    registry = {node.args[0].value: ast.literal_eval(node.args[2])
                for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == '_f'
                and len(node.args) >= 3}
    surface = sl.load_surface()
    assert len(surface) >= 15
    for name, knob in surface.items():
        text = (ROOT / knob['file']).read_text().splitlines()
        assert name in text[knob['line'] - 1]
        if knob['kind'] == 'env':
            assert registry[name] == knob['default']
        else:
            assignment = next(n for n in ast.walk(ast.parse('\n'.join(text)))
                              if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
            assert ast.literal_eval(assignment.value) == knob['default']


@pytest.mark.parametrize('target', ['_CLEAN_TAIL_FLOOR_MS', 'forget_all', 'erase_user',
                                  'auth_check', 'MODEL_PATH', 'safety_guard'])
def test_denylist(setup, target):
    data = json.loads((Path(__file__).parent / 'surface.json').read_text())
    data['knobs'][0]['name'] = target
    path = setup[3] / 'bad.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='forbidden'):
        sl.load_surface(path)


def test_target_redirection_and_floor(setup):
    data = json.loads((Path(__file__).parent / 'surface.json').read_text())
    path = setup[3] / 'bad.json'
    data['knobs'][0]['file'] = 'services/zoe-data/auth.py'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        sl.load_surface(path)
    data['knobs'][0]['file'] = sl.FLUE
    next(k for k in data['knobs'] if k['name'] == 'ZOE_VAD_CLEAN_TAIL_MS')['values'].append(499)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='floor'):
        sl.load_surface(path)


def test_tail_and_sign_negative_controls():
    before = [sl.Measurement(i, 1000, 0) for i in range(64)]
    after = [sl.Measurement(i, 980 if i < 54 else 1100, 0) for i in range(64)]
    assert sl.verdict(before, after)[1] == 'tail_regressed'
    after = [sl.Measurement(i, 980 if i < 33 else 1001, 0) for i in range(64)]
    assert sl.verdict(before, after)[1] == 'uncertain'
    assert sl.verdict(before, before)[1] == 'below_threshold'


def test_outcome_guard_mutation_control():
    before = [sl.Measurement(i, 1000, 0) for i in range(64)]
    after = [sl.Measurement(i, 980, int(i == 0)) for i in range(64)]
    namespace = dict(sl.__dict__)
    source = inspect.getsource(sl.verdict).replace(
        'if any(a.outcome != b.outcome for a, b in zip(after, before)):', 'if False:')
    exec(source, namespace)
    assert sl.verdict(before, after)[0] == 'DISCARD'
    assert namespace['verdict'](before, after)[0] == 'KEEP'


@pytest.mark.parametrize('start,allowed', [('2026-11-16T10:00:00+00:00', True),
    ('2026-11-16T09:59:59+00:00', False), ('2026-11-16T14:00:00+00:00', False),
    ('2026-11-21T11:00:00+00:00', False), ('2026-11-16T02:30:00+00:00', False),
    ('2026-11-16T04:30:00+00:00', False), ('2026-11-16T05:00:00-05:00', True)])
def test_windows(start, allowed):
    assert bool(sl.Windows(UTC_WINDOWS).remaining(datetime.fromisoformat(start))) == allowed


@pytest.mark.parametrize('a,b', [('01:54', '02:00'), ('03:29', '03:31'),
                                ('04:00', '04:55'), ('23:00', '01:00')])
def test_refuse_unsafe_windows(setup, a, b):
    path = setup[3] / 'windows.json'
    path.write_text(json.dumps(dict(timezone='UTC', weekdays={'0': [[a, b]]})))
    with pytest.raises(ValueError):
        sl.Windows(path)


def test_outside_refuses(setup):
    setup[1].advance(4 * 3600)
    with pytest.raises(ValueError, match='outside'):
        execute(setup)
    assert not (setup[3] / 'ledger.jsonl').exists()


@pytest.mark.parametrize('limit', ['cap', 'seconds', 'window'])
def test_abandon_before_deadline(setup, limit):
    kwargs = {limit: 0.505} if limit != 'window' else {}
    if limit == 'window':
        setup[1].advance(4 * 3600 - 0.505)
    start = setup[1].monotonic()
    rows, backend = execute(setup, **kwargs)
    assert rows[0]['verdict'] == 'ABANDON'
    assert rows[0]['n'] < 64
    assert backend.calls <= 50
    assert setup[1].monotonic() - start < 0.505
    assert not list(setup[3].glob('*.patch.json'))


def test_budget_and_numeric_ledger(setup):
    rows, backend = execute(setup, max_experiments=2, turns=16)
    assert len(rows) == 2 and backend.calls == 2 * 2 * 16
    assert sl.load_ledger(setup[3] / 'ledger.jsonl', setup[0]) == rows
    assert all(type(v) in (int, float, bool, type(None))
               for r in rows for k, v in r.items() if k not in ('knob', 'verdict', 'reason'))
    rows[0]['utterance'] = 'synthetic forbidden text'
    (setup[3] / 'ledger.jsonl').write_text(json.dumps(rows[0]) + '\n')
    with pytest.raises(ValueError):
        sl.load_ledger(setup[3] / 'ledger.jsonl', setup[0])


def test_hundred_reproducible_and_resume(setup):
    surface, clock, windows, output = setup
    rows, backend = execute(setup, max_experiments=100)
    assert len(rows) == 100
    assert len({(r['knob'], r['value']) for r in rows}) == 100
    report = sl.tally(rows, backend)
    assert report['false_keeps'] == 0 and report['real_wins_found'] == 4
    clock2 = sl.FakeClock(clock.start)
    rows2 = sl.run(surface, sl.FakeBackend(surface, clock2), clock2, windows, output / 'again')
    assert rows == rows2
    more, _ = execute(setup, max_experiments=1)
    assert (more[0]['knob'], more[0]['value']) not in {(r['knob'], r['value']) for r in rows}
    print('\n[lab] 100-experiment tally:', json.dumps(report, sort_keys=True))
    # Retain only aggregate evidence in the worktree, no turn payloads.
    (Path(__file__).parent / 'fake-tally.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')


def test_replay_never_starts():
    with pytest.raises(PermissionError):
        sl.ReplayBackend()
    with pytest.raises(NotImplementedError):
        sl.ReplayBackend(True)


def test_resume_refuses_changed_budget(setup):
    execute(setup, max_experiments=1)
    with pytest.raises(ValueError, match='fixed budget changed'):
        execute(setup, turns=32)
