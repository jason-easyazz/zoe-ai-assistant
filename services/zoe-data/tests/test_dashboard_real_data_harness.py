"""CI wrapper: the desktop dashboard shows REAL data (desktop Wave 4b, 2026-10-19).

Runs dist/test_dashboard_real_data.js: no widget or dashboard file calls Math.random; the
System card renders a loading state, the server's numbers from /api/system/stats, and an
honest unavailable / sign-in / stale state; the Home card renders this house's rooms and
switchable devices from /api/rooms + /api/ha/entities (or an empty / unavailable state, never
four fixed rooms); the Weather card carries no placeholder temperature; two cards of one type
are two widget instances with distinct uids in the saved layout (the duplicate-widget flaw);
the desktop board syncs its layout to /api/dashboard/layout/ while the touch board stays local;
and the cards read the theme tokens dark mode swaps. Every check has a negative control.
Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_dashboard_real_data.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the dashboard harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_dashboard_real_data_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"dashboard real-data harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "dashboard real data: 27/27 checks passed" in proc.stdout
