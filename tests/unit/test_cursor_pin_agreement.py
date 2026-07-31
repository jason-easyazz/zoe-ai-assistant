"""The cursor-agent pin is declared in TWO places; nothing but this test keeps them equal.

`scripts/setup/install_cursor_agent.sh` (`CURSOR_PINNED_VERSION` — the host provisioner
that downloads, verifies, and symlinks the pinned binary) and `modules/omnigent/entrypoint.sh`
(`DECLARED_PIN` — the container's fallback when the host symlink is absent or unverified)
each name the version independently. They are only ever correct together: if one is bumped
without the other, the container's repo-declared fallback pin diverges from what the host
actually provisions, and — per the entrypoint's fail-closed contract — a stale or
below-minimum `DECLARED_PIN` either links a build omnigent no longer supports or, once
neither source verifies, silently disables the Cursor harness (no crash, just a WARNING).

This is exactly the risk the sibling `test_harness_pin_agreement.py` pins for the Pi
coding-agent version; see that module's docstring for the same failure shape.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _installer_pin() -> str:
    txt = (REPO / "scripts/setup/install_cursor_agent.sh").read_text()
    m = re.search(r'^CURSOR_PINNED_VERSION="([^"]+)"', txt, re.MULTILINE)
    assert m, "no CURSOR_PINNED_VERSION found in scripts/setup/install_cursor_agent.sh"
    return m.group(1)


def _entrypoint_pin() -> str:
    txt = (REPO / "modules/omnigent/entrypoint.sh").read_text()
    m = re.search(r'^DECLARED_PIN="([^"]+)"', txt, re.MULTILINE)
    assert m, "no DECLARED_PIN found in modules/omnigent/entrypoint.sh"
    return m.group(1)


def test_cursor_pin_is_identical_in_both_places():
    installer, entrypoint = _installer_pin(), _entrypoint_pin()
    assert installer == entrypoint, (
        f"cursor-agent pin drift — install_cursor_agent.sh CURSOR_PINNED_VERSION={installer!r} "
        f"entrypoint.sh DECLARED_PIN={entrypoint!r}"
    )
