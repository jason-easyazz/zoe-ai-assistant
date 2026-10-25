"""Deletion-decision tests for scripts/maintenance/unit_drift_cleanup.sh (fake SYSTEMCTL, temp unit dirs)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "unit_drift_cleanup.sh"
U = "bonsai-server.service"


def _run(tmp_path, state="inactive", state_rc=3, execute=True, extra=None, backup=None, wants_link=False):
    unitdir = tmp_path / "units"
    unitdir.mkdir(exist_ok=True)
    (unitdir / U).write_text("[Service]\nExecStart=/bin/true\n")
    for name, body in (extra or {}).items():
        (unitdir / name).write_text(body)
    if wants_link:
        (unitdir / "default.target.wants").mkdir()
        (unitdir / "default.target.wants" / U).symlink_to(unitdir / U)
    fake = tmp_path / "fakectl"
    fake.write_text(f'#!/bin/sh\ncase "$2" in is-active) echo "{state}"; exit {state_rc};; esac\nexit 0\n')
    fake.chmod(0o755)
    env = dict(os.environ, ZOE_UNIT_DIR=str(unitdir), SYSTEMCTL=str(fake),
               ZOE_UNIT_BACKUP_DIR=str(backup or tmp_path / "bak"))
    r = subprocess.run(["bash", str(SCRIPT)] + (["--execute"] if execute else []),
                       env=env, capture_output=True, text=True)
    return r, unitdir


def test_dry_run_removes_nothing(tmp_path):
    r, d = _run(tmp_path, execute=False)
    assert r.returncode == 0 and (d / U).exists()


def test_inactive_unreferenced_is_removed_after_backup(tmp_path):
    r, d = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (d / U).exists() and (tmp_path / "bak" / U).exists()


@pytest.mark.parametrize("state", ["active", "activating", "Failed to connect to bus: No medium found", "reloading"])
def test_active_or_unknown_state_is_kept(tmp_path, state):
    r, d = _run(tmp_path, state=state, state_rc=0 if state == "active" else 1)
    assert (d / U).exists(), r.stdout


@pytest.mark.parametrize("line", [f"Requires={U}", f"After=x.service {U}", f"Wants=a.service \\\n  {U}"])
def test_referenced_is_kept(tmp_path, line):
    r, d = _run(tmp_path, extra={"other.service": f"[Unit]\n{line}\n"})
    assert (d / U).exists(), r.stdout


def test_wants_link_is_kept(tmp_path):
    r, d = _run(tmp_path, wants_link=True)
    assert (d / U).exists(), r.stdout


def test_failed_backup_removes_nothing_and_exits_nonzero(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    r, d = _run(tmp_path, backup=blocker / "sub")  # mkdir under a regular file fails
    assert r.returncode != 0 and (d / U).exists()


def test_failed_copy_removes_nothing(tmp_path):
    bak = tmp_path / "bak"
    bak.mkdir()
    bak.chmod(0o500)
    try:
        if os.access(bak, os.W_OK):
            pytest.skip("running as root; cannot make dir unwritable")
        r, d = _run(tmp_path, backup=bak)
        assert r.returncode != 0 and (d / U).exists()
    finally:
        bak.chmod(0o700)
