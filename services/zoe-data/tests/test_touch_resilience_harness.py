"""CI wrapper: estate resilience (UI deep review wave 6) — dist/test_touch_resilience.js.

Pins, without a browser: lists keep the last good board when every type fails (never
"no lists" over a deploy restart), the timers fetch is shared across its three callers and
a failure is not memoised, the HA entity load is shared across the dock/rooms/sleep pollers,
the dock music chain re-arms on a failed poll, Music Assistant being down reads as
"isn't available" rather than "nothing playing", and the Sources tab tells auth apart from
failure. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_touch_resilience.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the estate resilience harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_touch_resilience_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"estate resilience harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
    assert "share ONE fetch" in proc.stdout and "keep the last good board" in proc.stdout
