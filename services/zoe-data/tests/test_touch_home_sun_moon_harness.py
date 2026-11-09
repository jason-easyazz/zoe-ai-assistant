"""CI wrapper: the touch home time/temp alignment contract and the sun/moon beside the temp.

Pins, against the REAL CEL block and CSS of dist/touch/home.html (no browser, no network): the sun is on the horizon at sunrise/sunset and highest at
solar noon, the moon does the same between sunset and sunrise, a stale or missing weather feed still places it, the moon's phase comes from the date,
the time starts at the orb's left edge and the temp mirrors it on the right, the icon cannot leave its band, nothing animates on first seating.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_home_sun_moon_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_home_sun_moon.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
