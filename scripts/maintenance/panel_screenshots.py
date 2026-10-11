#!/usr/bin/env python3
"""Capture README screenshots of the touch panel UI from INVENTED demo data.

Safety model (read before changing anything):
  * Serves ``services/zoe-ui/dist`` from a throwaway local static server and
    drives ONE headless Chromium at the panel's real 1280x720.
  * NO request ever reaches a Zoe backend. Every ``/api/**`` call is answered by
    a fixture in this file (an invented household: Alex, Sam, Juniper the dog);
    a path without a fixture is answered 404 and printed, and every non-local
    request is aborted. So nothing real can appear and nothing needs tearing
    down. The demo identity is ``demo_bar_0d3a1c7e`` (it is never sent anywhere).
  * Run:  python3 scripts/maintenance/panel_screenshots.py [outdir]
"""
from __future__ import annotations

import datetime as dt
import functools
import http.server
import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "services" / "zoe-ui" / "dist"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "images" / "panel"
VIEW = {"width": 1280, "height": 720}
USER = "demo_bar_0d3a1c7e"

# A fixed instant (a Saturday morning, UTC) so every shot is reproducible.
NOW = dt.datetime(2026, 10, 10, 9, 41, 0)
TODAY = NOW.date()
NOW_MS = int(NOW.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def day(n: int) -> dt.date:
    return TODAY + dt.timedelta(days=n)


def event(i, title, d, hh, mm=0, dur=60, cat="family", loc=""):
    return {
        "id": str(i), "user_id": USER, "title": title, "start_date": d.isoformat(),
        "start_time": f"{hh:02d}:{mm:02d}:00", "end_date": None, "end_time": None,
        "duration": dur, "category": cat, "location": loc, "all_day": False,
        "recurring": None, "metadata": None, "visibility": "family", "deleted": False,
        "created_at": "2026-10-01T00:00:00", "updated_at": "2026-10-01T00:00:00",
    }


EVENTS = [
    event(1, "Juniper - vet check-up", day(0), 11, 30, 45, "health"),
    event(2, "Sam's swimming lesson", day(0), 15, 0, 60, "family"),
    event(3, "Dinner with the Parkers", day(0), 18, 30, 120, "personal"),
    event(4, "Team stand-up", day(1), 9, 0, 30, "work"),
    event(5, "Farmers market", day(1), 10, 0, 90, "personal"),
    event(6, "Parent-teacher evening", day(2), 17, 30, 60, "family"),
    event(7, "Dentist - Alex", day(3), 8, 30, 45, "health"),
    event(8, "Book club", day(4), 19, 0, 120, "personal"),
    event(9, "Sam's birthday party", day(6), 14, 0, 180, "family"),
    event(10, "Plumber visit", day(7), 10, 0, 60, "general"),
    event(11, "Juniper - grooming", day(9), 13, 0, 60, "general"),
]

SHOPPING = [
    ("Oat milk", False), ("Sourdough loaf", False), ("Bananas", False),
    ("Dog food - Juniper's chicken mix", False), ("Pasta sauce", False),
    ("Tea bags", False), ("Bin bags", False), ("Lemons", True), ("Eggs", True),
]
JOBS = [
    ("Wash the car", False), ("Book Juniper's grooming", True), ("Fix the garden gate", False),
    ("Plan Sam's birthday party", False), ("Return library books", True),
]
LISTS = {
    "shopping": [{"id": "l1", "name": "Shopping", "list_type": "shopping"}],
    "personal_todos": [{"id": "l2", "name": "Weekend jobs", "list_type": "personal_todos"}],
}
ITEMS = {
    "l1": [{"id": f"i{n}", "text": t, "completed": c} for n, (t, c) in enumerate(SHOPPING)],
    "l2": [{"id": f"j{n}", "text": t, "completed": c} for n, (t, c) in enumerate(JOBS)],
}

TIMERS = {"timers": [
    {"id": "t1", "label": "Pasta", "duration_s": 600, "expires_at_ms": NOW_MS + 6 * 60_000 + 12_000},
    {"id": "t2", "label": "Laundry", "duration_s": 2700, "expires_at_ms": NOW_MS + 31 * 60_000},
]}

REMINDERS = {"reminders": [
    {"id": "r1", "title": "Pick up Sam from swimming", "due_date": day(0).isoformat(), "due_time": "16:00:00", "completed": False},
    {"id": "r2", "title": "Give Juniper her tablet", "due_date": day(0).isoformat(), "due_time": "18:00:00", "completed": False},
    {"id": "r3", "title": "Call Gran about Sunday", "due_date": day(1).isoformat(), "due_time": "10:30:00", "completed": False},
]}

PEOPLE = {"people": [
    {"id": "p1", "name": "Sam", "relationship": "child"},
    {"id": "p2", "name": "Gran", "relationship": "family"},
    {"id": "p3", "name": "Priya Parker", "relationship": "friend"},
]}

WEATHER = {"temp": 21, "feels_like": 20, "description": "sunny", "icon": "01d", "city": ""}

SESSION = {"session_id": "demo-session", "user_id": USER, "username": "Alex", "role": "user",
           "expires_at": "2099-01-01T00:00:00Z"}

def _ent(eid, state, name, icon=None, **attrs):
    a = {"friendly_name": name}
    if icon:
        a["icon"] = icon
    a.update(attrs)
    return {"entity_id": eid, "state": state, "attributes": a}


HA = {"count": 8, "entities": [
    _ent("light.living_room", "on", "Living Room Light"),
    _ent("light.kitchen", "on", "Kitchen Light"),
    _ent("light.hallway", "off", "Hallway Light"),
    _ent("input_boolean.reading_lamp", "off", "Reading Lamp"),
    _ent("input_boolean.ceiling_fan", "on", "Ceiling Fan", "mdi:fan"),
    _ent("climate.house", "heat_cool", "House", current_temperature=21, temperature=22,
         min_temp=16, max_temp=28, hvac_modes=["off", "heat", "cool", "heat_cool"]),
    _ent("scene.movie_night", "scening", "Movie Night"),
    _ent("scene.good_morning", "scening", "Good Morning"),
]}

# Invented music. Cover art is generated SVG served from the local fixture server
# (the page only draws artwork whose URL starts with http/https).
TRACKS = [
    ("Blue Hour", "The Paper Lanterns", "#5b74ff", "#c77bff"),
    ("Slow Mornings", "Marlowe & Finch", "#ff9e6a", "#ff7a90"),
    ("Kitchen Radio", "Juniper Lane", "#3fb98a", "#5ec8f0"),
    ("Porch Light", "Oak Street Trio", "#ffbf5e", "#f0742e"),
    ("Paper Boats", "Hollow Pines", "#6aa6ff", "#4a7de0"),
]
COVER = "/__demo/cover/%d.svg"


def np_payload(base):
    t = TRACKS[0]
    return {"available": True, "now_playing": {
        "player_id": "demo-kitchen", "player_name": "Kitchen", "state": "playing",
        "title": t[0], "artist": t[1], "album": "Lantern Sessions", "image": base + COVER % 0,
        "elapsed": 84, "duration": 227, "volume": 38, "shuffle": False, "repeat": "off",
        "dont_stop": True, "queue_item_id": "q0", "queue_index": 0}}


def queue_payload(base):
    return {"items": [{"queue_item_id": f"q{i}", "index": i, "name": t[0], "title": t[0],
                       "artist": t[1], "image": base + COVER % i, "uri": f"demo://track/{i}",
                       "duration": 200 + i * 7} for i, t in enumerate(TRACKS)]}


def cover_svg(i):
    a, b = TRACKS[i % len(TRACKS)][2:4]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400"><defs>'
            f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/>'
            f'<stop offset="1" stop-color="{b}"/></linearGradient></defs><rect width="400" height="400" fill="url(#g)"/>'
            f'<circle cx="{120 + 40 * (i % 3)}" cy="{150 + 30 * (i % 2)}" r="{90 + 12 * i}" fill="#fff" fill-opacity=".16"/>'
            f'<circle cx="290" cy="290" r="{60 + 8 * i}" fill="#000" fill-opacity=".12"/></svg>')


