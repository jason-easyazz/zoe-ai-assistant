"""Every owner-run maintenance script from the forget-reach PR must render ``--help`` (argparse %-interpolates help text).

Codex on #2110: an unescaped ``%h`` in a help string raised ``ValueError`` on ``--help`` (and at construction on Python 3.14), so the
owner-run migration was unusable. Break: put ``%h`` back in ``migrate_hermes_env.py``'s ``--home`` help.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

MAINT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance"
SCRIPTS = ("migrate_hermes_env", "redact_router_shadow", "cleanup_dead_units", "rotate_service_logs")


@pytest.mark.parametrize("name", SCRIPTS)
def test_help_renders(name, capsys):
    spec = importlib.util.spec_from_file_location(f"help_{name}", MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit) as exc:
        mod.main(["--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out
