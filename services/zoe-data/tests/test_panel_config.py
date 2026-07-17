"""Contract tests for per-panel config (location / speaker / pinned dock).

Fixtures here are copied from REAL live responses captured 2026-07-17 against
this house, not invented ones — a fixture that mocks a contract the real API
doesn't have proves nothing. From `curl localhost:8000/api/ha/entities` (45
entities):

  {"entities": [{"entity_id": "input_number.thermostat_temperature",
                 "state": "21.0",
                 "attributes": {"min": 16.0, "max": 30.0, "step": 0.5,
                                "mode": "box", "unit_of_measurement": "°C",
                                "icon": "mdi:thermostat",
                                "friendly_name": "Thermostat"}}, ...],
   "count": 45}

Ground truth that drives the shape under test:
  - ZERO `light.*` and ZERO `climate.*` entities exist. The "lights" are
    `input_boolean.*` — hence no domain allow-list.
  - The thermostat is TWO entities (sensor read + input_number write) — hence
    every pin is a read/write pair.
  - `input_boolean.fan`/`.tv` share a domain with the lights but carry their own
    `mdi:fan`/`mdi:television` icons — hence server-side icon resolution.
"""

import pytest

from routers.panel_config import (
    KIND_SCENE,
    KIND_TEMP,
    KIND_TOGGLE,
    MAX_PIN_NAME_LEN,
    MAX_PINS,
    build_config_payload,
    normalize_entity_id,
    normalize_location,
    normalize_pin,
    normalize_pin_name,
    player_ids_or_unknown,
    resolve_kind,
    resolve_pins,
    validate_pins,
)

pytestmark = pytest.mark.ci_safe


# Verbatim from the live HA bridge (curl localhost:8000/api/ha/entities).
LIVE_ENTITIES = {
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
    "input_boolean.tv": {
        "entity_id": "input_boolean.tv",
        "state": "off",
        "attributes": {"friendly_name": "TV", "icon": "mdi:television"},
    },
    "scene.good_night": {
        "entity_id": "scene.good_night",
        "state": "2026-04-06T04:58:23.796694+00:00",
        "attributes": {"friendly_name": "Good Night"},  # scenes carry no icon
    },
    "scene.movie_time": {
        "entity_id": "scene.movie_time",
        "state": "unknown",
        "attributes": {"friendly_name": "Movie Time"},
    },
    "switch.lva_88a29e0a953f_mute": {
        "entity_id": "switch.lva_88a29e0a953f_mute",
        "state": "unavailable",
        "attributes": {"friendly_name": "zoe-touch-pi Mute", "icon": "mdi:microphone-off"},
    },
    "sensor.current_temperature": {
        "entity_id": "sensor.current_temperature",
        "state": "21.0",
        "attributes": {
            "unit_of_measurement": "°C",
            "icon": "mdi:thermometer",
            "friendly_name": "Current Temperature",
        },
    },
    "input_number.thermostat_temperature": {
        "entity_id": "input_number.thermostat_temperature",
        "state": "21.0",
        "attributes": {
            "initial": None,
            "editable": False,
            "min": 16.0,
            "max": 30.0,
            "step": 0.5,
            "mode": "box",
            "unit_of_measurement": "°C",
            "icon": "mdi:thermostat",
            "friendly_name": "Thermostat",
        },
    },
}

TEMP_PIN = {
    "read_eid": "sensor.current_temperature",
    "write_eid": "input_number.thermostat_temperature",
    "name": "Temp",
}


# ── pin names: single word, server-enforced ───────────────────────────────────

def test_pin_name_trims_surrounding_whitespace():
    assert normalize_pin_name("  Living  ") == "Living"


@pytest.mark.parametrize("bad", ["Living Room", "a b", "two\twords", "line\nbreak"])
def test_pin_name_rejects_multiple_words(bad):
    # Label length is the binding width constraint on the dock, not pin count.
    with pytest.raises(ValueError, match="single word"):
        normalize_pin_name(bad)


def test_pin_name_rejects_empty_and_whitespace_only():
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="empty"):
            normalize_pin_name(bad)


def test_pin_name_enforces_max_length():
    assert normalize_pin_name("A" * MAX_PIN_NAME_LEN) == "A" * MAX_PIN_NAME_LEN
    with pytest.raises(ValueError, match="at most"):
        normalize_pin_name("A" * (MAX_PIN_NAME_LEN + 1))


