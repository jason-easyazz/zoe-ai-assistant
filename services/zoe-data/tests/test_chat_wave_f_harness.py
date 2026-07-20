"""CI wrapper: the Wave-F chat.html repairs (action_menu, add_to_list,
proactive deep-link, agent-activity reattach).

Each assertion is validated against the real pre-fix files from origin/main:
chat.html fails 4 of 6 checks there and agent-activity.js fails the 5th, so
this is not a synthetic guard. One assertion is deliberately file-wide — it
found a SECOND live instance of the broken onclick pattern (renderPriceTable)
that the audit had not flagged.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_chat_wave_f_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        # A silent skip on CI means the action-menu / proactive-session /
        # agent-activity regressions stop being covered while the build still
        # goes green. Skip is acceptable on a dev box; on CI it is a failure.
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the chat UI harness")
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_chat_wave_f_fixes.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_list_item_create_route_shape_unchanged():
    """add_to_list now posts ListItemCreate{text,priority} to
    /{list_type}/{list_id}/items. Pin the route so a server change surfaces here
    rather than as a silent 422 in the UI again.
    """
    src = (ROOT / "zoe-data" / "routers" / "lists.py").read_text(encoding="utf-8")
    assert '@router.post("/{list_type}/{list_id}/items")' in src
    models = (ROOT / "zoe-data" / "models.py").read_text(encoding="utf-8")
    assert "class ListItemCreate" in models and "text: str" in models
