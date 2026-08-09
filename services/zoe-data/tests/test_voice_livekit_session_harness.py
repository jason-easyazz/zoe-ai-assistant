"""CI wrapper: run the LiveKit voice-page session node harness (the REAL helper
block extracted from dist/voice.html and dist/touch/voice.html against stub
localStorage/fetch).

The server-side gate is deterministic and tested in
`test_livekit_media_authz.py`; this is the OTHER half of the acceptance
constraint — the panel and the desktop page must still get a working voice turn
once the endpoints stop answering anonymous callers. It pins the guest mint, the
single bounded retry, and the no-silent-downgrade rule.
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
