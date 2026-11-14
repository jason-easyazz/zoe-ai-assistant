"""CI wrapper: a chat component's props stay inside their attribute (chat.html).

chat.html's escapeHtml() did not escape quotes and the generative-UI renderers
used it for attribute values, inside inline handlers and for link targets. The
node harness renders every such component with plain and with hostile props,
compares the tags and attribute names, clicks the bound listeners, and carries
negative controls that put each old defect back.

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
HARNESS = ROOT / "zoe-ui" / "dist" / "test_chat_component_attributes.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail(
                "Node.js is not installed on this CI host, so the chat component "
                "attribute guard cannot run. Install node on the runner."
            )
        pytest.skip("Node.js is not installed on this host")
    return node


def test_chat_component_props_stay_inside_their_attribute():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run(
        [_node(), str(HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, (
        f"chat component attribute guard failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "checks passed" in proc.stdout
    # The negative controls must have run, or a blind suite would pass too.
    for control in (
        "PASS control: escapeHtml without quotes, used in attributes, is caught",
        "PASS control: yes_action written into an onclick is caught",
        "PASS control: a link that keeps any scheme is caught",
    ):
        assert control in proc.stdout
