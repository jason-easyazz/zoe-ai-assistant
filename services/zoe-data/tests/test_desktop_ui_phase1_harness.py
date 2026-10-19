"""CI wrapper: desktop UI "just needs to work", phase 1 (2026-10-19).

Runs dist/test_desktop_ui_phase1.js: the fake developer dashboard stays retired (no files,
no More-menu link, no nginx location), dist/ root holds no stray files, every page a desktop
page navigates to exists, desktop Settings carries no OpenClaw/Hermes section and pairs a
panel in place (no /touch/pair.html window), chat cannot force a turn into a retired runtime,
calendar leaves notifications to the shared panel, and journal makes no /api/journeys call
and ships no fake entries or third-party images. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_desktop_ui_phase1.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the desktop UI phase-1 harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_desktop_ui_phase1_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"desktop UI phase-1 harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "desktop UI phase 1: 8 checks passed" in proc.stdout
