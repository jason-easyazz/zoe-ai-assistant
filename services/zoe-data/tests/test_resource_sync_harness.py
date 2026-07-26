"""CI wrapper: desktop resource sync must poll, not open dead WebSockets.

The six per-resource sockets never connected for any user (no session_id in the
URL; a browser cannot set X-Session-ID on a WebSocket), and their polling
fallback was unreachable because maxReconnectAttempts defaults to 0 meaning
unlimited. Net: a failed-handshake storm and no data-change signal at all.

Validated against the real pre-fix file (4 of 6 checks fail there).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_resource_sync_polls_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the resource-sync harness")
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_resource_sync_polls.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_ws_routes_still_require_session_id():
    """If the server ever stops requiring session_id on these routes, sockets
    become viable again and this whole change should be revisited.
    """
    main = (ROOT / "zoe-data" / "main.py").read_text(encoding="utf-8")
    assert 'websocket.query_params.get("session_id")' in main, (
        "the per-resource WS routes no longer read session_id — re-evaluate "
        "whether polling is still the right call"
    )