FORECAST = {"hourly": [
    {"time": f"2026-10-10T{h:02d}:00:00", "temp": t, "icon": ic}
    for h, t, ic in [(10, 21, "02d"), (11, 22, "01d"), (12, 24, "01d"), (13, 25, "02d"), (14, 25, "03d")]]}

VOICE_Q = "Add dog food to the shopping list"

# Per-shot knobs: the dock shows one chip per live thing, so the hero shots keep it sparse.
CFG = {"ha": "one", "timers": 2, "music": True}

# What the README shows. Others (day, timers, reminders, weather, rooms, contacts) can be
# requested by name as argv[2], comma separated.
DEFAULT_SHOTS = ["home", "voice", "lists", "calendar", "music"]

UNHANDLED: set[str] = set()


def api(base: str, method: str, path: str, query: dict):
    """Return (status, json-able) for an /api path, or None when there is no fixture."""
    if path == "/api/calendar/events":
        a, b = query.get("start_date", [None])[0], query.get("end_date", [None])[0]
        return 200, {"events": [e for e in EVENTS if (not a or e["start_date"] >= a) and (not b or e["start_date"] <= b)]}
    if path.startswith("/api/lists/") and path.endswith("/items"):
        return 200, {"items": ITEMS.get(path.split("/")[4], [])}
    if path.startswith("/api/lists/"):
        return 200, {"lists": LISTS.get(path.split("/")[3], [])}
    if path == "/api/skybridge/timers":
        return 200, {"timers": TIMERS["timers"][:CFG["timers"]]}
    if path == "/api/reminders/":
        return 200, REMINDERS
    if path == "/api/people/":
        return 200, PEOPLE
    if path == "/api/weather/current":
        return 200, WEATHER
    if path == "/api/weather/forecast":
        return 200, FORECAST
    if path == "/api/auth/guest":
        return 200, SESSION
    if path == "/api/ha/entities":
        ents = HA["entities"] if CFG["ha"] is True else HA["entities"][:1] if CFG["ha"] == "one" else []
        return 200, {"count": len(ents), "entities": ents}
    if path == "/api/music/now-playing":
        return 200, np_payload(base) if CFG["music"] else {"available": True, "now_playing": None}
    if path.startswith("/api/music/queue/"):
        return 200, queue_payload(base)
    if path == "/api/rooms":
        return 200, {"rooms": []}
    if path == "/api/panels/default/config":
        return 200, {"pinned": []}
    if path.endswith("/sleep-gate"):
        return 200, {"allow": True}
    if path == "/api/proactive/inbox":
        return 200, {"count": 0}
    if path == "/api/system/display/preferences":
        return 200, {"preferences": {}}
    if path in ("/api/ui/panel/bind", "/api/ui/state/sync", "/api/ui/actions/pending"):
        return 200, {"ok": True, "actions": []}
    return None


