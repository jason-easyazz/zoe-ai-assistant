"""Every API path ``touch/smart-home.html`` fetches must be a route zoe-data actually serves (2026-10-18).

The page called ``/api/ha/states``, ``/api/ha/areas`` and ``/api/ha/scene``, none of which ever existed, so it answered 404 and
fell back to demo devices. It now uses ``/api/ha/entities``, ``/api/rooms`` and ``/api/ha/control``. This reads the page as text
and matches each fetched path against the REAL routers' route table (no browser, no server).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from starlette.routing import Match

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routers import ha_control, rooms

pytestmark = pytest.mark.ci_safe

PAGE = ROOT.parent / "zoe-ui" / "dist" / "touch" / "smart-home.html"
FETCH = re.compile(r"fetch\(\s*'(/api/[^'?]+)'(?:\s*,\s*\{\s*method:\s*'([A-Z]+)')?")


def _fetches():
    return [(m.group(1), m.group(2) or "GET") for m in FETCH.finditer(PAGE.read_text(encoding="utf-8"))]


def _served(path: str, method: str) -> bool:
    scope = {"type": "http", "path": path, "method": method, "root_path": ""}
    for router in (ha_control.router, rooms.router):
        for route in router.routes:
            match, _ = route.matches(scope)
            if match is Match.FULL:
                return True
    return False


def test_the_page_fetches_only_routes_that_exist():
    calls = _fetches()
    assert ("/api/ha/entities", "GET") in calls and ("/api/rooms", "GET") in calls  # the parser sees the page
    missing = [(p, m) for p, m in calls if not _served(p, m)]
    assert missing == [], f"smart-home.html calls routes zoe-data does not serve: {missing}"


def test_actions_send_their_data_in_a_field_the_control_route_reads():
    # HAControlPayload reads `params` / `service_data`; an unknown key (the page used `value`) is accepted and DROPPED,
    # so brightness and temperature silently did nothing.
    text = PAGE.read_text(encoding="utf-8")
    assert "value: {" not in text
    assert "params: { brightness_pct" in text and "params: { temperature" in text
