"""CI wrapper: the two-panel targeting node harnesses (A11, architecture deep dive 2026-10-18).

Pins, against the REAL js/touch-ui-executor.js (no browser, no network):
  * dist/test_touch_panel_targeting.js - two kiosks behind a push.py-shaped fake server: a turn on panel A (sent on
    panel_panel-a, or on "all" naming panel-a) shows on A only; voice:* naming no panel is acted on by neither; a
    household-wide ui_action with no panel id still reaches both; a negative control proves the check sees the leak;
  * dist/test_touch_panel_match.js - the matchers themselves: aliases match, a foreign id is refused, the strict
    (per-panel) matcher refuses an absent id, and a browser with no identity claims no addressed frame.
The server half is tests/test_panel_targeting.py.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("harness,marker", [
    ("test_touch_panel_targeting.js", "checks passed"),
    ("test_touch_panel_match.js", "all assertions passed"),
])
def test_touch_panel_targeting_node_harness(harness, marker):
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    proc = subprocess.run([node, str(ROOT / "zoe-ui" / "dist" / harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert marker in proc.stdout
