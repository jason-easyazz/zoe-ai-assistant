"""scripts/maintenance/samantha_weekly.sh: the quiet-window refusal and the no-writes-to-the-live-checkout rule.

The script is run for real with a faked `date`, `flock` and harness python so the window can be placed anywhere
without touching the box: nothing here takes the real harness lock or starts a harness.

* inside 01:50-04:55 at start -> exit 4, nothing cloned, no harness started;
* the window opening WHILE WAITING FOR THE LOCK (the lock wait can be 2 h) -> the step is refused (exit 4) and the
  harness never starts - the boundary the single pre-flock check missed;
* outside the window -> the harness starts, from a clone, and the "live" checkout is byte-for-byte unchanged.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "maintenance" / "samantha_weekly.sh"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

# `date` shim: %H/%M come from $FAKE_STATE ("day" -> 12:00, "night" -> 03:00); the fake flock flips the state to
# FAKE_AFTER_LOCK, i.e. time passes while the wrapper waits on the lock. Every other format is the real date.
_FAKE_DATE = """#!/usr/bin/env bash
state="$(cat "$FAKE_STATE")"
case "$1" in
  +%H) case "$state" in night) echo 03 ;; near) echo 01 ;; *) echo 12 ;; esac ;;
  +%M) [ "$state" = near ] && echo 48 || echo 00 ;;
  *) exec /bin/date "$@" ;;
esac
"""
# `flock` shim: skip -E/-w options and the lock path, advance the clock, run the command.
_FAKE_FLOCK = """#!/usr/bin/env bash
while [ "${1:-}" = -E ] || [ "${1:-}" = -w ]; do shift 2; done
shift   # the lock path
echo "${FAKE_AFTER_LOCK:-day}" > "$FAKE_STATE"
exec "$@"
"""
# `python3` shim: the harness steps only record that they started; anything else is the real interpreter.
_FAKE_PY = """#!/usr/bin/env bash
case "$1" in
  */scripts/perf/samantha_*.py)
    echo "$1" >> "$HARNESS_STARTED"; echo "$*" >> "$HARNESS_ARGS"
    [ -f "${1%/scripts/perf/*}/labs/flue-zoe-brain-2x/dist/server.mjs" ] && echo linked >> "$HARNESS_FLUE"
    git -C "${1%/scripts/perf/*}" status --porcelain >> "$HARNESS_STATUS"
    exit 0 ;;
esac
exec "$REAL_PY" "$@"
"""


# `timeout` shim: records the deadline it was given, then runs the command unbounded.
_FAKE_TIMEOUT = """#!/usr/bin/env bash
echo "$*" >> "$TIMEOUT_ARGS"
shift 2   # --kill-after=60 <seconds>
exec "$@"
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout


def _run(tmp_path: Path, start: str, after_lock: str, stale_build: bool = False):
    live = tmp_path / "live"
    live.mkdir()
    _git("init", "-q", cwd=live)
    (live / "a.txt").write_text("x\n")
    flue = live / "labs" / "flue-zoe-brain-2x"
    flue.mkdir(parents=True)
    (flue / "package.json").write_text("{}\n")
    (flue / "src").mkdir()
    (flue / "src" / "brain.ts").write_text("export {}\n")
    _git("add", "a.txt", "labs/flue-zoe-brain-2x/package.json", "labs/flue-zoe-brain-2x/src/brain.ts", cwd=live)
    _git("commit", "-q", "-m", "c", cwd=live)
    fake = tmp_path / "bin"
    fake.mkdir()
    _exe(fake / "date", _FAKE_DATE)
    _exe(fake / "flock", _FAKE_FLOCK)
    _exe(fake / "python3", _FAKE_PY)
    _exe(fake / "timeout", _FAKE_TIMEOUT)
    (flue / "dist").mkdir()                       # the gitignored sidecar build a fresh clone does not have
    (flue / "dist" / "server.mjs").write_text("//\n")
    if stale_build is True:                       # a source file newer than the build: a failed rebuild
        os.utime(flue / "dist" / "server.mjs", (1, 1))
    elif stale_build == "dirty":                  # a FRESH build of uncommitted sources
        (flue / "src" / "brain.ts").write_text("export const x = 1\n")
        os.utime(flue / "dist" / "server.mjs", (4102444800, 4102444800))
    state = tmp_path / "state"
    state.write_text(start)
    started = tmp_path / "started"
    home = tmp_path / "home"
    (home / ".cache" / "zoe").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "HOME": str(home), "ZOE_ASSISTANT_ROOT": str(live),
           "FAKE_STATE": str(state), "FAKE_AFTER_LOCK": after_lock, "HARNESS_STARTED": str(started),
           "REAL_PY": sys.executable, "TMPDIR": str(tmp_path),
           "HARNESS_ARGS": str(tmp_path / "args"), "HARNESS_FLUE": str(tmp_path / "flue"), "HARNESS_STATUS": str(tmp_path / "status"), "TIMEOUT_ARGS": str(tmp_path / "timeout")}
    before = _git("status", "--porcelain=v1", "--ignored", cwd=live), sorted(p.name for p in live.iterdir())
    res = subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    after = _git("status", "--porcelain=v1", "--ignored", cwd=live), sorted(p.name for p in live.iterdir())
    return res, started, before == after


