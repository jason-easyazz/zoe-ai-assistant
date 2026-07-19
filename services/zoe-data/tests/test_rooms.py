"""Contract tests for Zoe-owned rooms (W1: model + API).

Fixtures mirror the REAL live house captured 2026-07-19, not invented ones. Two
facts from it drive the shapes under test:

  - The first real Grid Connect switch arrived as
    ``switch.bedroom_1_switch_1`` / "Bedroom 1 Switch 1" — a LIGHT switch whose
    name contains no "light". That device is precisely why name-derived rooms
    (``smart_home_service._room_of``) are not good enough and this model exists.
  - The house has ZERO ``light.*`` entities; its controls are
    ``input_boolean.*`` / ``switch.*`` / ``scene.*``. Hence entity ids are
    validated for SHAPE only — a domain allow-list would reject every real
    device.

These cover the pure layer (validation, payload resolution, picker filtering).
The DB-backed routes are exercised against real SQLite in
``test_panel_config.py`` — a fake cursor asserts against a stub rather than the
schema, and would not have caught the room column missing from an INSERT.
"""

import pytest

from routers.rooms import (
    MAX_ROOM_NAME_LEN,
    is_pickable,
    build_room_payload,
    normalize_entity_id,
    normalize_ha_area_id,
    normalize_room_name,
    resolve_device,
    room_entity_ids_for_panel,
    slugify,
)

pytestmark = pytest.mark.ci_safe


# The live entity index, keyed as the resolver receives it.
LIVE_ENTITIES = {
    "switch.bedroom_1_switch_1": {
        "entity_id": "switch.bedroom_1_switch_1",
        "state": "on",
        "attributes": {"friendly_name": "Bedroom 1 Switch 1"},
    },
    "input_boolean.bedroom_light": {
        "entity_id": "input_boolean.bedroom_light",
        "state": "off",
        "attributes": {"friendly_name": "Bedroom Light", "icon": "mdi:ceiling-light"},
    },
    "input_boolean.fan": {
        "entity_id": "input_boolean.fan",
        "state": "off",
        "attributes": {"friendly_name": "Ceiling Fan", "icon": "mdi:fan"},
    },
    # Present in HA but not currently usable — a flapping integration. This is a
    # DIFFERENT case from "absent from the index" and takes a different branch;
    # a mutation run proved the suite passed without it.
    "switch.flaky_lamp": {
        "entity_id": "switch.flaky_lamp",
        "state": "unavailable",
        "attributes": {"friendly_name": "Flaky Lamp"},
    },
    "switch.never_reported": {
        "entity_id": "switch.never_reported",
        "state": "unknown",
        "attributes": {"friendly_name": "Never Reported"},
    },
}


class _Row(dict):
    """Rows arrive subscriptable; dict is a faithful enough stand-in."""


def _room_row(**over):
    row = {"id": "r-1", "name": "Bedroom", "slug": "bedroom", "ha_area_id": None}
    row.update(over)
    return _Row(row)


# ── names + slugs ─────────────────────────────────────────────────────────────

def test_room_name_keeps_spaces():
    """Unlike a dock pin, a room name is prose: "Living Room" must survive."""
    assert normalize_room_name("  Living   Room  ") == "Living Room"


def test_room_name_rejects_empty_and_overlong():
    for bad in ("", "   ", None, 5):
        with pytest.raises(ValueError):
            normalize_room_name(bad)
    with pytest.raises(ValueError):
        normalize_room_name("x" * (MAX_ROOM_NAME_LEN + 1))


def test_slug_is_stable_key():
    assert slugify("Living Room") == "living_room"
    assert slugify("Kid's Bedroom!") == "kid_s_bedroom"
    # Never empty: a slug is the key an intent matches on.
    assert slugify("!!!") == "room"


# ── entity ids: SHAPE ONLY ────────────────────────────────────────────────────

def test_entity_id_accepts_every_domain_this_house_uses():
    """A domain allow-list would reject every real control in this house."""
    for eid in (
        "switch.bedroom_1_switch_1",
        "input_boolean.bedroom_light",
        "input_number.thermostat_temperature",
        "scene.good_night",
        "light.hypothetical",
    ):
        assert normalize_entity_id(eid) == eid