def test_pin_name_rejects_non_string():
    with pytest.raises(ValueError, match="string"):
        normalize_pin_name(42)


# ── entity ids: shape only, no domain allow-list ──────────────────────────────

@pytest.mark.parametrize(
    "entity_id",
    [
        "input_boolean.bedroom_light",  # this house's actual "lights"
        "input_number.thermostat_temperature",
        "sensor.current_temperature",
        "scene.good_night",
        "switch.lva_88a29e0a953f_mute",
        "light.bedroom",  # doesn't exist here, must still be accepted
        "climate.bedroom",
    ],
)
def test_entity_id_accepts_every_domain(entity_id):
    # A domain allow-list would reject every real control in this house.
    assert normalize_entity_id(entity_id) == entity_id


@pytest.mark.parametrize("bad", ["nodomain", ".leading", "trailing.", "", "   "])
def test_entity_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        normalize_entity_id(bad)


# ── kind resolution (server-side, from the write entity) ──────────────────────

@pytest.mark.parametrize(
    "write_eid,expected",
    [
        ("input_boolean.bedroom_light", KIND_TOGGLE),
        ("input_boolean.fan", KIND_TOGGLE),
        ("switch.lva_88a29e0a953f_mute", KIND_TOGGLE),
        ("light.bedroom", KIND_TOGGLE),
        ("scene.good_night", KIND_SCENE),
        ("input_number.thermostat_temperature", KIND_TEMP),
        ("number.whatever", KIND_TEMP),
    ],
)
def test_resolve_kind_from_write_entity(write_eid, expected):
    assert resolve_kind(write_eid) == expected


# ── pin canonicalisation: simple form and pair form ───────────────────────────

def test_simple_form_canonicalises_to_an_equal_pair():
    # A light has one entity; the response shape stays the pair regardless.
    assert normalize_pin({"entity_id": "input_boolean.bedroom_light", "name": "Bed"}) == {
        "read_eid": "input_boolean.bedroom_light",
        "write_eid": "input_boolean.bedroom_light",
        "name": "Bed",
    }


def test_pair_form_keeps_distinct_read_and_write():
    # The operator's headline case: "one light and the temp control".
    assert normalize_pin(TEMP_PIN) == {
        "read_eid": "sensor.current_temperature",
        "write_eid": "input_number.thermostat_temperature",
        "name": "Temp",
    }


def test_pin_rejects_mixing_simple_and_pair_forms():
    with pytest.raises(ValueError, match="not both"):
        normalize_pin({
            "entity_id": "input_boolean.fan",
            "read_eid": "sensor.current_temperature",
            "name": "Temp",
        })


def test_pin_requires_an_entity():
    with pytest.raises(ValueError, match="needs entity_id or read_eid"):
        normalize_pin({"name": "Bed"})


@pytest.mark.parametrize("half", ["read_eid", "write_eid"])
def test_pin_pair_requires_both_halves(half):
    # Read-only pins can't be actioned; write-only pins can't show state.
    with pytest.raises(ValueError):
        normalize_pin({half: "sensor.current_temperature", "name": "Temp"})


# ── the pin list ──────────────────────────────────────────────────────────────

def test_validate_pins_preserves_operator_order():
    pins = validate_pins([
        {"entity_id": "input_boolean.bedroom_light", "name": "Bed"},
        TEMP_PIN,
    ])
    assert [p["write_eid"] for p in pins] == [
        "input_boolean.bedroom_light",
        "input_number.thermostat_temperature",
    ]


def test_validate_pins_enforces_cap():
    too_many = [
        {"entity_id": f"input_boolean.l{i}", "name": f"L{i}"} for i in range(MAX_PINS + 1)
    ]
    with pytest.raises(ValueError, match="at most"):
        validate_pins(too_many)


def test_validate_pins_allows_exactly_max_including_a_temp_pin():
    # The measured worst case that must fit: one temp tile + three toggles.
    pins = [TEMP_PIN] + [
        {"entity_id": f"input_boolean.l{i}", "name": f"L{i}"} for i in range(MAX_PINS - 1)
    ]
    assert len(validate_pins(pins)) == MAX_PINS


def test_validate_pins_rejects_duplicate_write_entity():
    with pytest.raises(ValueError, match="duplicate"):
        validate_pins([
            {"entity_id": "input_boolean.bedroom_light", "name": "Bed"},
            {"entity_id": "input_boolean.bedroom_light", "name": "Lamp"},
        ])


