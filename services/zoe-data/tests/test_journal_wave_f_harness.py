"""CI wrapper: the Wave-F journal repairs.

Validated against the real pre-fix files from origin/main (journal.html fails
2 of 6 checks there, journal-ui-enhancements.js fails a third), so this is not
a synthetic guard.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_journal_wave_f_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        # Skip is fine on a dev box; on CI a silent skip means these checks
        # stop running while the build still goes green.
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the journal UI harness")
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_journal_wave_f.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_journal_update_route_still_exists():
    """The Edit button now depends on PUT /api/journal/{entry_id}. Pin it so a
    server change surfaces here rather than as a silent failure in the UI --
    the route's existence was previously mis-documented in the page itself.
    """
    src = (ROOT / "zoe-data" / "routers" / "journal.py").read_text(encoding="utf-8")
    assert '@router.put("/{entry_id}"' in src
    models = (ROOT / "zoe-data" / "models.py").read_text(encoding="utf-8")
    assert "class JournalEntryUpdate" in models


def test_weather_router_owns_location_search():
    """Location autocomplete now calls /api/weather/location/search."""
    src = (ROOT / "zoe-data" / "routers" / "weather.py").read_text(encoding="utf-8")
    assert 'prefix="/api/weather"' in src
    assert '@router.get("/location/search"' in src
