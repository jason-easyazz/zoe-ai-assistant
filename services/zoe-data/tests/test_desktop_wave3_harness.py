"""CI wrapper: run the desktop wave-3 harness (UI deep review 2026-10-04).

The node harness pins, without a browser: the synchronous desktop auth gate in
js/auth.js (no member session → /index.html before any page init; a guest is not
signed in on desktop), the ONE push socket per page (session_id, backoff, ping; no
page opens its own), the ?redirect= guard in auth.html, the post-login destination
in index.html, the DOM-built orb suggestion toast and the escaped people search
results (two stored-XSS sinks), and push-notifications.js (403 VAPID → null, the
`subscription` declaration, no Bearer, guests never auto-subscribe).

FAILS rather than skips when node is missing on CI: a skip is indistinguishable
from a pass and this is the only automated check on these defects.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_desktop_wave3.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the desktop wave-3 harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_desktop_wave3_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"desktop wave-3 harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
    for must in (
        "gate: data page + GUEST session",
        "hub: member session → ONE socket",
        "redirect: javascript: is refused",
        "orb toast: hostile title/message are escaped",
        "people search: hostile name/category/id are escaped",
        "push: a 403 VAPID response resolves null",
    ):
        assert must in proc.stdout, must
