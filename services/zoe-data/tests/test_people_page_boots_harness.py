"""CI wrapper: prove people.html's main script still BOOTS.

people.html was 100% dead from 2026-05-18 until the Wave-F fix: its main
<script> called `.getContext('2d')` on a `#peopleCanvas` that does not exist,
throwing on statement two and killing the whole ~1500-line block — including
the DOMContentLoaded handler — against a perfectly healthy /api/people backend.

The harness is validated against the real pre-fix file (git show origin/main),
which fails 4 of its 6 checks, so this is not a synthetic guard.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_people_page_boots_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_people_page_boots.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_people_backend_still_serves_the_grid_fields():
    """The card grid reads circle/health_score off /api/people rows. If the
    serializer stops sending them the grid silently degrades, so pin it here.
    """
    utils = ROOT / "zoe-data" / "people_utils.py"
    src = utils.read_text(encoding="utf-8")
    for field in ("circle", "health_score"):
        assert field in src, (
            f"people_utils no longer mentions {field!r}; people.html's card grid "
            "reads it and will render blank tiles"
        )