def test_validate_pins_rejects_non_list_and_non_dict_items():
    with pytest.raises(ValueError, match="list"):
        validate_pins({"entity_id": "input_boolean.fan", "name": "Fan"})
    with pytest.raises(ValueError, match="object"):
        validate_pins(["input_boolean.fan"])


def test_validate_pins_empty_list_is_valid():
    assert validate_pins([]) == []


# ── speaker validation: an MA outage must not lock the operator out ───────────

def test_empty_player_list_means_cannot_validate_not_no_players():
    # REGRESSION (caught by the live round-trip, not by a mock): music_service
    # ._ma never raises — it returns None on a transport error, which
    # get_players() turns into []. Treating [] as an empty set rejected a
    # player_id that /api/music/players was listing at that very moment.
    assert player_ids_or_unknown([]) is None
    assert player_ids_or_unknown(None) is None
    assert player_ids_or_unknown("not a list") is None


def test_real_player_list_validates():
    # Shape verbatim from `curl localhost:8000/api/music/players`.
    players = [{"player_id": "up286412cf6eb7", "name": "Jason’s MacBook Pro (2)"}]
    assert player_ids_or_unknown(players) == {"up286412cf6eb7"}


def test_player_list_of_junk_entries_means_cannot_validate():
    assert player_ids_or_unknown([{"name": "no id"}, "junk"]) is None


# ── location ──────────────────────────────────────────────────────────────────

def test_location_collapses_whitespace_but_allows_multiple_words():
    # location is prose ("Living Room"), unlike the single-word dock label.
    assert normalize_location("  Living   Room ") == "Living Room"


def test_location_empty_clears():
    assert normalize_location("") is None
    assert normalize_location("   ") is None
    assert normalize_location(None) is None


def test_location_enforces_max_length():
    with pytest.raises(ValueError, match="at most"):
        normalize_location("x" * 65)


# ── resolution against the live HA shape ──────────────────────────────────────

def test_resolve_toggle_pin_is_render_ready():
    resolved, unresolved = resolve_pins(
        validate_pins([{"entity_id": "input_boolean.bedroom_light", "name": "Bed"}]),
        LIVE_ENTITIES,
    )
    assert unresolved == []
    assert resolved == [{
        "name": "Bed",
        "kind": KIND_TOGGLE,
        "read_eid": "input_boolean.bedroom_light",
        "write_eid": "input_boolean.bedroom_light",
        "write_action": "toggle",
        "state": "off",
        "setpoint": None,
        "friendly_name": "Bedroom Light",
        "icon": "mdi:ceiling-light",
        "available": True,
        "min": None,
        "max": None,
        "step": None,
        "unit": None,
    }]


def test_resolve_temp_pin_carries_bounds_and_both_readings():
    resolved, unresolved = resolve_pins(validate_pins([TEMP_PIN]), LIVE_ENTITIES)
    assert unresolved == []
    assert resolved == [{
        "name": "Temp",
        "kind": KIND_TEMP,
        "read_eid": "sensor.current_temperature",
        "write_eid": "input_number.thermostat_temperature",
        # `set_value` is unmapped in ha_control's action_map, which falls through
        # to the bridge — so this needs no control-path change.
        "write_action": "set_value",
        "state": "21.0",       # what the room IS (sensor)
        "setpoint": "21.0",    # what it's set TO (input_number)
        "friendly_name": "Current Temperature",
        "icon": "mdi:thermometer",
        "available": True,
        "min": 16.0,
        "max": 30.0,
        "step": 0.5,
        "unit": "°C",
    }]


def test_resolve_scene_pin_uses_kind_default_icon():
    # Every scene in this house has attributes.icon == None.
    resolved, _ = resolve_pins(
        validate_pins([{"entity_id": "scene.good_night", "name": "Night"}]), LIVE_ENTITIES
    )
    assert resolved[0]["kind"] == KIND_SCENE
    assert resolved[0]["write_action"] == "turn_on"
    assert resolved[0]["icon"] == "mdi:palette-outline"


