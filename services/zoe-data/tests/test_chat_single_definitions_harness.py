"""CI wrapper: every function in chat.html's inline scripts is declared once.

chat.html declared eight functions twice (the notification helpers in two
scripts, where the later copy was the live one, and escapeAttr twice in one
script, where the weaker copy won by hoisting). The node harness fails on any
repeated declaration, runs the surviving escapeAttr / displayNotifications, and
carries its own negative controls.

Fails rather than skips when node is missing on CI: a silent skip is
indistinguishable from a pass.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_chat_single_definitions.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail(
                "Node.js is not installed on this CI host, so the chat.html "
                "single-definition guard cannot run. Install node on the runner."
            )
        pytest.skip("Node.js is not installed on this host")
    return node


def test_chat_declares_each_function_once():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run(
        [_node(), str(HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, (
        f"chat.html single-definition guard failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "checks passed" in proc.stdout
    # The negative controls must have run, or a blind scanner would pass too.
    assert "PASS control: a second displayNotifications in a later script is caught" in proc.stdout
    assert "PASS control: a second escapeAttr in the same script is caught" in proc.stdout
