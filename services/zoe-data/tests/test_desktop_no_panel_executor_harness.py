"""CI wrapper: desktop pages must not register themselves as touch panels.

touch-ui-executor.js has no pathname guard, so on every desktop page that
loaded it, init() minted a fake panel_<random> id, POSTed /api/ui/panel/bind,
started 2s+5s timers, opened a second /ws/push, and asked the service worker to
poll every 5s -- a poll that outlives the page. The estate legitimately needs
it, so the harness asserts the SPLIT, not deletion.

Validated against the real pre-fix tree (3 of 6 checks fail there).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_desktop_no_panel_executor_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the desktop panel-executor harness")
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_desktop_no_panel_executor.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_sw_still_handles_stop_panel_poll():
    """common.js's cleanup is useless if the SW stops understanding the message."""
    sw = (ROOT / "zoe-ui" / "dist" / "sw.js").read_text(encoding="utf-8")
    assert "STOP_PANEL_POLL" in sw
    assert "START_PANEL_POLL" in sw
