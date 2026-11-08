"""scripts/maintenance/bar_baseline_tonight.sh: the evening-slot launcher only starts the baseline run when every gate says the brain is free.

Every gate has a negative control: the all-clear case starts, and flipping exactly one input refuses. The clock, night_busy, pgrep, the
brain-window lock and the live checkout are all stubbed (paths and the clock only - the script has no switch that skips a gate).
"""
from __future__ import annotations

import calendar
import fcntl
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "bar_baseline_tonight.sh"


def _epoch(hh: int, mm: int) -> int:
    return calendar.timegm((2026, 11, 5, hh, mm, 0))   # the subprocess runs with TZ=UTC


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)
    return path


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture()
def rig(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    _git(["init", "-q"], live)
    (live / "f.txt").write_text("x")
    _git(["add", "f.txt"], live)
    _git(["commit", "-qm", "init"], live)
    marker = tmp_path / "ran"
    # night_busy exits 1 = "may run" unless the env says the kind is paused (exit 0)
    nb = _stub(tmp_path / "night_busy.sh", 'case "$1" in voice) [ "${PAUSE_VOICE:-0}" = 1 ] && exit 0;; test) [ "${PAUSE_TEST:-0}" = 1 ] && exit 0;; esac; exit 1')
    rec = _stub(tmp_path / "record.sh", f'echo "$@" > {marker}')
    pg = _stub(tmp_path / "pgrep", '[ "${LANDER_ALIVE:-0}" = 1 ] && exit 0; exit 1')
    lock = tmp_path / "brain.lock"
    base = {"PATH": os.environ["PATH"], "TZ": "UTC", "HOME": str(tmp_path), "ZOE_BASELINE_NIGHT_BUSY": str(nb),
            "ZOE_BASELINE_RECORD": str(rec), "ZOE_BASELINE_PGREP": str(pg), "ZOE_BASELINE_BRAIN_LOCK": str(lock),
            "ZOE_LANDING_REPO": str(live)}

    def run(hh=23, mm=0, check=False, extra_env=None, args=()):
        env = {**base, "ZOE_BASELINE_NOW_EPOCH": str(_epoch(hh, mm)), **(extra_env or {})}
        cmd = ["bash", str(SCRIPT), *(["--check"] if check else []), *args]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)

    return type("Rig", (), {"run": staticmethod(run), "marker": marker, "live": live, "lock": lock})


def test_all_clear_in_the_slot_starts_the_run_and_passes_args_through(rig):
    r = rig.run(23, 0, args=("--no-person",))
    assert r.returncode == 0, r.stderr
    assert rig.marker.read_text().strip() == "--no-person"


def test_check_mode_reports_go_but_runs_nothing(rig):
    r = rig.run(23, 0, check=True)
    assert r.returncode == 0 and "clear to run" in r.stdout
    assert not rig.marker.exists()


@pytest.mark.parametrize("hh,mm", [(21, 59), (7, 0), (12, 0), (1, 5), (1, 30), (2, 0), (4, 0), (4, 55), (6, 59)])
def test_outside_the_evening_slot_refuses(rig, hh, mm):
    r = rig.run(hh, mm)
    assert r.returncode == 3 and "evening slot" in r.stderr
    assert not rig.marker.exists()


@pytest.mark.parametrize("hh,mm", [(22, 0), (23, 59), (0, 0), (1, 4)])
def test_slot_edges_that_start(rig, hh, mm):
    assert rig.run(hh, mm, check=True).returncode == 0


def test_night_busy_voice_pause_refuses(rig):
    r = rig.run(extra_env={"PAUSE_VOICE": "1"})
    assert r.returncode == 3 and "voice says pause" in r.stderr and not rig.marker.exists()


def test_night_busy_test_pause_refuses(rig):
    r = rig.run(extra_env={"PAUSE_TEST": "1"})
    assert r.returncode == 3 and "test says pause" in r.stderr and not rig.marker.exists()


def test_a_live_voice_lander_refuses(rig):
    r = rig.run(extra_env={"LANDER_ALIVE": "1"})
    assert r.returncode == 3 and "land_voice_pr.sh" in r.stderr and not rig.marker.exists()


def test_a_held_brain_window_lock_refuses_and_a_free_one_does_not(rig):
    rig.lock.write_text("")
    assert rig.run(check=True).returncode == 0               # control: the file exists but nobody holds it
    with open(rig.lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = rig.run()
    assert r.returncode == 3 and "is held" in r.stderr and not rig.marker.exists()


def test_a_dirty_live_checkout_refuses_with_its_own_exit_code(rig):
    (rig.live / "untracked.txt").write_text("cruft")
    r = rig.run()
    assert r.returncode == 4 and "not clean" in r.stderr and not rig.marker.exists()


def test_an_unreadable_live_checkout_refuses(rig, tmp_path):
    r = rig.run(extra_env={"ZOE_LANDING_REPO": str(tmp_path / "missing")})
    assert r.returncode == 4 and not rig.marker.exists()