@pytest.mark.parametrize(
    "entity_id,expected_icon",
    [
        ("input_boolean.fan", "mdi:fan"),
        ("input_boolean.tv", "mdi:television"),
        ("input_boolean.bedroom_light", "mdi:ceiling-light"),
    ],
)
def test_icon_comes_from_ha_not_the_domain(entity_id, expected_icon):
    # fan/tv/lights all share the input_boolean domain — domain-derived icons
    # would draw a lightbulb on all three (the bug slice(0,3) currently hides).
    resolved, _ = resolve_pins(
        validate_pins([{"entity_id": entity_id, "name": "X"}]), LIVE_ENTITIES
    )
    assert resolved[0]["icon"] == expected_icon


def test_resolve_pins_skips_stale_entity_without_raising():
    # HA entities get renamed and deleted — a stale pin must degrade, not 500.
    resolved, unresolved = resolve_pins(
        validate_pins([
            {"entity_id": "light.deleted_yesterday", "name": "Gone"},
            {"entity_id": "scene.movie_time", "name": "Movie"},
        ]),
        LIVE_ENTITIES,
    )
    assert [p["write_eid"] for p in resolved] == ["scene.movie_time"]
    assert unresolved == ["light.deleted_yesterday"]


def test_temp_pin_is_dropped_when_either_half_goes_stale():
    resolved, unresolved = resolve_pins(
        validate_pins([{
            "read_eid": "sensor.current_temperature",
            "write_eid": "input_number.deleted_thermostat",
            "name": "Temp",
        }]),
        LIVE_ENTITIES,
    )
    assert resolved == []
    assert unresolved == ["input_number.deleted_thermostat"]


def test_resolve_pins_marks_unavailable_state_not_available():
    resolved, _ = resolve_pins(
        validate_pins([{"entity_id": "switch.lva_88a29e0a953f_mute", "name": "Mute"}]),
        LIVE_ENTITIES,
    )
    assert resolved[0]["state"] == "unavailable"
    assert resolved[0]["available"] is False


def test_resolve_pins_keeps_pins_when_ha_is_offline():
    # entity_index None == "HA unknown". The dock must still render the
    # operator's chosen pins through an HA hiccup rather than blanking.
    resolved, unresolved = resolve_pins(validate_pins([TEMP_PIN]), None)
    assert unresolved == []
    assert resolved[0]["write_eid"] == "input_number.thermostat_temperature"
    assert resolved[0]["kind"] == KIND_TEMP  # kind survives: it's derived, not fetched
    assert resolved[0]["state"] is None
    assert resolved[0]["available"] is False


def test_resolve_tolerates_a_legacy_simple_entity_id_row():
    # Defensive: a stored row written as {entity_id,...} still resolves.
    resolved, _ = resolve_pins(
        [{"entity_id": "input_boolean.fan", "name": "Fan"}], LIVE_ENTITIES
    )
    assert resolved[0]["read_eid"] == resolved[0]["write_eid"] == "input_boolean.fan"


# ── the payload contract ──────────────────────────────────────────────────────

def _payload(**overrides):
    kwargs = dict(
        device_id="zoe-touch-pi",
        location="kitchen",
        stored_default_player=None,
        global_default_player="",
        stored_pins=None,
        entity_index=LIVE_ENTITIES,
    )
    kwargs.update(overrides)
    return build_config_payload(**kwargs)


def test_payload_is_flat_and_resolved():
    payload = _payload(stored_pins=validate_pins([TEMP_PIN]))
    # Flat: no nested "preferences"/"config" envelope for the client to unwrap.
    assert set(payload) == {
        "device_id", "location", "default_player", "default_player_source",
        "pins_configured", "pinned", "unresolved", "ha_available", "max_pins",
    }
    assert payload["location"] == "kitchen"
    # Already-resolved: the dock knows what it renders without a 2nd round-trip.
    assert payload["pinned"][0]["friendly_name"] == "Current Temperature"


def test_every_pin_has_a_stable_key_set_regardless_of_kind():
    # The client must never branch on key existence — only on `kind`.
    payload = _payload(stored_pins=validate_pins([
        TEMP_PIN,
        {"entity_id": "input_boolean.fan", "name": "Fan"},
        {"entity_id": "scene.good_night", "name": "Night"},
    ]))
    keysets = [set(p) for p in payload["pinned"]]
    assert len(payload["pinned"]) == 3
    assert all(k == keysets[0] for k in keysets)


