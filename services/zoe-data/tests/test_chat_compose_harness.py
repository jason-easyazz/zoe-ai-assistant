"""CI wrapper: run the chat compose-bridge node harness (real chat.html helpers +
the real zoe-compose renderer). Repo rule: AG-UI-touching changes carry tests."""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_chat_compose_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_chat_compose_render.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_zoe_compose_version_matches_touch_panel():
    """chat.html and skybridge.html share zoe-compose.js; their ?v cache-busters
    must move together or offline-first chat clients serve a stale renderer."""
    import re
    chat = (ROOT / "zoe-ui" / "dist" / "chat.html").read_text(encoding="utf-8")
    touch = (ROOT / "zoe-ui" / "dist" / "touch" / "skybridge.html").read_text(encoding="utf-8")
    cv = re.search(r"zoe-compose\.js\?v=([\w.-]+)", chat)
    tv = re.search(r"zoe-compose\.js\?v=([\w.-]+)", touch)
    assert cv and tv, "both pages must load the shared zoe-compose.js with a ?v"
    assert cv.group(1) == tv.group(1), f"version drift: chat={cv.group(1)} touch={tv.group(1)}"