def test_entity_id_rejects_malformed():
    for bad in ("", "   ", "noseparator", ".leading", "trailing.", None, 7):
        with pytest.raises(ValueError):
            normalize_entity_id(bad)


def test_ha_area_link_is_optional():
    """A room with no HA area is normal, not unconfigured."""
    assert normalize_ha_area_id(None) is None
    assert normalize_ha_area_id("") is None
    assert normalize_ha_area_id("  ") is None
    assert normalize_ha_area_id(" bedroom_area ") == "bedroom_area"


# ── device resolution ─────────────────────────────────────────────────────────

def test_resolve_device_uses_live_name_and_state():
    d = resolve_device("input_boolean.bedroom_light", LIVE_ENTITIES)
    assert d["name"] == "Bedroom Light"
    assert d["state"] == "off"
    assert d["icon"] == "mdi:ceiling-light"
    assert d["available"] is True


@pytest.mark.parametrize(
    "entity_id,name",
    [("switch.flaky_lamp", "Flaky Lamp"), ("switch.never_reported", "Never Reported")],
)
def test_device_present_but_unusable_is_named_yet_unavailable(entity_id, name):
    """HA returned it, so its NAME is known — but the state says it cannot be
    driven. Reporting available:true here would let a dead control render as a
    live one (the same rule the dock's pins carry)."""
    d = resolve_device(entity_id, LIVE_ENTITIES)
    assert d["name"] == name, "a device HA returned must still be named"
    assert d["available"] is False


def test_device_missing_from_ha_is_kept_but_unavailable():
    """The user put it in this room deliberately — an HA hiccup must not silently
    empty their room. (Distinct from a stale PIN, which panel_config drops.)"""
    d = resolve_device("switch.gone", LIVE_ENTITIES)
    assert d["entity_id"] == "switch.gone"
    assert d["available"] is False
    assert d["name"] is None


def test_ha_offline_keeps_every_device_listed():
    payload = build_room_payload(
        _room_row(), ["input_boolean.bedroom_light", "switch.bedroom_1_switch_1"], None
    )
    assert [d["entity_id"] for d in payload["devices"]] == [
        "input_boolean.bedroom_light",
        "switch.bedroom_1_switch_1",
    ]
    assert payload["ha_available"] is False
    assert all(d["available"] is False for d in payload["devices"])


def test_the_switch_that_motivated_this_model_resolves():
    """"Bedroom 1 Switch 1" carries no "light" in its name, so name-parsing calls
    it a switch. Explicit room membership is what makes it findable."""
    payload = build_room_payload(_room_row(), ["switch.bedroom_1_switch_1"], LIVE_ENTITIES)
    assert payload["slug"] == "bedroom"
    assert payload["devices"][0]["name"] == "Bedroom 1 Switch 1"
    assert payload["devices"][0]["available"] is True


def test_device_order_is_preserved():
    """Order is the user's choice and round-trips verbatim."""
    ids = ["input_boolean.fan", "switch.bedroom_1_switch_1", "input_boolean.bedroom_light"]
    payload = build_room_payload(_room_row(), ids, LIVE_ENTITIES)
    assert [d["entity_id"] for d in payload["devices"]] == ids


@pytest.mark.parametrize(
    "entity_id",
    [
        "switch.bedroom_1_switch_1",     # the real Grid Connect switch
        "input_boolean.bedroom_light",
        "light.anything",
        "scene.good_night",
        "input_number.thermostat_temperature",
        "media_player.lounge",
        "cover.garage",
    ],
)
def test_picker_suggests_controllable_devices(entity_id):
    assert is_pickable(entity_id) is True


@pytest.mark.parametrize(
    "entity_id",
    [
        # Real noise from the live house's 48 entities — offering these in a
        # "put a device in this room" picker is what made it unusable.
        "event.backup_automatic_backup",
        "sensor.backup_backup_manager_state",
        "sensor.backup_last_attempted_automatic_backup",
        "assist_satellite.zoe_touch_pi_assist_satellite",
        "update.home_assistant_core_update",
        "binary_sensor.rpi_power_status",
    ],
)
def test_picker_hides_diagnostic_and_plumbing_entities(entity_id):
    assert is_pickable(entity_id) is False


