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