def test_unconfigured_pins_are_distinct_from_explicitly_empty():
    # This distinction is the contract: "no pins" != "pin nothing".
    never = _payload(stored_pins=None)
    assert never["pins_configured"] is False  # dock keeps its slice(0,3) fallback
    assert never["pinned"] == []

    chose_none = _payload(stored_pins=[])
    assert chose_none["pins_configured"] is True  # dock shows nothing
    assert chose_none["pinned"] == []


def test_panel_speaker_overrides_global():
    payload = _payload(
        stored_default_player="up286412cf6eb7", global_default_player="living"
    )
    assert payload["default_player"] == "up286412cf6eb7"
    assert payload["default_player_source"] == "panel"


def test_global_speaker_is_the_fallback():
    payload = _payload(stored_default_player=None, global_default_player="living")
    assert payload["default_player"] == "living"
    assert payload["default_player_source"] == "global"


def test_no_speaker_anywhere_resolves_to_none():
    payload = _payload(stored_default_player=None, global_default_player="")
    assert payload["default_player"] is None
    assert payload["default_player_source"] == "none"


def test_blank_stored_speaker_falls_back_to_global():
    payload = _payload(stored_default_player="   ", global_default_player="living")
    assert payload["default_player_source"] == "global"


def test_payload_reports_ha_availability():
    assert _payload()["ha_available"] is True
    assert _payload(entity_index=None)["ha_available"] is False


def test_unregistered_panel_gets_safe_defaults():
    payload = _payload(device_id="brand-new-panel", location=None)
    assert payload["location"] is None
    assert payload["pins_configured"] is False
    assert payload["max_pins"] == MAX_PINS


# ── route-level contract (HTTP layer) ─────────────────────────────────────────
#
# A mini app mounting ONLY this router with `get_db`/`get_current_user`
# overridden — NOT `TestClient(app)`, which starts the full FastAPI lifespan and
# is Jetson-only (see tests/AGENTS.md). This keeps the lane slim-dep-green while
# still exercising status codes, the auth dependency, the UPSERT parameters and
# the partial-update semantics that the pure-function tests can't reach.

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from database import get_db
from routers import panel_config as pc


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeRow(dict):
    """Stands in for an asyncpg Record: dict-like with .keys()."""


class _FakeDB:
    """Records the SQL it is handed and replays a canned post-write row."""

    def __init__(self, row=None):
        self.row = row
        self.calls = []
        self.committed = False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))

        async def _run():
            return _FakeCursor(self.row)

        return _run()

    async def commit(self):
        self.committed = True


