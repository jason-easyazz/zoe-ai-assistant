"""Harness quiet hours (P-2, ``scripts/maintenance/harness_quiet.py``): the bar, day-sim, person bench and the
voice-regression probe stay off the live brain 07:00-22:00 unless forced; the landing loop's probe is exempt
from the hours but yields to the panel. Negative controls: the guard BLOCKS a probe during waking hours, and
every named harness actually calls it. No network, no ssh (the panel check is injected)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
# by FILE PATH, never sys.path: scripts/maintenance/pi_runtime_probe.py would shadow the service module of that
# name for every later test in the session (it did: test_brain_dispatch broke)


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


hq = _load("harness_quiet", ROOT / "scripts" / "maintenance" / "harness_quiet.py")

NOON = dt.datetime(2026, 10, 19, 12, 0)
NIGHT = dt.datetime(2026, 10, 19, 23, 30)
QUIET = lambda: True   # noqa: E731
BUSY = lambda: False   # noqa: E731


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("ZOE_HARNESS_FORCE", "ZOE_HARNESS_LANDING", "ZOE_HARNESS_QUIET_START", "ZOE_HARNESS_QUIET_END",
              "ZOE_HARNESS_YIELD_TO_PANEL", "ZOE_HARNESS_PANEL_WAIT_S"):
        monkeypatch.delenv(k, raising=False)


def test_waking_hours_block_a_probe():
    # NEGATIVE CONTROL: the whole point - a probe at noon is refused even with the panel quiet
    v = hq.check("voice_regression_probe", now=NOON, panel=QUIET)
    assert not v.allowed and v.kind == "hours" and "07:00-22:00" in v.reason


@pytest.mark.parametrize("hour,allowed", [(6, True), (7, False), (21, False), (22, True), (3, True)])
def test_window_edges(hour, allowed):
    assert hq.check("samantha_bar", now=NOON.replace(hour=hour, minute=59 if hour == 6 else 0),
                    panel=QUIET).allowed is allowed


def test_outside_hours_still_yields_to_the_panel():
    assert hq.check("samantha_bar", now=NIGHT, panel=QUIET).allowed
    v = hq.check("samantha_bar", now=NIGHT, panel=BUSY)
    assert not v.allowed and v.kind == "panel"


def test_landing_probe_is_exempt_from_the_hours_but_not_the_panel(monkeypatch):
    assert hq.check("voice_regression_probe", landing=True, now=NOON, panel=QUIET).allowed
    assert not hq.check("voice_regression_probe", landing=True, now=NOON, panel=BUSY).allowed
    monkeypatch.setenv("ZOE_HARNESS_LANDING", "1")
    assert hq.check("voice_regression_probe", now=NOON, panel=QUIET).allowed


def test_force_skips_both_and_says_so(monkeypatch):
    v = hq.check("samantha_bar", force=True, now=NOON, panel=BUSY)
    assert v.allowed and v.kind == "forced" and "FORCED" in v.reason
    monkeypatch.setenv("ZOE_HARNESS_FORCE", "1")
    assert hq.check("samantha_bar", now=NOON, panel=BUSY).allowed


def test_window_is_configurable_wraps_and_fails_closed(monkeypatch):
    monkeypatch.setenv("ZOE_HARNESS_QUIET_START", "20:00")
    monkeypatch.setenv("ZOE_HARNESS_QUIET_END", "06:00")
    assert hq.in_waking_hours(NIGHT) and not hq.in_waking_hours(NOON)
    monkeypatch.setenv("ZOE_HARNESS_QUIET_START", "off")
    assert not hq.in_waking_hours(NOON)
    monkeypatch.setenv("ZOE_HARNESS_QUIET_START", "garbage")   # a typo falls back to the default, not open
    assert hq.in_waking_hours(NOON)


def test_panel_check_parses_the_pi_log_and_fails_open():
    class R:
        def __init__(self, out):
            self.stdout = out
    seen = lambda: dt.datetime(2026, 10, 19, 11, 55)  # noqa: E731
    assert hq.panel_quiet(NOON, last=seen) is False                       # 5 min ago: in use
    assert hq.panel_quiet(NOON, last=lambda: dt.datetime(2026, 10, 19, 11, 49)) is True
    assert hq.panel_quiet(NOON, last=lambda: None) is True               # unknown = quiet (as panel_quiet)
    assert hq.last_panel_activity(lambda *a, **k: R("2026-10-16 07:20:04")) == dt.datetime(2026, 10, 16, 7, 20, 4)
    assert hq.last_panel_activity(lambda *a, **k: R("")) is None
    assert hq.last_panel_activity(lambda *a, **k: (_ for _ in ()).throw(OSError("no ssh"))) is None


def test_wait_for_panel_quiet_is_bounded():
    slept = []
    states = iter([False, False, True])
    assert hq.wait_for_panel_quiet(600, poll_s=60, panel=lambda: next(states), sleep=slept.append, log=lambda m: None)
    assert slept == [60, 60]
    slept.clear()
    assert not hq.wait_for_panel_quiet(120, poll_s=60, panel=BUSY, sleep=slept.append, log=lambda m: None)
    assert slept == [60, 60]


def test_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(hq, "panel_quiet", lambda now=None, **k: True)
    monkeypatch.setattr(hq, "in_waking_hours", lambda now=None: True)
    assert hq.main(["check", "samantha_bar"]) == hq.EXIT_REFUSED
    assert hq.main(["check", "samantha_bar", "--landing"]) == 0
    assert hq.main(["check", "samantha_bar", "--force"]) == 0
    assert hq.main(["panel-quiet"]) == 0


# ── the voice probe: refuses before its lock and before any artifact write ──────────────────────────────────


def _probe():
    return _load("voice_regression_probe", ROOT / "scripts" / "maintenance" / "voice_regression_probe.py")


def test_probe_refuses_during_waking_hours_without_touching_the_artifact():
    vrp = _probe()
    refused = vrp._harness_quiet_refusal(check=lambda name: hq.Verdict(False, "hours", "hours"),
                                         wait=lambda s: pytest.fail("hours must not wait"))
    assert refused == "hours"
    assert os.environ.get("ZOE_HARNESS_YIELD_TO_PANEL") is None
    src = (ROOT / "scripts" / "maintenance" / "voice_regression_probe.py").read_text()
    main = src[src.index("def main("):]
    guard, lock = main.index("_harness_quiet_refusal()"), main.index("_acquire_harness_lock()")
    assert guard < lock
    refusal_branch = main[guard:main.index("return 3", guard)]
    assert "emit_result(" not in refusal_branch and "write" not in refusal_branch   # a refusal writes no artifact
    # every artifact write on the run path comes after the guard (the only earlier one is the interpreter-error exit)
    assert main.count("emit_result(", 0, guard) == 1 and "interpreter:" in main[:guard]


def test_probe_waits_out_a_busy_panel_then_tells_the_replay_to_yield(monkeypatch):
    vrp = _probe()
    verdicts = iter([hq.Verdict(False, "panel", "panel"), hq.Verdict(True, "ok", "ok")])
    waited = []
    assert vrp._harness_quiet_refusal(check=lambda name: next(verdicts),
                                      wait=lambda s: waited.append(s) or True) is None
    assert waited == [2700.0]
    assert os.environ.pop("ZOE_HARNESS_YIELD_TO_PANEL") == "1"


# ── every named harness calls the guard (static: a refactor that drops it must fail here) ──────────────────


@pytest.mark.parametrize("path,needle", [
    ("scripts/perf/samantha_bar.py", 'harness_quiet_refusal("samantha_bar")'),
    ("scripts/perf/user_model_ab.py", "sb.harness_quiet_refusal()"),          # _gates: day-sim, person bench, A/B
    ("scripts/perf/recall_evidence_probe.py", 'bar.harness_quiet_refusal("recall_evidence_probe")'),
    ("scripts/maintenance/voice_regression_probe.py", "_harness_quiet_refusal()"),
    ("services/zoe-data/tests/replay_samples.py", "_yield_to_panel()"),
])
def test_harnesses_call_the_guard(path, needle):
    assert needle in (ROOT / path).read_text()


@pytest.mark.parametrize("path", ["scripts/perf/samantha_day_sim.py", "scripts/perf/samantha_person.py"])
def test_day_sim_and_person_bench_gate_through_user_model_ab(path):
    assert "uma._gates(args, live, log)" in (ROOT / path).read_text()


def test_the_shared_harness_gate_really_refuses_at_noon(monkeypatch):
    """Behavioural, not a source string: user_model_ab._gates (day-sim, person bench, the A/B) returns the quiet-hours
    refusal BEFORE it touches the deploy check, the token or the live server."""
    uma = _load("user_model_ab", ROOT / "scripts" / "perf" / "user_model_ab.py")
    sb = uma.sb
    monkeypatch.setattr(sb, "in_nightly_window", lambda now: False)
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: pytest.fail("the gate went past quiet hours"))
    monkeypatch.setattr(hq, "in_waking_hours", lambda now=None: True)
    monkeypatch.setitem(sys.modules, "harness_quiet", hq)
    reason = uma._gates(None, None, print)
    assert reason and "quiet hours" in reason
    monkeypatch.setenv("ZOE_HARNESS_FORCE", "1")              # the deliberate override reaches the next gate
    with pytest.raises(pytest.fail.Exception, match="went past quiet hours"):
        uma._gates(None, None, print)


# ---- the brain window (01:50-04:55), owned by the multi-step orchestrator record_bench_baselines.sh -------------------

def _at(hh, mm=0, day=19):
    return dt.datetime(2026, 10, day, hh, mm)


@pytest.mark.parametrize("when,ok", [
    (_at(22, 0), True), (_at(23, 30), True), (_at(1, 5), True),        # 01:05 + 45 min = 01:50 exactly: finishes in time
    (_at(1, 6), False), (_at(1, 30), False),                             # cannot finish before 01:50
    (_at(1, 50), False), (_at(2, 51), False), (_at(4, 0), False),
    (_at(4, 45), False),                                                 # the documented-wrong start: inside the 04:10-04:52 voice gate
    (_at(4, 54), False), (_at(4, 55), True), (_at(5, 0), True), (_at(6, 45), True),
])
def test_brain_window_blocks_inside_and_late_starts(when, ok):
    v = hq.brain_window_verdict("record_bench_baselines", now=when)
    assert v.allowed is ok, v.reason
    assert ok or v.kind == "brain-window"


def test_brain_window_budget_is_respected_and_force_does_not_bypass(monkeypatch):
    assert hq.brain_window_verdict("x", budget_min=20, now=_at(1, 30)).allowed        # a 20-min step fits before 01:50
    assert not hq.brain_window_verdict("x", budget_min=21, now=_at(1, 30)).allowed
    monkeypatch.setenv("ZOE_HARNESS_FORCE", "1")                                       # force skips quiet hours, NOT this
    assert not hq.brain_window_verdict("x", now=_at(4, 45)).allowed


def test_brain_window_cli_exit_codes(capsys):
    assert hq.main(["brain-window", "x", "--now", "2026-10-19T04:45:00"]) == hq.EXIT_REFUSED
    assert "brain window" in capsys.readouterr().out
    assert hq.main(["brain-window", "x", "--now", "2026-10-19T05:00:00"]) == 0


def _stub_tree(tmp_path, refuse_after):
    """A copy of the orchestrator over stub scripts: harness_quiet.py ``check`` allows, ``brain-window`` allows its first
    ``refuse_after`` calls then refuses (exit 3); every harness step just drops a marker file."""
    import shutil
    m = tmp_path / "scripts" / "maintenance"
    (tmp_path / "scripts" / "perf").mkdir(parents=True)
    m.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "maintenance" / "record_bench_baselines.sh", m / "record_bench_baselines.sh")
    (m / "harness_quiet.py").write_text(
        "import sys, pathlib\n"
        f"n = pathlib.Path({str(tmp_path / 'calls')!r})\n"
        "if sys.argv[1] == 'brain-window-wait':\n"
        "    print(7)\n"
        "    sys.exit(0)\n"
        "if sys.argv[1] == 'brain-window':\n"
        "    c = int(n.read_text()) if n.exists() else 0\n"
        "    n.write_text(str(c + 1))\n"
        f"    sys.exit(0 if c < {refuse_after} else 3)\n"
        "sys.exit(0)\n")
    for rel in ("maintenance/voice_regression_probe.py", "perf/samantha_bar.py", "perf/samantha_day_sim.py",
                "perf/samantha_person.py"):
        (tmp_path / "scripts" / rel).write_text(
            "import sys, pathlib\n"
            f"pathlib.Path({str(tmp_path)!r}, 'ran-' + pathlib.Path(sys.argv[0]).stem).write_text('x')\n")
    return m / "record_bench_baselines.sh"


def _run_orchestrator(tmp_path, refuse_after):
    import subprocess
    sh = _stub_tree(tmp_path, refuse_after)
    env = {**os.environ, "HOME": str(tmp_path), "ZOE_HARNESS_LOCK": str(tmp_path / "lock")}
    r = subprocess.run(["bash", str(sh)], env=env, capture_output=True, text=True, timeout=60)
    return r, sorted(p.name for p in tmp_path.glob("ran-*"))


def test_orchestrator_refuses_up_front_in_the_brain_window_and_runs_nothing(tmp_path):
    r, ran = _run_orchestrator(tmp_path, refuse_after=0)
    assert r.returncode == 3 and ran == [], (r.stdout, r.stderr)


def test_orchestrator_rechecks_inside_the_lock_before_each_step(tmp_path):
    # up-front check passes, the first step's in-lock check passes, the rest are refused: one step ran, three did not
    r, ran = _run_orchestrator(tmp_path, refuse_after=2)
    assert r.returncode == 1 and ran == ["ran-voice_regression_probe"], (ran, r.stdout)


def test_orchestrator_positive_control_runs_every_step_when_clear(tmp_path):
    r, ran = _run_orchestrator(tmp_path, refuse_after=99)
    assert r.returncode == 0 and len(ran) == 4, (ran, r.stdout, r.stderr)


@pytest.mark.parametrize("when,secs", [
    (_at(1, 20), 600),        # 30 min to the window - the 20-min step budget = 10 min, not the 30-min cap
    (_at(1, 30), 0),          # the step cannot finish in time: never queue
    (_at(1, 0), 1800),        # plenty of time: the cap
    (_at(2, 51), 0),          # inside the window
    (_at(4, 55), 1800), (_at(23, 0), 1800),
])
def test_lock_wait_cannot_cross_the_brain_window(when, secs):
    assert hq.lock_wait_s(20, now=when) == secs


def test_orchestrator_bounds_each_lock_wait_by_the_brain_window(tmp_path):
    """Codex P1: the in-lock re-check runs only AFTER ``flock -w`` returns, so a start just before the cutoff could queue up
    to 30 min inside the protected window. Every step must pass the brain-window-wait value (the stub says 7) to flock -w."""
    import subprocess
    sh = _stub_tree(tmp_path, refuse_after=99)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real = subprocess.run(["which", "flock"], capture_output=True, text=True).stdout.strip()
    stub = bindir / "flock"
    stub.write_text(f'#!/bin/bash\necho "$2" >> {tmp_path}/flock-waits\nexec {real} "$@"\n')
    stub.chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "ZOE_HARNESS_LOCK": str(tmp_path / "lock"),
           "PATH": f"{bindir}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", str(sh)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (tmp_path / "flock-waits").read_text().split() == ["7"] * 4
