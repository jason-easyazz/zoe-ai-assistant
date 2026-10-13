"""CI wrapper: the wall panel's push socket always self-heals — dist/test_touch_push_reconnect.js.

Runs the REAL js/touch-ui-executor.js (whole file, real init()) and js/websocket-sync.js
initPush() against a fake clock, fake sockets and a fake server that can drop, refuse, hang or go
silent: reconnect within 31 s of the server coming back after an hour of refusals (guest and
member, fallback and shared channel, never capped); a CANCELLED navigation (beforeunload with the
page staying) no longer kills the poll/watchdog/reconnect; an OPEN-but-silent socket is replaced
by the heartbeat; online / visible reconnect at once. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_touch_push_reconnect.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the push reconnect harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_touch_push_reconnect_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"push reconnect harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
    assert "a cancelled navigation leaves poll, watchdog and push reconnect alive" in proc.stdout
    assert "OPEN-but-silent socket is replaced" in proc.stdout
