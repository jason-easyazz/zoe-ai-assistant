"""CI wrapper: the lists page must default to real LIST widgets.

Reported by Jason 2026-07-20 ("my lists aren't listed in list") and reproduced
in a browser: the grid held one widget, "Project", showing 0, and the page made
zero /api/lists calls.

createDefaultLayout() used getAvailableWidgets('lists'), which filters on
`w.lists === true` -- and in widget-manifest.json only 'project' carries that
flag. So the default was [project] (a stub-backed dead wing), and because the
array was non-empty the shopping/work/personal fallback never fired.

Validated against the real pre-fix file (2 of 4 checks fail there).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_lists_default_layout_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the lists default-layout harness")
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_lists_default_layout.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_projects_route_is_still_a_stub():
    """'project' is excluded from the lists default because its backend returns
    an empty stub. If that ever becomes real, revisit the exclusion.
    """
    stubs = (ROOT / "zoe-data" / "routers" / "stubs.py").read_text(encoding="utf-8")
    assert "/api/projects" in stubs or "projects" in stubs, (
        "the projects stub moved; re-check whether 'project' should still be "
        "excluded from the lists default layout"
    )
