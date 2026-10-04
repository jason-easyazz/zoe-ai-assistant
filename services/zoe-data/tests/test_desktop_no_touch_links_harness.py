"""CI wrapper: desktop is desktop, touch is touch (standing rule, 2026-07-20).

Runs dist/test_desktop_no_touch_links.js: no desktop page routes into /touch/*, the
cooking/smart-home stubs and the dead tier stay deleted, notifications-panel.js deep-links
only from a touch surface, the orb purges transcripts on the document-level zoe:logout, the
dashboard pageshow handlers act only on a BFCache restore, offline.html probes /health, and
no desktop call hits a FastAPI trailing-slash 307. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_desktop_no_touch_links.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the desktop no-touch-links harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_desktop_no_touch_links_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"desktop no-touch-links harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
    assert "carry no route into /touch/" in proc.stdout
