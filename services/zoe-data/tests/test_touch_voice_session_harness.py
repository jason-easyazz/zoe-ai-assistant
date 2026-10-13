"""CI wrapper: run the panel voice-session node harness (ZOE_VOICE_SESSION).

Pins, against the REAL js/touch-ui-executor.js and js/touch-voice-session.js (no browser, no network):
  * a voice_session / voice_card push frame is relayed only when it names THIS panel (never 'any panel');
  * the "<name> · voice" badge shows while the session is open; a private card shows the real rows as text only;
  * a card for another member is ignored, and every private card disappears when the session closes;
  * the privacy-safe card carries no rows; home.html loads the script.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_voice_session_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_voice_session.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
