"""CI wrapper: run the panel review-cards node harness (ZOE_PANEL_REVIEW_CARDS).

Pins, against the REAL js/touch-voice-session.js + js/touch-review-cards.js (no browser, no network): the card token travels
only in the X-Panel-Card header, a row goes only after the server said yes, a refusal keeps it, "Someone new" shows the phone QR
and never a text input, Play fetches the clip with the header, another member's card is never drawn, and the session closing
removes everything.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_review_cards_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_review_cards.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