def _client(db, monkeypatch, user=None):
    monkeypatch.setattr(pc, "_global_default_player", lambda: "living")

    async def _fake_index():
        return LIVE_ENTITIES

    async def _fake_players():
        return {"up286412cf6eb7"}

    monkeypatch.setattr(pc, "_entity_index", _fake_index)
    monkeypatch.setattr(pc, "_known_player_ids", _fake_players)

    app = FastAPI()
    app.include_router(pc.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user or {"user_id": "jason"}
    return TestClient(app)


def test_route_get_is_reachable_without_auth_headers():
    # The kiosk runs as a guest and polls this at boot — same posture as
    # GET /api/system/display/preferences. A GET that needed a session would
    # silently blank the dock on the panel.
    assert get_current_user not in _route_dependencies(pc.get_panel_config)


def _route_dependencies(endpoint):
    """The dependency callables declared on a route handler."""
    import inspect

    return {
        p.default.dependency
        for p in inspect.signature(endpoint).parameters.values()
        if hasattr(p.default, "dependency")
    }


def test_route_put_requires_the_same_auth_dependency_as_display_prefs():
    # PUT is a write: it must go through get_current_user, matching
    # PUT /api/system/display/preferences.
    assert get_current_user in _route_dependencies(pc.put_panel_config)


def test_route_get_returns_resolved_payload(monkeypatch):
    db = _FakeDB(_FakeRow({
        "location": "kitchen",
        "default_player": None,
        "pinned": json.dumps([{
            "read_eid": "input_boolean.fan",
            "write_eid": "input_boolean.fan",
            "name": "Fan",
        }]),
    }))
    r = _client(db, monkeypatch).get("/api/panels/zoe-touch-pi/config")
    assert r.status_code == 200
    body = r.json()
    assert body["location"] == "kitchen"
    assert body["default_player_source"] == "global"  # falls back
    assert body["pinned"][0]["icon"] == "mdi:fan"


def test_route_get_unregistered_panel_is_not_an_error(monkeypatch):
    r = _client(_FakeDB(None), monkeypatch).get("/api/panels/never-seen/config")
    assert r.status_code == 200
    assert r.json()["pins_configured"] is False


def test_route_put_writes_only_supplied_columns(monkeypatch):
    # The atomicity contract: a location-only PUT must not name `pinned` or
    # `default_player` in its SET list, or a concurrent write is clobbered.
    db = _FakeDB(_FakeRow({"location": "Lounge", "default_player": None, "pinned": None}))
    r = _client(db, monkeypatch).put("/api/panels/p1/config", json={"location": "Lounge"})
    assert r.status_code == 200
    sql, params = db.calls[0]
    assert "location=excluded.location" in sql
    assert "pinned=excluded.pinned" not in sql
    assert "default_player=excluded.default_player" not in sql
    assert db.committed


def test_route_put_uses_portable_timestamp_not_now(monkeypatch):
    db = _FakeDB(_FakeRow({"location": "Lounge", "default_player": None, "pinned": None}))
    _client(db, monkeypatch).put("/api/panels/p1/config", json={"location": "Lounge"})
    sql, _ = db.calls[0]
    assert "CURRENT_TIMESTAMP" in sql
    assert "NOW()" not in sql


def test_route_put_returns_the_stored_row_not_the_request(monkeypatch):
    # RETURNING is what makes the response reflect what's actually stored.
    db = _FakeDB(_FakeRow({
        "location": "Bedroom", "default_player": "up286412cf6eb7", "pinned": None,
    }))
    r = _client(db, monkeypatch).put("/api/panels/p1/config", json={"location": "Bedroom"})
    assert r.json()["location"] == "Bedroom"
    assert r.json()["default_player"] == "up286412cf6eb7"
    assert r.json()["default_player_source"] == "panel"


def test_route_put_empty_body_touches_no_config_column(monkeypatch):
    db = _FakeDB(_FakeRow({"location": "kitchen", "default_player": None, "pinned": None}))
    r = _client(db, monkeypatch).put("/api/panels/p1/config", json={})
    assert r.status_code == 200
    sql, _ = db.calls[0]
    assert "excluded" not in sql  # only updated_at is set
    assert "updated_at=CURRENT_TIMESTAMP" in sql


@pytest.mark.parametrize(
    "payload,detail",
    [
        ({"pinned": [{"entity_id": "input_boolean.fan", "name": "Living Room"}]},
         "single word"),
        ({"pinned": [{"entity_id": "input_boolean.fan", "name": "WayTooLongLabel"}]},
         "at most"),
        ({"default_player": "not-a-real-speaker"}, "unknown player_id"),
        ({"default_player": 42}, "must be a string"),
    ],
)
def test_route_put_rejects_bad_input_with_400(monkeypatch, payload, detail):
    db = _FakeDB(_FakeRow({"location": None, "default_player": None, "pinned": None}))
    r = _client(db, monkeypatch).put("/api/panels/p1/config", json=payload)
    assert r.status_code == 400
    assert detail in r.json()["detail"]
    assert db.calls == []  # rejected before touching the DB


def test_route_put_rejects_a_non_object_body(monkeypatch):
    db = _FakeDB(None)
    r = _client(db, monkeypatch).put("/api/panels/p1/config", json=["not", "an", "object"])
    assert r.status_code == 400


def test_route_put_accepts_the_temp_pair_and_stores_the_canonical_pair(monkeypatch):
    stored = json.dumps([{
        "read_eid": "sensor.current_temperature",
        "write_eid": "input_number.thermostat_temperature",
        "name": "Temp",
    }])
    db = _FakeDB(_FakeRow({"location": None, "default_player": None, "pinned": stored}))
    r = _client(db, monkeypatch).put("/api/panels/p1/config", json={"pinned": [TEMP_PIN]})
    assert r.status_code == 200
    # What got written is the canonical pair.
    _, params = db.calls[0]
    assert json.loads(params[4]) == [{
        "read_eid": "sensor.current_temperature",
        "write_eid": "input_number.thermostat_temperature",
        "name": "Temp",
    }]
    pin = r.json()["pinned"][0]
    assert (pin["kind"], pin["write_action"], pin["min"], pin["max"]) == (
        "temp", "set_value", 16.0, 30.0,
    )
