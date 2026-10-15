"""CI wrapper: run the estate Memories-space node harness.

Pins, against the REAL js/touch-memories-space.js (DOM shim whose innerHTML throws; no browser, no network):
  * a refused read (guest / expired / device-token) shows the sign-in prompt and NO rows, names or counts;
  * a member sees groups with provenance badges, a Forget target per item behind a confirm step, private classes as counts;
  * forget sends nothing before the confirm, removes the row only on success, keeps it on failure, drops to sign-in on 401/403;
  * the rows leave the DOM after the privacy window, and navigation (unmount) drops a late response;
  * home.html: the launcher has Memories and no Ask Zoe tile, while the Ask surface (voice answers, the command bar) stays.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_memories_space_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_memories_space.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