@needs_git
def test_refuses_to_start_inside_the_night_window(tmp_path):
    res, started, live_untouched = _run(tmp_path, start="night", after_lock="night")
    assert res.returncode == 4, res.stderr
    assert "night window" in res.stderr
    assert not started.exists()
    assert live_untouched


@needs_git
def test_window_opening_while_waiting_for_the_lock_refuses_the_step(tmp_path):
    # Negative control for the post-lock re-check: it is "day" at start and at the per-step check, and "night" by
    # the time the lock is granted. Without the check inside the flocked command the harness would start.
    res, started, live_untouched = _run(tmp_path, start="day", after_lock="night")
    assert res.returncode == 4, (res.returncode, res.stdout, res.stderr)
    assert "opened while waiting for the lock" in res.stderr
    assert not started.exists(), "a harness step started inside the night window"
    assert live_untouched


@needs_git
def test_outside_the_window_the_harness_runs_from_a_clone_and_the_live_checkout_is_untouched(tmp_path):
    res, started, live_untouched = _run(tmp_path, start="day", after_lock="day")
    assert res.returncode == 0, (res.stdout, res.stderr)
    steps = started.read_text().split()
    assert len(steps) == 2 and all("/repo/scripts/perf/samantha_" in s for s in steps)
    assert str(tmp_path / "live") not in "".join(steps), "the harness must run from the clone, not the live checkout"
    assert live_untouched


@needs_git
def test_a_start_just_before_the_window_is_refused(tmp_path):
    # 01:48 -> the window opens in 2 min, inside the 5 min grace: the wrapper must not start a multi-hour bench.
    res, started, live_untouched = _run(tmp_path, start="near", after_lock="near")
    assert res.returncode == 4, (res.returncode, res.stdout, res.stderr)
    assert "not starting" in res.stderr and not started.exists()
    assert live_untouched


@needs_git
def test_each_step_is_bounded_by_the_deadline_passes_the_service_dir_and_gets_the_flue_build(tmp_path):
    res, started, live_untouched = _run(tmp_path, start="day", after_lock="day")
    assert res.returncode == 0, (res.stdout, res.stderr)
    # 12:00 -> window at 01:50 = 13 h 50 min = 49800 s, minus the 300 s grace
    assert (tmp_path / "timeout").read_text().split()[:2] == ["--kill-after=60", "49500"]
    args = (tmp_path / "args").read_text().splitlines()
    assert all(f"--service-dir {tmp_path / 'live'}/services/zoe-data" in a for a in args), args   # the service dir, not the repo root
    assert all("--require-bound" in a for a in args)
    assert (tmp_path / "flue").read_text().split() == ["linked", "linked"]   # the clone sees the live checkout's sidecar build
    assert live_untouched


@needs_git
def test_a_flue_build_older_than_its_sources_is_not_linked(tmp_path):
    res, started, live_untouched = _run(tmp_path, start="day", after_lock="day", stale_build=True)
    assert "older than its sources" in res.stderr
    assert not (tmp_path / "flue").exists(), "a stale sidecar build was linked into the clone"


@needs_git
def test_a_fresh_build_of_uncommitted_sources_is_not_linked(tmp_path):
    res, started, live_untouched = _run(tmp_path, start="day", after_lock="day", stale_build="dirty")
    assert "not linked" in res.stderr
    assert not (tmp_path / "flue").exists(), "a build of uncommitted live sources was linked into a 'clean' clone"


@needs_git
def test_the_clone_stays_clean_with_the_links_in_place(tmp_path):
    # --record-baseline needs a clean clone; dist/ and node_modules/ in .gitignore do not match symlinks
    res, started, live_untouched = _run(tmp_path, start="day", after_lock="day")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert (tmp_path / "flue").exists()
    assert (tmp_path / "status").read_text() == "", (tmp_path / "status").read_text()
