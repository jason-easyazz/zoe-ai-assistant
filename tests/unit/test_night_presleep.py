"""ONE ORCHESTRATOR for the night (docs/knowledge/night-window.md section 19): the backup and the memory export run in the window's PRE-SLEEP phase
(``night_window.sh --presleep``, the unit's ``ExecStartPre=-``), sequenced, idempotent per day, independent of whether the window itself can run. Host-seam doubles.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json
import re
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "perf"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "night"))

import night_window as nw  # noqa: E402
from test_night_window import SYSTEMD, _private_locks, at, make  # noqa: E402,F401

REPO = Path(__file__).resolve().parents[2]


def started(host):
    return [c for c in host.joined() if "start --wait" in c]


def test_backup_runs_first_then_the_export_and_both_are_recorded(tmp_path):
    w, host, cfg = make(tmp_path)
    st = nw.run_presleep(cfg, host, host.log)
    assert [c.split()[-1] for c in started(host)] == ["zoe-backup.service", "zoe-memory-export.service"]
    assert st["zoe-backup.service"]["rc"] == 0 and st["zoe-memory-export.service"]["rc"] == 0
    assert json.loads(nw.presleep_path(cfg, host).read_text())["zoe-backup.service"]["rc"] == 0


def test_the_retry_slots_do_not_start_a_unit_that_already_succeeded_today(tmp_path):
    w, host, cfg = make(tmp_path)
    nw.run_presleep(cfg, host, host.log)
    n = len(started(host))
    assert n == 2                                                           # control: the first call did start both
    nw.run_presleep(cfg, host, host.log)
    assert len(started(host)) == n


def test_a_failed_backup_is_recorded_loudly_does_not_stop_the_export_and_is_retried_by_the_next_slot(tmp_path):
    w, host, cfg = make(tmp_path, fail_run=("zoe-backup.service",))
    st = nw.run_presleep(cfg, host, host.log)
    assert st["zoe-backup.service"]["rc"] != 0 and st["zoe-memory-export.service"]["rc"] == 0
    host.fail_run = ()
    st2 = nw.run_presleep(cfg, host, host.log)
    assert st2["zoe-backup.service"]["rc"] == 0                             # a failure is not stamped done: the 03:05 slot tries again
    assert len([c for c in started(host) if c.endswith("zoe-memory-export.service")]) == 1


def test_the_window_report_carries_the_presleep_result(tmp_path):
    w, host, cfg = make(tmp_path, fail_run=("zoe-memory-export.service",))
    nw.run_presleep(cfg, host, host.log)
    host.fail_run = ()
    assert w.run() == nw.EXIT_OK, w.outcome
    assert any(n.startswith("presleep zoe-memory-export.service: FAILED") for n in w.rec["notes"])
    assert any(n.startswith("presleep zoe-backup.service: ok") for n in w.rec["notes"])


def test_the_unit_runs_presleep_before_the_window_and_ignores_its_failure():
    body = "\n".join(ln for ln in (SYSTEMD / "zoe-night-window.service").read_text().splitlines() if not ln.lstrip().startswith("#"))
    pre = re.search(r"^ExecStartPre=(-?)(\S+) --presleep$", body, re.M)
    assert pre and pre.group(1) == "-", "the leading '-' keeps the backup independent of the window: it never blocks, and a window that refuses still leaves the backup taken"
    assert body.index("ExecStartPre=") < body.index("ExecStart=")


def test_the_two_timers_are_gone_from_the_repo_and_registered_retired():
    assert not (SYSTEMD.parent.parent / "setup/systemd/zoe-backup.timer").exists() and not (SYSTEMD.parent.parent / "setup/systemd/zoe-memory-export.timer").exists()
    inv = (REPO / "scripts/maintenance/unit_inventory.py").read_text()
    assert '"zoe-backup.timer"' in inv and '"zoe-memory-export.timer"' in inv
    for svc in ("zoe-backup.service", "zoe-memory-export.service"):          # the services the window starts must stay
        assert (REPO / "scripts/setup/systemd" / svc).exists()


def test_an_unrelated_hour_is_refused_for_presleep_unless_forced(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(nw, "run_presleep", lambda *a, **k: called.append(1) or {})
    import datetime as dt

    class Noon(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 9, 12, 0)

    monkeypatch.setattr(nw.dt, "datetime", Noon)
    assert nw.main(["--presleep"]) == nw.EXIT_OK and called == []
    assert nw.main(["--presleep", "--anytime"], host_factory=lambda lg: object()) == nw.EXIT_OK and called == [1]


def test_each_unit_is_checkpointed_before_the_next_starts_so_a_kill_between_them_loses_nothing(tmp_path):
    w, host, cfg = make(tmp_path)
    orig = host.run

    def die_on_export(argv, timeout=60.0, mutating=True, env=None):
        if "zoe-memory-export.service" in " ".join(argv) and "start" in argv:
            raise KeyboardInterrupt("killed between the backup and the export")
        return orig(argv, timeout, mutating, env)

    host.run = die_on_export
    with pytest.raises(KeyboardInterrupt):
        nw.run_presleep(cfg, host, host.log)
    saved = json.loads(nw.presleep_path(cfg, host).read_text())
    assert saved["zoe-backup.service"]["rc"] == 0 and "zoe-memory-export.service" not in saved       # the backup is already stamped
    host.run = orig
    before = len(started(host))
    nw.run_presleep(cfg, host, host.log)
    assert [c.split()[-1] for c in started(host)[before:]] == ["zoe-memory-export.service"]            # the retry takes only the export


def test_a_late_retry_slot_does_not_start_pre_sleep_work_that_would_overrun_the_window(tmp_path):
    """The pre-sleep phase must leave the window proper 47 minutes before end-by (03:55): at 03:15 nothing is started; at 02:30 each unit is clipped to what is left."""
    w, host, cfg = make(tmp_path, start=at(3, 15))
    nw.run_presleep(cfg, host, host.log)
    assert started(host) == []                                              # 03:15 is past 03:08
    w2, host2, cfg2 = make(tmp_path / "early", start=at(2, 30))
    seen = []
    orig = host2.run
    host2.run = lambda argv, timeout=60.0, mutating=True, env=None: (seen.append(timeout) if "start" in argv and "--wait" in argv else None) or orig(argv, timeout, mutating, env)
    nw.run_presleep(cfg2, host2, host2.log)
    assert len(seen) == 2 and all(t <= cfg2.presleep_timeout_s for t in seen) and all(t <= (cfg2.end_by - cfg2.presleep_leave_min - 150) * 60 for t in seen)


def test_the_unit_start_timeout_covers_the_pre_sleep_phase_plus_the_whole_window():
    body = "\n".join(ln for ln in (SYSTEMD / "zoe-night-window.service").read_text().splitlines() if not ln.lstrip().startswith("#"))
    t = int(re.search(r"^TimeoutStartSec=(\d+)", body, re.M).group(1))
    cfg = nw.NightCfg()
    assert t >= (cfg.end_by - (2 * 60 + 30) + 15) * 60            # from the 02:30 start to end-by (the pre-sleep phase + the window + its restore) plus 15 minutes of slack


def test_the_boot_catchup_runs_only_when_no_backup_succeeded_in_the_last_day(tmp_path):
    w, host, cfg = make(tmp_path, start=at(9, 0))
    assert nw.presleep_is_stale(cfg, host) is True                           # nothing recorded
    nw.run_presleep(cfg, host, host.log, catchup=True)
    assert len(started(host)) == 2 and nw.presleep_is_stale(cfg, host) is False        # catch-up ran (any hour) and is now fresh
    host.t += 30 * 3600
    assert nw.presleep_is_stale(cfg, host) is True                           # 30 h later it is stale again


def test_the_catchup_units_exist_and_are_installed_with_the_window():
    svc = (SYSTEMD / "zoe-night-presleep-catchup.service").read_text()
    tim = (SYSTEMD / "zoe-night-presleep-catchup.timer").read_text()
    assert "night_window.sh --presleep --catchup" in svc and "OnBootSec=" in tim and "Persistent" not in "\n".join(l for l in tim.splitlines() if not l.startswith("#"))
    assert "zoe-night-presleep-catchup.timer" in (REPO / "scripts/night/install_night_window.sh").read_text()


def _fake_systemctl(tmp_path, fail=""):
    d = tmp_path / "off"
    d.mkdir()
    log = tmp_path / "ctl.log"
    sc = tmp_path / "fakectl"
    sc.write_text(f"""#!/bin/bash
