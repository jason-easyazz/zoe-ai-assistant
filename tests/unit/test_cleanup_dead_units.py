"""scripts/maintenance/cleanup_dead_units.py - the owner-run removal of retired installs' user units (deep dive B9).

A fake units dir and a fake systemctl; nothing here touches the real ~/.config/systemd/user.
"""
from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "cleanup_dead_units.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("cleanup_dead_units", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def units(tmp_path):
    d = tmp_path / "units"
    d.mkdir()
    for name in ("hermes-agent.service", "openclaw-gateway.service", "flue-zoe-brain.service", "zoe-dreaming.service",
                 "zoe-dreaming.timer", "zoe-keepwarm.service", "zoe-keepwarm.timer", "bonsai-server.service",
                 "openclaw-gateway.service.bak-20260526", "flue-zoe-brain-2x.service", "zoe-data.service"):
        (d / name).write_text("[Unit]\nDescription=x\n")
    (d / "hermes-agent.service.d").mkdir()
    (d / "hermes-agent.service.d" / "kanban-worker-lean.conf").write_text("[Service]\nNice=5\n")
    (d / "zoe-keepwarm.service").write_text("[Unit]\nAfter=hermes-agent.service llama-server.service\n")
    (d / "zoe-dreaming.timer").write_text("[Timer]\nUnit=zoe-dreaming.service\n")
    (d / "default.target.wants").mkdir()
    return d


def dead(_unit):
    return "disabled", "inactive"


CTL: list = []          # every systemctl call the tool would make; the real systemctl is never reached from a test


@pytest.fixture(autouse=True)
def _fake_systemctl(monkeypatch):
    CTL.clear()

    def boom(*a, **k):
        raise AssertionError("a test reached the real subprocess/systemctl")
    monkeypatch.setattr("subprocess.run", boom)


def run(tool, units, tmp_path, *args, state=dead):
    return tool.main(["--units-dir", str(units), "--repo-root", str(tmp_path / "norepo"), "--backup-root", str(tmp_path / "bk"), *args],
                     state=state, ctl=lambda argv: CTL.append(argv) or 0)


def test_dry_run_changes_nothing_and_reports_every_family(tool, units, tmp_path, capsys):
    before = sorted(p.name for p in units.iterdir())
    assert run(tool, units, tmp_path) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and out.count("removable") == 6 and "Nothing was changed" in out
    assert sorted(p.name for p in units.iterdir()) == before and not (tmp_path / "bk").exists()


def test_apply_moves_the_files_not_deletes_and_leaves_live_units(tool, units, tmp_path):
    assert run(tool, units, tmp_path, "--apply") == 0
    left = sorted(p.name for p in units.iterdir())
    assert left == ["default.target.wants", "flue-zoe-brain-2x.service", "zoe-data.service"]       # live ones untouched
    (bdir,) = list((tmp_path / "bk").iterdir())
    assert stat.S_IMODE(bdir.stat().st_mode) == 0o700
    moved = sorted(p.name for p in bdir.iterdir())
    assert "hermes-agent.service.d" in moved and "openclaw-gateway.service.bak-20260526" in moved and "bonsai-server.service" in moved
    assert ["daemon-reload"] in CTL
    assert ["reset-failed", "zoe-keepwarm.timer"] in CTL


def test_the_2x_brain_is_never_in_the_flue_family(tool, units):
    assert [p.name for p in tool.family_files(units, "flue-zoe-brain")] == ["flue-zoe-brain.service"]


def test_an_enabled_unit_blocks_its_family(tool, units, tmp_path, capsys):
    state = lambda u: ("enabled", "inactive") if u == "bonsai-server.service" else dead(u)
    assert run(tool, units, tmp_path, "--apply", state=state) == 1
    out = capsys.readouterr().out
    assert "bonsai-server: BLOCKED" in out and (units / "bonsai-server.service").exists()
    assert not (units / "openclaw-gateway.service").exists()                       # the other families still went


