"""Deletion-decision tests for scripts/maintenance/unit_drift_cleanup.sh (fake SYSTEMCTL, temp unit dirs)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "unit_drift_cleanup.sh"
U = "bonsai-server.service"


def _run(tmp_path, state="inactive", state_rc=3, execute=True, extra=None, backup=None, wants_link=False,
         states=None, repo=None):
    unitdir = tmp_path / "units"
    unitdir.mkdir(exist_ok=True)
    (unitdir / U).write_text("[Service]\nExecStart=/bin/true\n")
    for name, body in (extra or {}).items():
        (unitdir / name).write_text(body)
    if wants_link:
        (unitdir / "default.target.wants").mkdir()
        (unitdir / "default.target.wants" / U).symlink_to(unitdir / U)
    fake = tmp_path / "fakectl"
    per = "".join(f'  {n}) echo "{v}"; exit {0 if v == "active" else 3};;\n' for n, v in (states or {}).items())
    fake.write_text(f'#!/bin/sh\ncase "$2" in is-active) case "$3" in\n{per}  *) echo "{state}"; exit {state_rc};; esac;; esac\nexit 0\n')
    fake.chmod(0o755)
    env = dict(os.environ, ZOE_UNIT_DIR=str(unitdir), SYSTEMCTL=str(fake),
               ZOE_UNIT_BACKUP_DIR=str(backup or tmp_path / "bak"),
               ZOE_REPO_ROOT=str(repo or tmp_path / "emptyrepo"))
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


@pytest.mark.parametrize("name", ["other.target", "other.path", "other.socket", "other.mount", "other.slice"])
def test_reference_from_any_unit_type_keeps_it(tmp_path, name):
    r, d = _run(tmp_path, extra={name: f"[Unit]\nRequires={U}\n"})
    assert (d / U).exists(), r.stdout


def test_reference_from_wants_dir_file_keeps_it(tmp_path):
    (tmp_path / "units" / "x.target.wants").mkdir(parents=True)
    (tmp_path / "units" / "x.target.wants" / "real.conf").write_text(f"[Unit]\nWants={U}\n")
    r, d = _run(tmp_path)
    assert (d / U).exists(), r.stdout


@pytest.mark.parametrize("line", [f"Requires = {U}", f"Requires\t=\t{U}", f"After =x.service {U}"])
def test_whitespace_around_equals_is_still_a_reference(tmp_path, line):
    r, d = _run(tmp_path, extra={"other.service": f"[Unit]\n{line}\n"})
    assert (d / U).exists(), r.stdout


PAIR = {"zoe-keepwarm.service": "[Service]\nExecStart=/bin/true\n",
        "zoe-keepwarm.timer": "[Timer]\nUnit=zoe-keepwarm.service\n"}


def test_active_sibling_timer_keeps_inactive_service(tmp_path):
    r, d = _run(tmp_path, extra=PAIR, states={"zoe-keepwarm.timer": "active", "zoe-keepwarm.service": "inactive"})
    assert (d / "zoe-keepwarm.service").exists() and (d / "zoe-keepwarm.timer").exists(), r.stdout
    assert not (d / U).exists()  # unrelated eligible unit is still removed


def test_eligible_pair_is_removed_together(tmp_path):
    r, d = _run(tmp_path, extra=PAIR)
    assert not (d / "zoe-keepwarm.service").exists() and not (d / "zoe-keepwarm.timer").exists(), r.stdout


def test_externally_referenced_timer_keeps_pair(tmp_path):
    r, d = _run(tmp_path, extra=dict(PAIR, **{"z.target": "[Unit]\nWants=zoe-keepwarm.timer\n"}))
    assert (d / "zoe-keepwarm.service").exists() and (d / "zoe-keepwarm.timer").exists(), r.stdout


def test_protected_disabled_unit_is_not_moved(tmp_path):
    d = tmp_path / "units"
    d.mkdir()
    (d / "llama-server-12b-deepbrain.service.disabled").write_text("[Service]\n")
    (d / "junk.service.bak").write_text("x")
    r, d = _run(tmp_path)
    assert (d / "llama-server-12b-deepbrain.service.disabled").exists(), r.stdout
    assert not (d / "junk.service.bak").exists() and (tmp_path / "bak" / "junk.service.bak").exists()


def test_cruft_referenced_by_repo_code_is_not_moved(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "reader.py").write_text('P = "~/.config/systemd/user/custom-thing.service.bak-1"\n')
    d = tmp_path / "units"
    d.mkdir()
    (d / "custom-thing.service.bak-1").write_text("x")
    r, d = _run(tmp_path, repo=repo)
    assert (d / "custom-thing.service.bak-1").exists(), r.stdout


def test_a_second_run_never_overwrites_an_earlier_backup_copy(tmp_path):
    """PR #2178 review. Break: cp -a over an existing backup -> the second same-day run replaces the original recovery copy."""
    bak = tmp_path / "bak"
    bak.mkdir()
    (bak / U).write_text("ORIGINAL RECOVERY COPY\n")
    r, d = _run(tmp_path, backup=bak)
    assert r.returncode == 1, r.stdout
    assert (bak / U).read_text() == "ORIGINAL RECOVERY COPY\n"      # untouched
    assert (d / U).exists()                                         # and nothing was removed


def test_a_failed_removal_is_reported_and_exits_nonzero(tmp_path):
    """PR #2178 review. Break: count a unit as removed even when rm fails -> 'removed' and exit 0."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory write bits")
    unitdir = tmp_path / "units"
    unitdir.mkdir()
    (unitdir / U).write_text("[Service]\nExecStart=/bin/true\n")
    fake = tmp_path / "fakectl"
    fake.write_text('#!/bin/sh\ncase "$2" in is-active) echo inactive; exit 3;; esac\nexit 0\n')
    fake.chmod(0o755)
    env = dict(os.environ, ZOE_UNIT_DIR=str(unitdir), SYSTEMCTL=str(fake), ZOE_UNIT_BACKUP_DIR=str(tmp_path / "bak"),
               ZOE_REPO_ROOT=str(tmp_path / "emptyrepo"))
    os.chmod(unitdir, 0o500)                                         # readable, not writable: backup works, rm cannot
    try:
        r = subprocess.run(["bash", str(SCRIPT), "--execute"], env=env, capture_output=True, text=True)
    finally:
        os.chmod(unitdir, 0o700)
    assert r.returncode == 1, r.stdout
    assert "FAILED to remove" in r.stdout and (unitdir / U).exists()
    assert (tmp_path / "bak" / U).exists()                          # the backup was still taken
