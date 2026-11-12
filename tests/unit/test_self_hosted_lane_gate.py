"""The self-hosted nightly's pytest steps must take the shared test lane and keep out of the night (2026-11-12: a cron GitHub started 6.5 h late ran the full
zoe-data suite on the Jetson WITHOUT /tmp/zoe-test-lane.lock, overlapped a landing's voice probe and failed it on speed)."""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "self-hosted-tests.yml"
GATE = ROOT / "scripts" / "maintenance" / "self_hosted_lane_gate.sh"
PYTEST_LINE = re.compile(r"pytest_junit_guard\.py|-m pytest")


def _pytest_steps():
    steps = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["host-tests"]["steps"]
    return [s for s in steps if PYTEST_LINE.search(str(s.get("run", "")))]


def test_every_pytest_step_takes_the_gate_before_it_runs_pytest():
    steps = _pytest_steps()
    assert len(steps) >= 4
    for s in steps:
        lines = [ln.strip() for ln in str(s["run"]).splitlines() if ln.strip() and not ln.strip().startswith("#")]
        gate = next((i for i, ln in enumerate(lines) if ln.startswith("source scripts/maintenance/self_hosted_lane_gate.sh")), None)
        first = next((i for i, ln in enumerate(lines) if PYTEST_LINE.search(ln)), None)
        assert gate is not None, f"{s.get('id')}: pytest runs without the lane gate"
        assert gate < first, f"{s.get('id')}: the gate must come BEFORE pytest"
    assert GATE.exists() and os.access(GATE, os.R_OK)


def _run(tmp_path, hour, hold=False):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "voice-day-ok").write_text("always\n")
    lock = tmp_path / "lane.lock"
    summary = tmp_path / "summary.md"
    summary.write_text("")
    now = dt.datetime(2026, 11, 12, hour, 0).timestamp()
    env = {**os.environ, "ZOE_LANDING_HOME": str(home), "ZOE_TEST_LANE_LOCK": str(lock), "LANE_WAIT_S": "1",
           "ZOE_LANDING_NOW_EPOCH": str(int(now)), "GITHUB_STEP_SUMMARY": str(summary)}
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    try:
        if hold:
            fcntl.flock(fd, fcntl.LOCK_EX)
        r = subprocess.run(["bash", "-e", "-c", f'source "{GATE}" unit-full; echo AFTER_GATE'], env=env, capture_output=True, text=True, timeout=60)
    finally:
        os.close(fd)
    return r, summary.read_text()


def test_the_gate_lets_a_free_lane_by_day_through_holding_the_lock(tmp_path):
    r, summary = _run(tmp_path, 12)
    assert r.returncode == 0 and "AFTER_GATE" in r.stdout and "SKIPPED" not in summary, (r.stdout, r.stderr)


def test_the_gate_skips_the_night_window_a_held_lane_and_never_runs_the_step_body(tmp_path):
    for hour in (2, 3, 4):                             # NEGATIVE CONTROLS: each must skip (exit 0, visibly), not fall through to pytest
        (tmp_path / f"h{hour}").mkdir()
        r, summary = _run(tmp_path / f"h{hour}", hour)
        assert r.returncode == 0 and "AFTER_GATE" not in r.stdout and "SKIPPED unit-full" in summary and "::warning::" in r.stdout, hour
    (tmp_path / "held").mkdir()
    r, summary = _run(tmp_path / "held", 12, hold=True)
    assert r.returncode == 0 and "AFTER_GATE" not in r.stdout and "SKIPPED unit-full" in summary and "stayed held" in summary


def test_the_gate_is_rechecked_after_the_lane_wait_and_releases_the_lock(tmp_path):
    """Clear when the wait began, busy by the time the lock is won (the daytime voice pause comes back mid-wait): skip AND free the lane."""
    import time

    home = tmp_path / "home"
    home.mkdir()
    ok = home / "voice-day-ok"
    ok.write_text("always\n")
    lock = tmp_path / "lane.lock"
    summary = tmp_path / "summary.md"
    summary.write_text("")
    now = dt.datetime(2026, 11, 12, 12, 0).timestamp()
    env = {**os.environ, "ZOE_LANDING_HOME": str(home), "ZOE_TEST_LANE_LOCK": str(lock), "LANE_WAIT_S": "30",
           "ZOE_LANDING_NOW_EPOCH": str(int(now)), "GITHUB_STEP_SUMMARY": str(summary)}
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    p = subprocess.Popen(["bash", "-e", "-c", f'source "{GATE}" unit-full; echo AFTER_GATE; sleep 5'], env=env, stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(2)                                  # the gate passed its first check and is now waiting on the lane
        ok.unlink()                                    # ... the household-hours pause returns during the wait
        fcntl.flock(fd, fcntl.LOCK_UN)                 # the lane frees: the gate wins it
        out, _ = p.communicate(timeout=60)
    finally:
        os.close(fd)
    assert p.returncode == 0 and "AFTER_GATE" not in out and "after the lane wait" in summary.read_text()
    fd2 = os.open(lock, os.O_RDWR)
    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)    # the skip released the lane
    os.close(fd2)
