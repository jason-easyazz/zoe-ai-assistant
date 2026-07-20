"""CI wrapper: run the chat.html XSS + sw.js cross-origin guard.

Without this wrapper the node harness only runs when someone invokes it by hand,
so re-introducing either defect would sail through CI:

  * the two stored-XSS sinks in chat.html (session titles derived server-side
    from raw user message text, and user-authored reminder text) — note chat.html
    carries TWO copies of displayNotifications and the harness asserts on both;
  * the sw.js routes that sent cross-origin no-cors scripts/styles through
    NetworkFirst, stripping all 9 of chat.html's CDN assets on every reload.

Unlike test_sw_image_route_harness.py this FAILS rather than skips when node is
missing on CI: a silent skip is indistinguishable from a pass, and this guard is
the only automated check on either defect.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_chat_xss_and_sw_origin.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail(
                "Node.js is not installed on this CI host, so the chat XSS / sw "
                "cross-origin guard cannot run. A skip here would hide a real "
                "regression — install node on the runner instead."
            )
        pytest.skip("Node.js is not installed on this host")
    return node


def test_chat_xss_and_sw_origin_guard():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run(
        [_node(), str(HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, (
        f"chat XSS / sw origin guard failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "checks passed" in proc.stdout
    # Both displayNotifications copies must actually have been exercised.
    assert "displayNotifications #2 routes notification.message through escapeHtml" in proc.stdout
