"""CI wrapper: Settings > My pendant (dist/js/omi-pendant-settings.js + both Settings pages; record docs/knowledge/omi-linking.md).

Runs dist/test_omi_pendant_settings.js (node + a tiny fake root, no browser): linked / not linked / last sync, linking off says to ask
the household admin, sync off renders nothing, a household-off setting cannot be turned on (a member only narrows), keep-audio days
capped at the ceiling, Link only on touch with the first-time notice, unlink asks first and warns, escaping, 48 px targets, no text
input. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_omi_pendant_settings.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the My pendant settings UI harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_omi_pendant_settings_ui_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"My pendant settings UI harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
