"""The idle screensaver must not drift to the night clock while someone is up.

The `sleep` surface is a plain INACTIVITY timer (3 min, no touch/voice). It
never knew whether a human was present, so it kept appearing while the operator
was sitting in a lit room — "the sleep card keeps coming up on zoe, even though
I'm not asleep anymore".

There is no presence sensor to ask. Verified against the live house
(2026-07-24, `GET /api/ha/entities`): 44 entities, ZERO `binary_sensor`, and
`person.jason` is `unknown`. The room's own toggles are the only honest signal,
so the operator chose "light fully blocks sleep".

FIXTURE PROVENANCE: `switch.bedroom_1_switch_1` ("Bedroom Light") is the real
entity in the real Bedroom room — the panel `zoe-touch-pi` is bound to room
73319610-63e0-4d32-a7f1-b80f80ff97f5, whose only `room_devices` row is that
switch. Only the fields the resolver reads are kept.

DIRECTION OF FAILURE is the load-bearing property: every unknown must resolve to
`block: False` (i.e. SLEEP). The panel must never latch awake because a lookup
failed — that is the same direction the client's timeout race falls, so the two
halves agree instead of fighting.
"""
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.ci_safe

from routers.panel_config import resolve_sleep_gate

LIGHT = "switch.bedroom_1_switch_1"          # the real Bedroom Light
FAN = "input_boolean.fan"                     # real domain in this house
TEMP = "input_number.thermostat_temperature"  # a READING, never occupancy
SENSOR = "sensor.current_temperature"


def idx(*pairs):
    """Build an entity index the way `_entity_index()` does: id -> entity."""
    return {eid: {"entity_id": eid, "state": state} for eid, state in pairs}


def test_room_light_on_blocks_sleep():
    """The reported bug: operator is up, light is on, panel slept anyway."""
    gate = resolve_sleep_gate({LIGHT}, idx((LIGHT, "on")))
    assert gate["block"] is True, "a lit room must keep the panel awake"
    assert gate["entities"] == [LIGHT]


def test_dark_room_sleeps():
    """The guard must stay NARROW — a dark room still sleeps, or the fix would
    just disable the screensaver."""
    gate = resolve_sleep_gate({LIGHT}, idx((LIGHT, "off")))
    assert gate["block"] is False
    assert gate["entities"] == []


def test_ha_unavailable_falls_through_to_sleeping():
    """`_entity_index()` returns None when HA is unreachable. Unknown must mean
    SLEEP, never "latch awake"."""
    assert resolve_sleep_gate({LIGHT}, None)["block"] is False
    assert resolve_sleep_gate({LIGHT}, None)["reason"] == "ha-unavailable"


def test_panel_in_no_room_sleeps():
    """`room_entity_ids_for_panel` returns an EMPTY set for every "we don't
    know" case, which must not be read as occupancy."""
    assert resolve_sleep_gate(set(), idx((LIGHT, "on")))["block"] is False
    assert resolve_sleep_gate(None, idx((LIGHT, "on")))["block"] is False


def test_readings_are_not_occupancy():
    """A temperature that happens to be non-zero is not a person. Only
    toggle-ish domains count."""
    gate = resolve_sleep_gate({TEMP, SENSOR}, idx((TEMP, "on"), (SENSOR, "on")))
    assert gate["block"] is False, "a sensor/number must never imply occupancy"


def test_a_fan_or_tv_counts_as_someone_being_up():
    """The rule is domain-based on purpose: a switch may be a light, a fan or a
    TV, and all three are equally good evidence a human is awake."""
    assert resolve_sleep_gate({FAN}, idx((FAN, "on")))["block"] is True


def test_unavailable_entity_is_not_on():
    for state in ("unavailable", "unknown", None):
        gate = resolve_sleep_gate({LIGHT}, idx((LIGHT, state)))
        assert gate["block"] is False, f"state {state!r} must not count as on"


def test_stale_room_device_is_skipped_not_crashed():
    """HA entities get renamed/deleted; a room_devices row can outlive one."""
    gate = resolve_sleep_gate({"switch.deleted_long_ago", LIGHT}, idx((LIGHT, "on")))
    assert gate["block"] is True and gate["entities"] == [LIGHT]


def test_any_one_of_several_being_on_is_enough():
    gate = resolve_sleep_gate({LIGHT, FAN}, idx((LIGHT, "off"), (FAN, "on")))
    assert gate["block"] is True and gate["entities"] == [FAN]


# ── route wiring (HTTP level) ────────────────────────────────────────────────
# The tests above prove the DECISION. These prove the PLUMBING that feeds it:
# `Depends(get_db)`, the `room_entity_ids_for_panel` lookup, and the hand-off to
# `_entity_index`. A resolver that is correct but wired to the wrong room — or a
# route that 500s on a panel with no room — would pass every test above.

def _client(monkeypatch, rooms_by_panel, entity_index):
    import routers.panel_config as pc
    import routers.rooms as rooms_mod
    from database import get_db

    async def fake_room_entity_ids(db, panel_id):
        return rooms_by_panel.get(panel_id, set())

    async def fake_entity_index():
        return entity_index

    # The route imports the resolver from routers.rooms INSIDE the handler, so
    # patch it on its home module (patching a local name would not take).
    monkeypatch.setattr(rooms_mod, "room_entity_ids_for_panel", fake_room_entity_ids)
    monkeypatch.setattr(pc, "_entity_index", fake_entity_index)

    app = FastAPI()
    app.include_router(pc.router)

    async def _no_db():
        return None

    app.dependency_overrides[get_db] = _no_db
    return TestClient(app)


def test_route_reports_block_for_a_panel_whose_room_is_lit(monkeypatch):
    client = _client(
        monkeypatch,
        {"zoe-touch-pi": {LIGHT}},
        idx((LIGHT, "on")),
    )
    resp = client.get("/api/panels/zoe-touch-pi/sleep-gate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["block"] is True
    assert body["reason"] == "room-occupied"
    assert body["entities"] == [LIGHT]


def test_route_reports_no_block_for_a_panel_with_no_room(monkeypatch):
    """A panel in no room must answer 200/false, not 500 — an unregistered panel
    is ordinary, and the kiosk polls this on every idle window."""
    client = _client(
        monkeypatch,
        {},                       # this panel id has no room
        idx((LIGHT, "on")),       # a lit entity exists, but not in THIS panel's room
    )
    resp = client.get("/api/panels/unknown-panel/sleep-gate")
    assert resp.status_code == 200
    assert resp.json()["block"] is False


def test_route_uses_THIS_panels_room_not_any_lit_entity(monkeypatch):
    """Guards the wiring itself: two panels, one lit room, one dark room."""
    client = _client(
        monkeypatch,
        {"lit-panel": {LIGHT}, "dark-panel": {FAN}},
        idx((LIGHT, "on"), (FAN, "off")),
    )
    assert client.get("/api/panels/lit-panel/sleep-gate").json()["block"] is True
    assert client.get("/api/panels/dark-panel/sleep-gate").json()["block"] is False
