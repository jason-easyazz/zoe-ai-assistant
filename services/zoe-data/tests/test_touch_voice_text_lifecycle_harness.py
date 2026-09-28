"""CI wrapper: run the panel voice-text lifecycle node harness.

Pins the two 2026-09-28 panel fixes against the REAL touch/home.html estate
script and js/touch-ui-executor.js (no browser, no network):
  * the push socket keeps itself alive and reconnects, so chat/"let's talk"
    replies (which reach the kiosk ONLY over it) are shown;
  * a voice answer auto-dismisses ~10 s after an unanswered follow-up window,
    is held for the life of an open "let's talk" conversation, and a new turn
    resets the clock.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_voice_text_lifecycle_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_voice_text_lifecycle.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
