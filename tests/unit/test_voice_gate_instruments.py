"""The voice gate's INSTRUMENTS (2026-11-12): what reads the verdict and when the nightly slot measures.

1. #2408: the lander logged "gate: completed/failure" for a rerun that succeeded - `gh run rerun` returns before the new attempt exists, so an
   immediate `gh run view` shows the PREVIOUS attempt. landing_gate_state believes a state only once the attempt number has moved on.
2. landing_running printed "/proc/<pid>/cmdline: No such file or directory" for a pid that died mid-scan (a failed redirect is reported before a
   later 2>/dev/null applies).
3. The 04:30 nightly replay failed on STT speed 12 of 14 nights because the 04:15 Serena restart walked ~325 worktrees' .gitignore files through it.
   The restart now fires AFTER the gate; pinned here so it cannot drift back inside the probe's slot.
"""
import os
import re
import shutil
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from tests.unit.landing_fakes import LANDING

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SYSTEMD = REPO / "scripts" / "setup" / "systemd"
needs_bash = pytest.mark.skipif(not shutil.which("bash") or not os.path.isdir("/proc/self"), reason="needs bash and /proc")


def _fake_gh(tmp_path, out, rc=0):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    gh = d / "gh"
    gh.write_text(f'#!/bin/bash\n[ {rc} = 0 ] || exit {rc}\nprintf "%s\\n" "{out}"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return d


def _state(tmp_path, out, prev, rc=0):
    path = f"{_fake_gh(tmp_path, out, rc)}:{os.environ['PATH']}"
    r = subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_gate_state 123 {prev}'],
                       capture_output=True, text=True, env={**os.environ, "PATH": path})
    return r.stdout.strip()


@needs_bash
def test_stale_previous_attempt_is_not_the_verdict(tmp_path):
    # attempt 1 failed; the rerun was requested but has not registered: still attempt 1
    assert _state(tmp_path, "1 completed/failure", prev=1) == "pending/"


@needs_bash
def test_new_attempt_verdict_is_read(tmp_path):
    assert _state(tmp_path, "2 completed/success", prev=1) == "completed/success"
    assert _state(tmp_path, "2 in_progress/", prev=1) == "in_progress/"


@needs_bash
def test_unreadable_gate_state_is_never_a_verdict(tmp_path):
    assert _state(tmp_path, "", prev=1, rc=1) == "unreadable/"
    assert _state(tmp_path, "garbage", prev=1) == "unreadable/"


def test_lander_waits_on_the_new_attempt_not_the_old_one():
    s = (REPO / "scripts" / "landing" / "land_voice_pr.sh").read_text()
    assert "landing_gate_state" in s
    assert "--json status,conclusion" not in s


@needs_bash
def test_landing_running_is_silent_when_pids_die_mid_scan():
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            subprocess.run(["true"])

    t = threading.Thread(target=churn)
    t.start()
    try:
        errs = []
        for _ in range(5):
            r = subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_running no_such_lander_xyz.sh'],
                               capture_output=True, text=True)
            errs.append(r.stderr)
    finally:
        stop.set()
        t.join()
    assert not any("No such file" in e for e in errs), errs


def test_proc_cmdline_read_has_no_bare_redirect():
    s = (REPO / "scripts" / "landing" / "landing_lib.sh").read_text()
    assert not re.search(r'mapfile[^\n]*< "/proc/\$p/cmdline" 2>/dev/null', s)


def _fake_proc_run(tmp_path):
    return subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_running lander_xyz.sh; echo rc=$?'],
                          capture_output=True, text=True, env={**os.environ, "LANDING_PROC_ROOT": str(tmp_path)})


@needs_bash
def test_non_enoent_cmdline_errors_still_surface(tmp_path):
    # fake /proc: pid 4242's cmdline is a self-referencing symlink (ELOOP, not the vanished-pid ENOENT); pid 4243 is a readable lander
    (tmp_path / "4242").mkdir()
    (tmp_path / "4242" / "cmdline").symlink_to("cmdline")  # self-loop: ELOOP
    (tmp_path / "4243").mkdir()
    (tmp_path / "4243" / "cmdline").write_bytes(b"/bin/bash\0lander_xyz.sh\0")
    r = _fake_proc_run(tmp_path)
    assert "rc=0" in r.stdout  # the readable process is still found
    assert "Too many levels" in r.stderr, r.stderr  # the non-ENOENT error is NOT swallowed
    assert "No such file" not in r.stderr


@needs_bash
def test_vanished_pid_enoent_is_dropped(tmp_path):
    (tmp_path / "4242").mkdir()  # no cmdline file at all: ENOENT, the dead-pid race
    r = _fake_proc_run(tmp_path)
    assert "rc=1" in r.stdout and r.stderr == ""


def _calendar_minutes(path):
    m = re.search(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d):00", path.read_text(), re.M)
    assert m, path
    return int(m.group(1)) * 60 + int(m.group(2))


def test_serena_restart_is_after_the_voice_gate_not_inside_its_slot():
    gate = _calendar_minutes(SYSTEMD / "zoe-voice-regression.timer")
    serena = _calendar_minutes(SYSTEMD / "zoe-serena-pregate-restart.timer")
    # the replay takes ~3 min (flock queue / timeout can stretch it): a restart in the gate's slot puts the ~85 min
    # .gitignore walk's CPU/disk load into the STT timing (STT per audio-second ~250 ms vs ~168 at every other hour, 2026-11)
    assert serena >= gate + 15, "the Serena restart must fire after the nightly voice replay has finished"
    assert serena - gate <= 120, "keep the restart in the same quiet night slot, not in the morning"


def test_serena_restart_is_ordered_after_the_replay_unit():
    # a replay still waiting for a quiet box (up to 45 min) or on the harness lock at 04:50 must finish before the restart starts
    assert re.search(r"^After=.*zoe-voice-regression\.service", (SYSTEMD / "zoe-serena-pregate-restart.service").read_text(), re.M)


def test_lander_does_not_rerun_without_a_baseline_attempt():
    s = (REPO / "scripts" / "landing" / "land_voice_pr.sh").read_text()
    assert 'if [ -z "$ATT0" ]' in s and "ATT0=0" not in s