echo "$@" >> {log}
case "$*" in
  *"{fail or "NEVER-MATCHES"}"*) exit 1 ;;
  *is-enabled*) u="${{@: -1}}"; if [ -e {d}/$u ]; then echo disabled; exit 1; fi; echo enabled ;;
  *"disable --now"*) touch {d}/"${{@: -1}}" ;;
  *"enable --now"*) rm -f {d}/"${{@: -1}}" ;;
esac
exit 0
""")
    sc.chmod(0o755)
    return sc, log, d


def test_the_installer_switches_the_two_timers_off_survives_a_reinstall_and_uninstall_turns_them_back_on(tmp_path):
    sc, log, off = _fake_systemctl(tmp_path)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NIGHT_DIR": str(tmp_path / "n"), "NIGHT_UNIT_DIR": str(tmp_path / "u"), "NIGHT_SYSTEMCTL": str(sc)}
    run = lambda *a: subprocess.run(["bash", str(REPO / "scripts/night/install_night_window.sh"), *a], capture_output=True, text=True, timeout=60, env=env)
    r1 = run()
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert (off / "zoe-backup.timer").exists() and (off / "zoe-memory-export.timer").exists()      # both switched off
    r2 = run()                                                                                      # a reinstall sees them already disabled: the ORIGINAL state must survive
    assert r2.returncode == 0, r2.stdout + r2.stderr
    state = (tmp_path / "n" / "INSTALLED").read_text()
    assert "zoe-backup.timer_was=enabled" in state and "zoe-memory-export.timer_was=enabled" in state
    r3 = run("--uninstall")
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert not (off / "zoe-backup.timer").exists() and not (off / "zoe-memory-export.timer").exists()   # re-enabled
    assert "enable --now zoe-backup.timer" in log.read_text()


def test_a_timed_out_unit_is_stopped_before_the_next_one_starts(tmp_path):
    w, host, cfg = make(tmp_path)
    real = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        if argv[-1] == "zoe-backup.service" and "start" in argv:
            host.cmds.append((list(argv), mutating))
            raise subprocess.TimeoutExpired(argv, timeout)           # `start --wait` ends the CLIENT only; the unit keeps running
        return real(argv, timeout=timeout, mutating=mutating, env=env)
    host.run = run
    nw.run_presleep(cfg, host, host.log)
    j = host.joined()
    stop = next(i for i, c in enumerate(j) if c.endswith("stop zoe-backup.service"))
    export = next(i for i, c in enumerate(j) if "start --wait zoe-memory-export.service" in c)
    assert stop < export, "the export must not start while the backup unit may still be running"


def test_a_refused_window_still_reports_a_failed_presleep(tmp_path):
    w, host, cfg = make(tmp_path, fail_run=("zoe-backup.service",))
    nw.run_presleep(cfg, host, host.log)

    def refuse():
        raise nw.Refused("test refusal")
    w.preflight = refuse
    w.run()
    assert any(n.startswith("presleep zoe-backup.service: FAILED") for n in w.rec["notes"])


def test_presleep_is_serialised_by_a_lock_file_released_after_the_run(tmp_path):
    import fcntl
    import os
    w, host, cfg = make(tmp_path)
    nw.run_presleep(cfg, host, host.log)
    lock = cfg.night_dir / "presleep.lock"
    assert lock.exists()
    fd = os.open(str(lock), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)               # free again once the run finished
    finally:
        os.close(fd)


def test_a_failed_install_puts_the_backup_and_export_timers_back(tmp_path):
    sc, log, off = _fake_systemctl(tmp_path, fail="enable --now zoe-night-window.timer")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NIGHT_DIR": str(tmp_path / "n"), "NIGHT_UNIT_DIR": str(tmp_path / "u"), "NIGHT_SYSTEMCTL": str(sc)}
    r = subprocess.run(["bash", str(REPO / "scripts/night/install_night_window.sh")], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0, r.stdout + r.stderr                                    # the window timer would not enable: the install fails ...
    assert "disable --now zoe-backup.timer" in log.read_text()                       # control: it did switch them off first
    assert not (off / "zoe-backup.timer").exists() and not (off / "zoe-memory-export.timer").exists(), "... and must leave the nightly backup schedule running"


def test_an_install_that_cannot_switch_a_legacy_timer_off_fails_and_restores_the_other(tmp_path):
    sc, log, off = _fake_systemctl(tmp_path, fail="disable --now zoe-memory-export.timer")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NIGHT_DIR": str(tmp_path / "n"), "NIGHT_UNIT_DIR": str(tmp_path / "u"), "NIGHT_SYSTEMCTL": str(sc)}
    r = subprocess.run(["bash", str(REPO / "scripts/night/install_night_window.sh")], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0, "a legacy timer left enabled would run the backup / export twice beside the window: that must not report success"
    assert "enable --now zoe-backup.timer" in log.read_text() and not (off / "zoe-backup.timer").exists()      # the one it had switched off is back
    assert "enable --now zoe-night-window.timer" not in log.read_text()
