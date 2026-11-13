"""CI wrapper: the wall panel's home screen tells the truth and needs no keyboard (gap audit 2026-11-13, lane A).

Runs dist/test_touch_home_honest_panel.js: the REAL ZFETCH block of touch/home.html in a vm (one deadline per
request, one "Can't reach Zoe" line while requests fail, cleared by the next answer; plain failure lines; one
load-failure wording), plus page checks with planted-fault negative controls (no typing dialogs, no silent taps,
the person-introduce arrival, the timer-done line in Zoe's voice, wall-readable sizes, chat session id, tappable
agenda rows, and the Settings screen's Voice ID / Pendant / Notifications rows and guest state). No browser, no network.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_home_honest_panel_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_home_honest_panel.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
