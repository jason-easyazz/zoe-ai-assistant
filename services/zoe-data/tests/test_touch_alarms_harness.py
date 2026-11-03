"""CI wrapper: run the panel alarms node harness (ZOE_ALARMS; dist/test_touch_alarms.js).

Pins, against the REAL js/touch-alarms.js (no browser, no network): a ring takes the sleep page, tap ANYWHERE snoozes, Turn off is
press-and-hold 1 s (a tap or an early release neither snoozes nor turns off), no snoozes left = tap does nothing, a snooze that
could not reach the server is retried and not re-rung, a ring the server stops listing is removed, a 404 hides the tabs, the
editor saves the right body, and no server string is ever parsed as HTML.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_alarms_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_alarms.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
