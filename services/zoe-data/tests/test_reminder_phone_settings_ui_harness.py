"""CI wrapper: desktop Settings > Telegram > Reminders on my phone (dist/js/reminder-phone-settings.js + settings.html; record
docs/knowledge/reminder-phone-delivery.md).

Runs dist/test_reminder_phone_settings.js (node + a fake root + a stubbed fetch, no browser): state from the GET, hidden when Telegram
is not linked, the documented PUT body, full_text mapping, web_push kept, no "Saved" before the server answered, a failed PUT reverts,
a 403 (not an adult) reverts and says so, desktop-only wiring. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_reminder_phone_settings.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the reminder phone settings UI harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_reminder_phone_settings_ui_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"reminder phone settings UI harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
