"""cleanup_dead_units, codex round 2 on #2110: dependency dirs, kept families, a failed daemon-reload.

Fake units dir and a fake systemctl; the real one is never reached.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "cleanup_dead_units.py"
CTL: list = []


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("cleanup_dead_units_deps", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _no_real_systemctl(monkeypatch):
    CTL.clear()

    def boom(*a, **k):
        raise AssertionError("a test reached the real subprocess/systemctl")
    monkeypatch.setattr("subprocess.run", boom)


@pytest.fixture
def units(tmp_path):
    d = tmp_path / "units"
    d.mkdir()
    for name in ("hermes-agent.service", "bonsai-server.service", "zoe-keepwarm.service", "zoe-keepwarm.timer", "zoe-data.service"):
        (d / name).write_text("[Unit]\nDescription=x\n")
    return d


def dead(_u):
    return "disabled", "inactive"


def run(tool, units, tmp_path, *args, state=dead, rc=0):
    return tool.main(["--units-dir", str(units), "--repo-root", str(tmp_path / "norepo"), "--backup-root", str(tmp_path / "bk"), *args],
                     state=state, ctl=lambda argv: CTL.append(argv) or (rc if argv == ["daemon-reload"] else 0))


def test_a_requires_directory_link_blocks_removal(tool, units, tmp_path, capsys):
    """Break: only recognise ``.wants`` again -> ``bonsai-server`` is moved out from under a live unit's ``.requires/`` link."""
    req = units / "zoe-data.service.requires"
    req.mkdir()
    (req / "bonsai-server.service").symlink_to(units / "bonsai-server.service")
    assert run(tool, units, tmp_path, "--apply") == 1
    assert "zoe-data.service.requires/bonsai-server.service (link)" in capsys.readouterr().out
    assert (units / "bonsai-server.service").exists()


def test_an_upholds_directory_link_blocks_removal(tool, units, tmp_path):
    up = units / "zoe-data.service.upholds"
    up.mkdir()
    (up / "bonsai-server.service").symlink_to(units / "bonsai-server.service")
    assert run(tool, units, tmp_path, "--apply") == 1 and (units / "bonsai-server.service").exists()


def test_a_dependency_dir_owned_by_a_removed_unit_does_not_block(tool, units, tmp_path):
    wants = units / "hermes-agent.service.wants"
    wants.mkdir()
    (wants / "bonsai-server.service").symlink_to(units / "bonsai-server.service")
    assert run(tool, units, tmp_path, "--apply") == 0 and not (units / "bonsai-server.service").exists()


def test_a_blocked_family_keeps_what_it_depends_on(tool, units, tmp_path, capsys):
    """Break: skip every retired family's files again -> the ACTIVE keepwarm loses ``hermes-agent`` underneath it."""
    (units / "zoe-keepwarm.service").write_text("[Unit]\nRequires=hermes-agent.service\n")
    state = lambda u: ("disabled", "active") if u == "zoe-keepwarm.service" else dead(u)
    assert run(tool, units, tmp_path, "--apply", state=state) == 1
    out = capsys.readouterr().out
    assert "hermes-agent: BLOCKED" in out and "referenced by zoe-keepwarm.service (Requires=)" in out
    assert (units / "hermes-agent.service").exists() and (units / "zoe-keepwarm.service").exists()
    assert not (units / "bonsai-server.service").exists()                 # an unrelated removable family still goes


def test_a_family_not_selected_with_only_still_counts_as_live(tool, units, tmp_path, capsys):
    (units / "zoe-keepwarm.service").write_text("[Unit]\nAfter=hermes-agent.service\n")
    assert run(tool, units, tmp_path, "--apply", "--only", "hermes-agent") == 1
    assert "referenced by zoe-keepwarm.service (After=)" in capsys.readouterr().out and (units / "hermes-agent.service").exists()


def test_blocking_propagates_to_a_third_family(tool, units, tmp_path, capsys):
    """keepwarm is active -> hermes-agent stays -> hermes-agent's own Wants= keeps bonsai-server too (a fixpoint, not one pass)."""
    (units / "zoe-keepwarm.service").write_text("[Unit]\nRequires=hermes-agent.service\n")
    (units / "hermes-agent.service").write_text("[Unit]\nWants=bonsai-server.service\n")
    state = lambda u: ("disabled", "active") if u == "zoe-keepwarm.service" else dead(u)
    assert run(tool, units, tmp_path, "--apply", state=state) == 1
    out = capsys.readouterr().out
    assert "bonsai-server: BLOCKED" in out and "referenced by hermes-agent.service (Wants=)" in out
    assert (units / "bonsai-server.service").exists()


def test_a_failed_daemon_reload_fails_the_run_and_says_how_to_restore(tool, units, tmp_path, capsys):
    """Break: ignore the daemon-reload return code -> exit 0 with the manager still holding the removed units."""
    assert run(tool, units, tmp_path, "--apply", rc=1) == 1
    out = capsys.readouterr().out
    assert "FAILED: systemctl --user daemon-reload returned non-zero" in out and "restore with:" in out
    assert ["daemon-reload"] in CTL and not any(c[0] == "reset-failed" for c in CTL)
    assert run(tool, units, tmp_path, "--apply", rc=0) == 0                 # nothing left to move: a clean second run


def test_a_backslash_continuation_still_names_the_unit(tool, units, tmp_path, capsys):
    """Codex round 3 on #2110. Break: scan physical lines again -> ``hermes-agent.service`` on the continuation line is never
    seen and an ACTIVE unit's dependency is moved by ``--apply``."""
    (units / "zoe-keepwarm.service").write_text(
        "[Unit]\nDescription=x\nRequires=network.target \\\n    hermes-agent.service\n")
    state = lambda u: ("disabled", "active") if u == "zoe-keepwarm.service" else dead(u)
    assert run(tool, units, tmp_path, "--apply", state=state) == 1
    assert "referenced by zoe-keepwarm.service (Requires=)" in capsys.readouterr().out
    assert (units / "hermes-agent.service").exists()
    # a comment inside the continuation does not end it; a line WITHOUT a trailing backslash does
    assert tool._directive_refs("Requires=a.service \\\n# note\n  hermes-agent.service\n", {"hermes-agent.service"}) == ["Requires"]
    assert tool._directive_refs("Requires=a.service\nhermes-agent.service\n", {"hermes-agent.service"}) == []
