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
