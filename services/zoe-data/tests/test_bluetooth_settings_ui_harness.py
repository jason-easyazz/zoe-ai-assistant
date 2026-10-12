"""CI wrapper: the Settings > Bluetooth devices UI (dist/js/bluetooth-settings.js + both Settings pages).

Runs dist/test_bluetooth_settings.js (node + a tiny fake DOM, no browser): every state the household sees (scanning, connecting,
connected, updating with progress, failed with the reason), escaping of panel-supplied text, the firmware update cannot start without
the no-undo acknowledgement and a ready checklist, no cancel once the install is ordered, >= 48 px finger targets on touch, flag-off
renders nothing. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_bluetooth_settings.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the Bluetooth settings UI harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_bluetooth_settings_ui_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"bluetooth settings UI harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
