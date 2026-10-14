"""CI wrapper: the panel's calendar event-detail sheet (no browser, no network).

Owner feedback 2026-10-14: tapping an event on the touch panel opened a cramped form
whose Category native <select> was unreadable on the dark kiosk, and the details should
be near-full-screen. The node harness pins, against the REAL js/touch-event-detail.js and
the real touch/home.html text, that the sheet renders present fields and omits empty
ones (today's minimal calendar row AND a fully-populated synced-calendar-shaped event),
that categories are AA-contrast chips with no native select / text input anywhere, that
saving PUTs only what changed (never title/location/metadata), that read-only events
hide edit/delete, and the spoken-close / swipe-down decisions. It also runs the same
suite against deliberately broken copies of the module, which must each go red.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_event_detail_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_event_detail.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
