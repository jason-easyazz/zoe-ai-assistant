"""CI wrapper: the estate reloads itself when idle, and a pushed voice navigate is acked.

Runs services/zoe-ui/dist/test_touch_page_fresh.js against the REAL touch/home.html FRESH block and the REAL push
handler of js/touch-ui-executor.js (no browser, no network). The harness carries its own negative controls (A6, B3).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_page_fresh_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_page_fresh.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "9 checks passed" in proc.stdout


def test_the_estate_loads_the_executor_that_acks_navigates():
    """The cache key moved with the ack, so a panel never pairs the new server check with a cached old executor."""
    html = (ROOT / "zoe-ui" / "dist" / "touch" / "home.html").read_text(encoding="utf-8")
    assert "/js/touch-ui-executor.js?v=estate-12-nav-ack" in html
