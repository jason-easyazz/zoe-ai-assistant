"""CI wrapper: run the lists two-step node harness (the REAL js/common.js helper).

Guards the Wave-F fix for the highest-leverage desktop breakage: `GET
/api/lists/{type}` returns list rows WITHOUT items (routers/lists.py:76-89), so
three surfaces that read `list.items` off it silently rendered wrong — an empty
calendar task sidebar, "0 items" on every list card, and a permanent "All tasks
completed!". All three returned HTTP 200, which is why it went unnoticed.

The harness is mutation-proven: reverting the helper to a single-step fetch, or
downgrading an unloadable list from null to [], both turn it red.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_lists_two_step_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_lists_items_two_step.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_lists_collection_route_still_omits_items():
    """If the server ever starts returning items on the collection route, the
    client-side two-step becomes redundant — fail loudly rather than let it rot.
    """
    lists_router = ROOT / "zoe-data" / "routers" / "lists.py"
    src = lists_router.read_text(encoding="utf-8")
    # The collection handler's SELECT — items must NOT appear in it.
    marker = 'SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at'
    assert marker in src, (
        "the /api/lists/{list_type} collection SELECT changed; re-check whether "
        "zoeFetchListsWithItems in js/common.js is still needed"
    )
