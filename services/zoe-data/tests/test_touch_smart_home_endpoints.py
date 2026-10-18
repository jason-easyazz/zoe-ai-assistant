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


# ---- the live house's own controls render and drive (cross-review, codex P1 on #2099) -----------------------------------------
# Fixture = the LIVE /api/ha/entities shape read 2026-10-18 (only the fields the page reads). This house has ZERO light.* and
# ZERO climate.*: its lights/fan/TV are switch.* / input_boolean.*, its thermostat is input_number.*.
LIVE = [
    {"entity_id": "switch.bedroom_1_switch_1", "state": "off", "attributes": {"friendly_name": "Bedroom Light", "icon": ""}},
    {"entity_id": "input_boolean.fan", "state": "off", "attributes": {"friendly_name": "Ceiling Fan", "icon": "mdi:fan"}},
    {"entity_id": "input_boolean.tv", "state": "on", "attributes": {"friendly_name": "TV", "icon": "mdi:television"}},
    {"entity_id": "input_number.thermostat_temperature", "state": "21.0",
     "attributes": {"friendly_name": "Thermostat", "min": 16.0, "max": 30.0, "step": 0.5, "unit_of_measurement": "°C",
                    "icon": "mdi:thermostat", "mode": "box"}},
    {"entity_id": "scene.movie_time", "state": "unknown", "attributes": {"friendly_name": "Movie Time"}},
]

# Runs the page's REAL inline script in a node VM with a minimal DOM: no browser, no network.
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const [page, fixture] = [fs.readFileSync(process.argv[2], 'utf8'), JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))];
const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = scripts.find(s => s.includes('function createDeviceCard'));
const els = {}, posts = [];
function el(id) {
  const e = { id, dataset: {}, className: '', innerHTML: '', textContent: '', hidden: false, children: [],
    classList: { add() {}, remove() {}, toggle() { return true; }, contains() { return false; } },
    appendChild(c) { this.children.push(c); }, querySelector() { return null; }, querySelectorAll() { return []; } };
  if (id) els[id] = e;
  return e;
}
['deviceGrid', 'roomTabs', 'scenesSection', 'scenesRow'].forEach(el);
const ctx = {
  console, JSON, Math, parseFloat, parseInt, isNaN, String, Set, Promise, setTimeout,
  localStorage: { getItem() { return null; } },
  TouchMenu: { init() {} },
  document: { addEventListener() {}, createElement() { return el(null); }, getElementById(id) { return els[id] || null; },
              querySelector() { return null; }, querySelectorAll() { return []; } },
  fetch: async (url, opts) => { posts.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null }); return { ok: true, json: async () => ({}) }; },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(src + '\n;globalThis.__t = { setAll(a) { allEntities = a; }, getVisibleEntities, createDeviceCard, renderDevices, adjustNumber,'
  + ' pickRoom(list, id) { rooms = list; currentRoom = id; } };', ctx);
(async () => {
  const t = ctx.__t;
  t.setAll(fixture);
  const visible = t.getVisibleEntities().map(e => e.entity_id);
  const cards = {};
  for (const e of fixture) { const c = t.createDeviceCard(e); if (c) cards[e.entity_id] = c.innerHTML; }
  t.renderDevices();
  const gridAll = els.deviceGrid.children.length;
  // the thermostat: the stepper the card painted, then one + press
  const tid = 'num_input_number_thermostat_temperature';
  const m = (cards['input_number.thermostat_temperature'] || '').match(/data-min="([^"]*)" data-max="([^"]*)" data-step="([^"]*)"[\s\S]*?data-value="([^"]*)"/);
  if (m) Object.assign(el(tid).dataset, { min: m[1], max: m[2], step: m[3], unit: '°C', value: m[4] });
  await t.adjustNumber('input_number.thermostat_temperature', +1);
  els[tid].dataset.value = '30';
  await t.adjustNumber('input_number.thermostat_temperature', +1);  // at max: clamps, sends nothing
  // Room tabs: a room with no matching devices must show ITS empty state, never every device in the house.
  const roomList = [{ id: 'r-spare', name: 'Spare', devices: new Set() },
                    { id: 'r-bed', name: 'Bedroom', devices: new Set(['switch.bedroom_1_switch_1']) }];
  const room = {};
  for (const id of ['r-spare', 'r-bed']) {
    t.pickRoom(roomList, id);
    els.deviceGrid.children = []; els.deviceGrid.innerHTML = '';
    const v = t.getVisibleEntities().map(e => e.entity_id);
    t.renderDevices();
    room[id] = { visible: v, cards: els.deviceGrid.children.length, html: els.deviceGrid.innerHTML };
  }
  console.log(JSON.stringify({ visible, cards, grid: gridAll, posts, room }));
})();
"""


def _run_page(tmp_path):
    import json
    import os
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI for the smart-home page harness")
        pytest.skip("Node.js is not installed on this host")
    harness, fixture = tmp_path / "harness.js", tmp_path / "live.json"
    harness.write_text(HARNESS, encoding="utf-8")
    fixture.write_text(json.dumps(LIVE), encoding="utf-8")
    proc = subprocess.run([node, str(harness), str(PAGE), str(fixture)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_live_houses_controls_render(tmp_path):
    out = _run_page(tmp_path)
    for eid in ("switch.bedroom_1_switch_1", "input_boolean.fan", "input_boolean.tv", "input_number.thermostat_temperature"):
        assert eid in out["visible"], eid
        assert eid in out["cards"], f"{eid} has no card renderer"
    assert "toggleDevice('input_boolean.fan'" in out["cards"]["input_boolean.fan"]
    assert "adjustNumber('input_number.thermostat_temperature'" in out["cards"]["input_number.thermostat_temperature"]
    assert out["grid"] == 4  # the four real controls, and no invented demo devices


def test_the_thermostat_sends_input_number_set_value_within_its_range(tmp_path):
    out = _run_page(tmp_path)
    assert out["posts"] == [{"url": "/api/ha/control", "body": {
        "entity_id": "input_number.thermostat_temperature", "action": "set_value", "params": {"value": 21.5}}}]


def test_a_selected_room_never_falls_back_to_every_device(tmp_path):
    """Cross-review (codex, #2099): a room tab with no matching devices used to show EVERY device, so a tap in "Spare" could
    switch another room's light. A selected room shows only its own devices, or its own empty state."""
    room = _run_page(tmp_path)["room"]
    assert room["r-spare"]["visible"] == [] and room["r-spare"]["cards"] == 0
    assert "No devices in this room yet" in room["r-spare"]["html"]
    assert room["r-bed"]["visible"] == ["switch.bedroom_1_switch_1"] and room["r-bed"]["cards"] == 1


def test_actions_send_their_data_in_a_field_the_control_route_reads():
    # HAControlPayload reads `params` / `service_data`; an unknown key (the page used `value`) is accepted and DROPPED,
    # so brightness and temperature silently did nothing.
    text = PAGE.read_text(encoding="utf-8")
    assert "value: {" not in text
    assert "params: { brightness_pct" in text and "params: { temperature" in text
