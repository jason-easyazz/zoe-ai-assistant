"""CI wrapper: run the dist/voice.html node harness (the REAL page code against stub localStorage/fetch/audio).

The server-side gate is deterministic and tested in `test_livekit_media_authz.py`; this is the OTHER half: the page still
gets a working voice turn. It pins the LiveKit mode's guest mint, single bounded retry and no-silent-downgrade rule, and the
local push-to-talk lane (A4, 2026-10-19): one 16 kHz mono PCM WAV per turn to POST /api/voice/turn, members only, and the
retired /ws/voice/ socket never opened.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_voice_livekit_session_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the LiveKit voice-session harness")
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_voice_livekit_session.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
