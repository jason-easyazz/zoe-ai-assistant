"""CI wrapper: run the enrolment-on-the-talk-screen node harness ("Zoe, learn my voice").

Pins, against the REAL js/touch-ui-executor.js and js/touch-voice-enroll.js (no browser, no network):
  * a voice_enroll push frame is relayed only when it names THIS panel (never 'any panel');
  * the overlay renders the phrase as text, the "2 / 5" counter and dots, the recording state with the phrase still up, the tick on accept,
    the gentle re-ask, the saving and closing lines;
  * it clears at once on a clear frame (abort / timeout / shutdown), the closing line expires by itself, the watchdog takes a stale phrase
    down if the daemon dies, a tap hides it; nothing is stored in the browser; home.html loads the script and the executor's ?v= is bumped.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_voice_enroll_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_voice_enroll.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
