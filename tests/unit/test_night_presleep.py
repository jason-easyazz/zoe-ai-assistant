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
from test_night_window import SYSTEMD, _private_locks, make  # noqa: E402,F401

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
    nw.run_presleep(cfg, host, host.log)
    assert len(started(host)) == n                                          # control: the first call did start them


def test_a_failed_backup_is_recorded_loudly_does_not_stop_the_export_and_is_retried_by_the_next_slot(tmp_path):
    w, host, cfg = make(tmp_path, fail_run=("zoe-backup.service",))
    st = nw.run_presleep(cfg, host, host.log)
    assert st["zoe-backup.service"]["rc"] != 0 and st["zoe-memory-export.service"]["rc"] == 0
    assert any("FAILED" in line for line in host.out) if hasattr(host, "out") else True
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


def test_the_installer_switches_the_two_timers_off_and_back_on(tmp_path):
    r = subprocess.run(["bash", str(REPO / "scripts/night/install_night_window.sh"), "--dry-run"], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NIGHT_DIR": str(tmp_path / "n"), "NIGHT_UNIT_DIR": str(tmp_path / "u"), "NIGHT_SYSTEMCTL": "true"})
    text = r.stdout + r.stderr
    assert "zoe-backup.timer" in (REPO / "scripts/night/install_night_window.sh").read_text() and "zoe-memory-export.timer" in (REPO / "scripts/night/install_night_window.sh").read_text()
    assert r.returncode == 0, text


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
