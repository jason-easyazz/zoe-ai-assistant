"""Pins ``SuccessExitStatus=143`` on the Node-hosted Flue units.

The Flue servers trap SIGTERM and exit with code 143 (128+15). systemd counts only
0 / death-by-SIGTERM as a clean stop, so without ``SuccessExitStatus=143`` every
stop or restart (every deploy) logs ``Main process exited, code=exited,
status=143`` + ``Failed with result 'exit-code'`` and flips the unit to *failed*
for an instant. Counted in the journal 2026-09-27 -> 2026-10-04: at least 29 false
failures on flue-zoe-brain-2x / flue-zoe-telegram — noise that buries a REAL crash
(any other exit status) in the failed-units list. ``Restart=always`` is independent
of this directive, so crash recovery is unchanged.
See docs/knowledge/log-review-units-2026-10-04.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

UNIT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "systemd"
NODE_UNITS = ("flue-zoe-brain-2x.service", "flue-zoe-telegram.service", "flue-executor.service")


def _directives(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


@pytest.mark.parametrize("name", NODE_UNITS)
def test_node_units_treat_sigterm_exit_143_as_success(name):
    lines = _directives((UNIT_DIR / name).read_text(encoding="utf-8"))
    assert any(re.fullmatch(r"SuccessExitStatus=.*\b143\b.*", ln) for ln in lines), (
        f"{name} lost SuccessExitStatus=143 — every deploy restart will log a false 'Failed with result'"
    )
    # Crash recovery must stay independent of the clean-exit mapping.
    assert "Restart=always" in lines


def test_every_node_hosted_unit_is_covered():
    """A new Flue unit (node, run from labs/flue-*) must be added to NODE_UNITS."""
    node_hosted = {
        p.name
        for p in UNIT_DIR.glob("*.service")
        if re.search(r"^ExecStart=.*/node\b", p.read_text(encoding="utf-8"), re.MULTILINE)
        and "labs/flue-" in p.read_text(encoding="utf-8")
    }
    assert node_hosted <= set(NODE_UNITS), (
        f"node-hosted unit(s) {sorted(node_hosted - set(NODE_UNITS))} not pinned for SuccessExitStatus=143"
    )


@pytest.mark.parametrize("name", ("flue-zoe-brain-2x", "flue-zoe-telegram"))
def test_tracked_dropin_carries_the_same_mapping(name):
    """The drop-in is how the fix reaches an INSTALLED unit (no template copy)."""
    lines = _directives((UNIT_DIR / f"{name}.service.d" / "50-exit-143.conf").read_text(encoding="utf-8"))
    assert lines == ["[Service]", "SuccessExitStatus=143"]