def test_picker_filter_never_narrows_what_can_be_STORED():
    """The picker is a suggestion filter, not a storage rule — an entity the
    picker hides must still be storable, or an importer (or a power user with an
    odd device) is locked out."""
    hidden = "sensor.backup_backup_manager_state"
    assert is_pickable(hidden) is False
    assert normalize_entity_id(hidden) == hidden


def test_linked_to_ha_reflects_the_area_link():
    assert build_room_payload(_room_row(), [], LIVE_ENTITIES)["linked_to_ha"] is False
    linked = build_room_payload(_room_row(ha_area_id="area_bedroom"), [], LIVE_ENTITIES)
    assert linked["linked_to_ha"] is True
    assert linked["ha_area_id"] == "area_bedroom"


# ── the DB lookup the voice path actually uses ───────────────────────────────
# Everything above is pure. `room_entity_ids_for_panel` is NOT: it is the real
# panel_id -> panels.room_id -> room_devices chain that W3's voice resolution
# depends on, and the W3 suite exercises the resolver with a hand-built set —
# so a wrong column name or Row-key error in here would be invisible and every
# test would pass for the wrong reason. Driven against real sqlite3 for the
# same reason panel_config's room tests are: a fake cursor asserts against a
# stub, not the schema.


class _Cur:
    def __init__(self, cursor):
        self._c = cursor

    async def fetchone(self):
        return self._c.fetchone()

    async def fetchall(self):
        return self._c.fetchall()


class _Db:
    """The router's async db surface over synchronous sqlite3 — loop-agnostic."""

    def __init__(self, path):
        import sqlite3

        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    async def execute(self, sql, params=()):
        return _Cur(self._conn.execute(sql, params))

    async def commit(self):
        self._conn.commit()


@pytest.fixture
def rooms_db(tmp_path):
    db = _Db(str(tmp_path / "rooms.db"))
    db._conn.executescript(
        """
        CREATE TABLE panels (panel_id TEXT PRIMARY KEY, room_id TEXT);
        CREATE TABLE room_devices (room_id TEXT NOT NULL, entity_id TEXT NOT NULL UNIQUE);
        INSERT INTO panels (panel_id, room_id) VALUES ('zoe-touch-pi', 'r-bed');
        INSERT INTO panels (panel_id, room_id) VALUES ('kitchen-panel', NULL);
        INSERT INTO panels (panel_id, room_id) VALUES ('ghost-panel', 'r-deleted');
        INSERT INTO room_devices (room_id, entity_id)
             VALUES ('r-bed', 'switch.bedroom_1_switch_1');
        INSERT INTO room_devices (room_id, entity_id)
             VALUES ('r-bed', 'input_boolean.bedroom_light');
        INSERT INTO room_devices (room_id, entity_id) VALUES ('r-kitchen', 'switch.kettle');
        """
    )
    db._conn.commit()
    return db


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_panel_in_a_room_returns_that_rooms_devices(rooms_db):
    got = _run(room_entity_ids_for_panel(rooms_db, "zoe-touch-pi"))
    assert got == {"switch.bedroom_1_switch_1", "input_boolean.bedroom_light"}
    assert "switch.kettle" not in got, "another room's device must never leak in"


@pytest.mark.parametrize(
    "panel_id,why",
    [
        ("kitchen-panel", "panel exists but is in no room"),
        ("ghost-panel", "room_id points at a room that no longer exists"),
        ("never-registered", "panel has no row at all"),
        ("", "no panel id — a phone or web chat has no room"),
    ],
)
def test_every_unknown_case_is_an_empty_set(rooms_db, panel_id, why):
    """Empty means "no room context", and the resolver then behaves exactly as it
    did before rooms existed. Approximating any of these would act on the WRONG
    room's light, which is worse than declining to help."""
    assert _run(room_entity_ids_for_panel(rooms_db, panel_id)) == set(), why


def test_a_broken_lookup_degrades_to_empty_rather_than_breaking_the_turn():
    """A voice turn must never fail because the room lookup did."""

    class _Exploding:
        async def execute(self, *a, **k):
            raise RuntimeError("db is on fire")

    assert _run(room_entity_ids_for_panel(_Exploding(), "zoe-touch-pi")) == set()
