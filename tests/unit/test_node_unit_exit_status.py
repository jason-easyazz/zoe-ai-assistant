"""Pins ``SuccessExitStatus=143`` on the Node-hosted Flue units.

The Flue servers trap SIGTERM and exit 143; systemd counts only 0 as a clean stop, so every
stop/deploy restart logged "Failed with result 'exit-code'" (29+ false failures in a week,
2026-09-27 -> 10-04), burying a real crash. ``Restart=always`` is independent of this.
See docs/knowledge/log-review-units-2026-10-04.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

UNIT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "systemd"
NODE_UNITS = ("flue-zoe-brain-2x", "flue-zoe-telegram")


def _directives(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


@pytest.mark.parametrize("name", NODE_UNITS)
def test_node_units_treat_sigterm_exit_143_as_success(name):
    lines = _directives(UNIT_DIR / f"{name}.service")
    assert any(re.fullmatch(r"SuccessExitStatus=.*\b143\b.*", ln) for ln in lines), name
    assert "Restart=always" in lines  # crash recovery stays independent of the clean-exit mapping


def test_every_flue_unit_is_covered_and_the_dropins_carry_the_same_mapping():
    flue = {p.stem for p in UNIT_DIR.glob("*.service")
            if re.search(r"^ExecStart=.*/node\b", p.read_text(encoding="utf-8"), re.MULTILINE)
            and "labs/flue-zoe-" in p.read_text(encoding="utf-8")}
    assert flue <= set(NODE_UNITS), f"unpinned Flue unit(s): {sorted(flue - set(NODE_UNITS))}"
    for name in ("flue-zoe-brain-2x", "flue-zoe-telegram"):
        assert _directives(UNIT_DIR / f"{name}.service.d" / "50-exit-143.conf") == ["[Service]", "SuccessExitStatus=143"]