def open_surface(page, sid_: str) -> None:
    """Drive the real launcher the way a finger would."""
    page.evaluate("document.getElementById('apps').click()")
    page.wait_for_timeout(500)
    page.click(f'.ltile[data-id="{sid_}"]')
    page.wait_for_timeout(1200)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(DIST), **k)

        def log_message(self, *a, **k):  # silence the per-request log
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    with sync_playwright() as pw:
        chrome = os.environ.get("CHROME_PATH") or next(
            (p for p in ("/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",) if Path(p).exists()), None)
        browser = pw.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        ctx = browser.new_context(viewport=VIEW, timezone_id="UTC", locale="en-US", service_workers="block")
        ctx.add_init_script(
            "try{localStorage.setItem('zoe_session',%s);localStorage.setItem('zoe_kiosk','1');"
            "sessionStorage.setItem('zoe_kiosk','1');}catch(e){}" % json.dumps(json.dumps(SESSION))
        )

        def route(r):
            u = urlparse(r.request.url)
            if u.hostname != "127.0.0.1" or u.port != port:
                r.abort()  # nothing leaves the box
                return
            if u.path.startswith("/__demo/cover/"):
                r.fulfill(status=200, content_type="image/svg+xml",
                          body=cover_svg(int(u.path.rsplit("/", 1)[1].split(".")[0])))
                return
            if not u.path.startswith("/api/") and not u.path.startswith("/ws/"):
                r.continue_()
                return
            hit = api(base, r.request.method, u.path, parse_qs(u.query))
            if hit is None:
                UNHANDLED.add(f"{r.request.method} {u.path}")
                r.fulfill(status=404, content_type="application/json", body="{}")
                return
            r.fulfill(status=hit[0], content_type="application/json", body=json.dumps(hit[1]))

        ctx.route("**/*", route)

        def fresh(**cfg):
            CFG.update({"ha": "one", "timers": 1, "music": False})
            CFG.update(cfg)
            pg = ctx.new_page()
            pg.clock.install(time=NOW.replace(tzinfo=dt.timezone.utc))
            pg.goto(f"{base}/touch/home.html?kiosk=1", wait_until="domcontentloaded")
            pg.wait_for_timeout(1500)
            return pg

        shots = sys.argv[2].split(",") if len(sys.argv) > 2 else DEFAULT_SHOTS

        def want(name):
            return shots is None or name in shots

        def snap(pg, name):
            pg.screenshot(path=str(OUT / f"{name}.png"))
            print("wrote", name)

        def surface(name, sid_, view=None, **cfg):
            if not want(name):
                return
            pg = fresh(**cfg)
            open_surface(pg, sid_)
            # The jukebox QR encodes this page's origin; never show one in a README.
            pg.evaluate("""[...document.querySelectorAll('body *')].filter(e=>!/^(SCRIPT|STYLE)$/.test(e.tagName)&&e.children.length===0&&/Scan to queue/.test(e.textContent)).forEach(e=>{const p=e.parentElement;if(p)p.style.display='none';})""")
            if view:
                pg.click(f'.calviews button[data-v="{view}"]')
                pg.wait_for_timeout(900)
            snap(pg, name)
            pg.close()

        if want("home"):
            pg = fresh(music=True)
            snap(pg, "home")
            pg.close()
        if want("voice"):
            pg = fresh()
            pg.evaluate("ZoeEstateVoice.listening()")
            pg.evaluate("ZoeEstateVoice.transcript(%s)" % json.dumps(VOICE_Q))
            pg.evaluate("ZoeEstateVoice.thinking()")
            pg.wait_for_timeout(700)
            snap(pg, "voice")
            pg.close()
        surface("lists", "list", ha="one")
        surface("calendar", "calendar", "week")
        surface("day", "day")
        surface("timers", "timer")
        surface("reminders", "reminder")
        surface("music", "music", music=True)
        surface("weather", "weather")
        surface("rooms", "rooms", ha=True, music=True)
        surface("contacts", "person")
        browser.close()
    try:  # lossless re-encode; keeps every image well under the 400 KB README budget
        from PIL import Image
        for f in OUT.glob("*.png"):
            Image.open(f).convert("RGB").save(f, optimize=True)
    except ImportError:
        pass
    print("unhandled API paths:")
    for p in sorted(UNHANDLED):
        print("  ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