def test_an_active_unit_blocks_its_family(tool, units, tmp_path):
    state = lambda u: ("disabled", "active") if u == "zoe-dreaming.timer" else dead(u)
    assert run(tool, units, tmp_path, "--apply", state=state) == 1
    assert (units / "zoe-dreaming.timer").exists() and (units / "zoe-dreaming.service").exists()


def test_a_live_unit_that_depends_on_a_dead_one_blocks_it(tool, units, tmp_path, capsys):
    (units / "zoe-data.service").write_text("[Unit]\nWants=bonsai-server.service\n")
    assert run(tool, units, tmp_path, "--apply") == 1
    assert "referenced by zoe-data.service (Wants=)" in capsys.readouterr().out and (units / "bonsai-server.service").exists()


def test_a_comment_or_a_non_dependency_key_is_not_a_reference(tool, units, tmp_path):
    (units / "zoe-data.service").write_text("[Unit]\n# Wants=bonsai-server.service\nDescription=talks about bonsai-server.service\n")
    assert run(tool, units, tmp_path, "--apply") == 0 and not (units / "bonsai-server.service").exists()


def test_an_enabled_symlink_in_a_wants_dir_blocks(tool, units, tmp_path):
    (units / "default.target.wants" / "openclaw-gateway.service").symlink_to(units / "openclaw-gateway.service")
    assert run(tool, units, tmp_path, "--apply") == 1 and (units / "openclaw-gateway.service").exists()


def test_a_name_that_merely_contains_a_dead_name_is_not_a_reference(tool, units, tmp_path):
    (units / "zoe-data.service").write_text("[Unit]\nAfter=flue-zoe-brain-2x.service\n")
    assert run(tool, units, tmp_path, "--apply") == 0 and not (units / "flue-zoe-brain.service").exists()
    assert (units / "flue-zoe-brain-2x.service").exists()


def test_rerun_after_apply_is_a_noop(tool, units, tmp_path, capsys):
    run(tool, units, tmp_path, "--apply")
    capsys.readouterr()
    assert run(tool, units, tmp_path, "--apply") == 0
    assert capsys.readouterr().out.count("already gone") == 6


def test_the_families_are_exactly_the_retired_installs():
    spec = importlib.util.spec_from_file_location("cdu_names", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert set(mod.FAMILIES) == {"hermes-agent", "openclaw-gateway", "flue-zoe-brain", "zoe-dreaming", "zoe-keepwarm", "bonsai-server"}


def test_a_timer_that_exists_only_elsewhere_still_blocks_its_family(tool, units, tmp_path):
    """PR #2110: only the service is in this units dir; its timer is loaded from another user-unit directory and ACTIVE.
    Break: skip members with no local file -> the timer is never asked about and the service is moved from under it."""
    (units / "zoe-dreaming.timer").unlink()
    asked = []

    def state(u):
        asked.append(u)
        return ("enabled", "active") if u == "zoe-dreaming.timer" else dead(u)

    assert run(tool, units, tmp_path, "--apply", state=state) == 1
    assert "zoe-dreaming.timer" in asked
    assert (units / "zoe-dreaming.service").exists()


def test_default_backup_root_is_the_dir_the_hourly_job_prunes(tool, units, tmp_path, monkeypatch):
    """PR #2110 review. Break: default to ~/.zoe/backups -> moved unit files (which can carry Environment= secrets) never expire."""
    pruned = tmp_path / "pruned"
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(pruned))
    assert tool.main(["--units-dir", str(units), "--repo-root", str(tmp_path / "norepo"), "--apply"],
                     state=dead, ctl=lambda argv: 0) == 0
    (bdir,) = list(pruned.glob("dead-units-*"))
    assert stat.S_IMODE(bdir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in bdir.iterdir() if f.is_file())


def test_explicit_backup_root_outside_the_pruned_dir_warns(tool, units, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(tmp_path / "pruned"))
    assert run(tool, units, tmp_path, "--apply") == 0
    assert "will NOT expire" in capsys.readouterr().err
