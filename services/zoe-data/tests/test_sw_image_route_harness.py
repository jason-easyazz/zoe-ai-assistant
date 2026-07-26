"""CI wrapper: run the service-worker image-route guard.

Without this wrapper the node guard only ran when someone invoked it by hand, so
dropping the same-origin predicate would pass CI and break every remote album
cover on the panel again — exactly the regression the guard exists to catch
(Greptile flagged this on PR #1405).

Follows the same shape as test_touch_conversation_harness.py: marker-based
selection, shells out to node, skips cleanly where node isn't installed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_sw_image_route_is_same_origin_only():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_sw_image_route.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"sw image-route guard failed:\n{proc.stdout}\n{proc.stderr}"
    assert "same-origin guard intact" in proc.stdout
