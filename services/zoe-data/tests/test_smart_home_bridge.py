"""Smart-home bridge — classifier + smart_home_service (mocked HA) + card shape.

Mirrors test_music_bridge.py: the classifier needs no bridge; the resolver is
exercised with a stubbed httpx layer so on/off/dim/scene + the offline path are
covered without touching the live Home Assistant hub.
"""
from __future__ import annotations

import pytest

import smart_home_service
from skybridge_service import classify_skybridge_intent, skybridge_intent_requires_identity


# ── classifier (no bridge needed) ────────────────────────────────────────────

@pytest.mark.parametrize("q,action", [
    ("turn on the lights", "turn_on"),
    ("turn off the kitchen lamp", "turn_off"),
    ("switch off the fan", "turn_off"),
    ("lights on", "turn_on"),
    ("dim the lights to 40%", "set_brightness"),
    ("set the bedroom lamp to 80 percent", "set_brightness"),
    ("brighten the lights", "set_brightness"),
    ("activate the movie time scene", "activate_scene"),
    ("run good night scene", "activate_scene"),
    ("is the kitchen light on", "status"),
    ("what lights are on", "status"),
    ("show me the devices", "status"),
    ("smart home", "status"),
])
def test_smart_home_intents(q, action):
    i = classify_skybridge_intent(q, None)
    assert i is not None and i.domain == "smart_home" and i.action == action, q


@pytest.mark.parametrize("q,pct", [
    ("dim the lights to 40%", 40),
    ("set the bedroom lamp to 80 percent", 80),
    ("brighten the lights", 100),
])
def test_brightness_percent_carried(q, pct):
    i = classify_skybridge_intent(q, None)
    assert i is not None and i.action == "set_brightness" and i.duration_seconds == pct, q


@pytest.mark.parametrize("q,name", [
    ("activate the movie time scene", "movie time"),
    ("run good night scene", "good night"),
])
def test_scene_name_extracted(q, name):
    i = classify_skybridge_intent(q, None)
    assert i is not None and i.action == "activate_scene" and i.query == name, q


@pytest.mark.parametrize("q", [
    "add lights to the shopping list",  # list write, not a device toggle
    "play some music",
    "turn off the music",
    "turn the music up",
    "set a timer for 5 minutes",
    "what's on my calendar",
])
def test_smart_home_does_not_over_capture(q):
    i = classify_skybridge_intent(q, None)
    assert i is None or i.domain != "smart_home", q


def test_smart_home_is_public_no_identity_gate():
    i = classify_skybridge_intent("turn on the lights", None)
    assert skybridge_intent_requires_identity(i) is False


# ── resolver (mocked HA bridge) ──────────────────────────────────────────────

class _Intent:
    def __init__(self, action, query="", brightness=0, entity_id=""):
        self.domain = "smart_home"
        self.action = action
        self.query = query
        self.duration_seconds = brightness
        self.entity_id = entity_id


class _FakeBridge:
    """Records control POSTs and serves canned device/scene lists."""

    def __init__(self, *, reachable=True):
        self.reachable = reachable
        self.controls: list[dict] = []
        self.scenes_activated: list[str] = []
        self._switch_state = "off"

    async def get(self, path):
        if not self.reachable:
            return None
        if path == "/lights":
            return {"lights": [
                {"entity_id": "light.lamp", "name": "Living Room Lamp",
                 "state": "on", "brightness": 200},
                {"entity_id": "light.bedroom", "name": "Bedroom Lamp",
                 "state": "off", "brightness": 0},
            ]}
        if path == "/switches":
            return {"switches": [
                {"entity_id": "switch.plug", "name": "Coffee Plug", "state": self._switch_state},
                {"entity_id": "switch.bedroom", "name": "Bedroom Switch", "state": "off"},
                {"entity_id": "switch.hall", "name": "Hallway Switch", "state": "unavailable"},
            ]}
        if path == "/scenes":
            return {"scenes": [
                {"entity_id": "scene.movie_time", "name": "Movie Time"},
            ]}
        return None

    async def post(self, path, payload):
        if not self.reachable:
            return None
        if path == "/devices/control":
            self.controls.append(payload)
            self._switch_state = "on" if payload.get("action") == "turn_on" else "off"
            return {"message": f"Successfully executed {payload['action']} on {payload['entity_id']}", "result": []}
        if path == "/scenes/activate":
            self.scenes_activated.append(payload["scene_id"])
            return {"message": "Successfully activated", "result": []}
        return None


@pytest.fixture
def bridge(monkeypatch):
    fake = _FakeBridge()

    async def _get(path):
        return await fake.get(path)

    async def _post(path, payload):
        return await fake.post(path, payload)

    monkeypatch.setattr(smart_home_service, "_ha_get", _get)
    monkeypatch.setattr(smart_home_service, "_ha_post", _post)
    return fake


@pytest.mark.asyncio
async def test_resolve_status_lists_devices_and_scenes(bridge):
    r = await smart_home_service.resolve_smart_home(_Intent("status"))
    assert r["handled"] is True
    props = r["cards"][0]["props"]
    assert {"Living Room Lamp", "Coffee Plug"} <= {d["name"] for d in props["devices"]}
    assert [s["name"] for s in props["scenes"]] == ["Movie Time"]
    lamp = next(d for d in props["devices"] if d["name"] == "Living Room Lamp")
    assert lamp["on"] is True and lamp["dimmable"] is True


@pytest.mark.asyncio
async def test_resolve_turn_on_sends_control(bridge):
    r = await smart_home_service.resolve_smart_home(_Intent("turn_on", query="coffee plug"))
    assert bridge.controls == [{"entity_id": "switch.plug", "action": "turn_on"}]
    assert "on" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_resolve_turn_off_sends_control(bridge):
    await smart_home_service.resolve_smart_home(_Intent("turn_off", query="coffee plug"))
    assert bridge.controls[-1] == {"entity_id": "switch.plug", "action": "turn_off"}


@pytest.mark.asyncio
async def test_resolve_dim_carries_brightness(bridge):
    await smart_home_service.resolve_smart_home(_Intent("set_brightness", query="living room lamp", brightness=40))
    assert bridge.controls[-1] == {
        "entity_id": "light.lamp", "action": "turn_on", "data": {"brightness_pct": 40}}


@pytest.mark.asyncio
async def test_set_brightness_prefers_dimmable_over_switch(bridge):
    # "bedroom" ties Bedroom Lamp (light) + Bedroom Switch — brightness narrows
    # to the dimmable light, so exactly one device is dimmed.
    await smart_home_service.resolve_smart_home(_Intent("set_brightness", query="bedroom", brightness=60))
    assert bridge.controls == [{
        "entity_id": "light.bedroom", "action": "turn_on", "data": {"brightness_pct": 60}}]


@pytest.mark.asyncio
async def test_ambiguous_toggle_asks_instead_of_controlling(bridge):
    # "bedroom" matches Bedroom Lamp AND Bedroom Switch for a plain toggle — ask.
    r = await smart_home_service.resolve_smart_home(_Intent("turn_off", query="bedroom"))
    assert bridge.controls == []
    assert "which" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_bare_singular_class_disambiguates(bridge):
    # "turn on the lamp" — a SINGULAR class word with two lamps present must ask,
    # not silently sweep every light (the classifier strips "lamp" to query="lamp").
    i = classify_skybridge_intent("turn on the lamp", None)
    r = await smart_home_service.resolve_smart_home(i)
    assert bridge.controls == []
    assert "which" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_bare_plural_class_sweeps(bridge):
    # "turn off the lights" — a PLURAL class word is an intentional all-lights sweep.
    i = classify_skybridge_intent("turn off the lights", None)
    await smart_home_service.resolve_smart_home(i)
    controlled = {c["entity_id"] for c in bridge.controls}
    assert controlled == {"light.lamp", "light.bedroom"}
    assert all(c["action"] == "turn_off" for c in bridge.controls)


@pytest.mark.asyncio
async def test_offline_device_is_not_controlled(bridge):
    r = await smart_home_service.resolve_smart_home(_Intent("turn_on", query="hallway"))
    assert bridge.controls == []  # never POST to an unavailable entity
    assert "offline" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_tile_query_targets_exact_entity_id(bridge):
    # A tile carries the exact entity id via the "@entity" marker — the round-trip
    # through the real classifier must control THAT entity, not a name match.
    i = classify_skybridge_intent("turn off Coffee Plug @switch.plug", None)
    assert i is not None and i.domain == "smart_home" and i.entity_id == "switch.plug"
    await smart_home_service.resolve_smart_home(i)
    assert bridge.controls == [{"entity_id": "switch.plug", "action": "turn_off"}]


@pytest.mark.asyncio
async def test_scene_chip_targets_exact_scene_id(bridge):
    i = classify_skybridge_intent("activate Movie Time @scene.movie_time", None)
    assert i is not None and i.action == "activate_scene" and i.entity_id == "scene.movie_time"
    await smart_home_service.resolve_smart_home(i)
    assert bridge.scenes_activated == ["scene.movie_time"]


@pytest.mark.asyncio
async def test_cross_domain_name_collision_tile_is_exact(monkeypatch):
    # A light AND a switch both named "Light Switch": tapping the SWITCH tile
    # (its @entity id) must toggle the switch, never the like-named light.
    async def _get(path):
        if path == "/lights":
            return {"lights": [{"entity_id": "light.ls", "name": "Light Switch", "state": "off"}]}
        if path == "/switches":
            return {"switches": [{"entity_id": "switch.ls", "name": "Light Switch", "state": "off"}]}
        return {"scenes": []}

    calls: list[dict] = []

    async def _post(path, payload):
        calls.append(payload)
        return {"message": "Successfully executed", "result": []}

    monkeypatch.setattr(smart_home_service, "_ha_get", _get)
    monkeypatch.setattr(smart_home_service, "_ha_post", _post)
    i = classify_skybridge_intent("turn on Light Switch @switch.ls", None)
    await smart_home_service.resolve_smart_home(i)
    assert calls == [{"entity_id": "switch.ls", "action": "turn_on"}]


@pytest.mark.asyncio
async def test_stale_tile_entity_gone_is_friendly(bridge):
    # A tile for an entity no longer present must not blindly POST.
    r = await smart_home_service.resolve_smart_home(_Intent("turn_on", entity_id="switch.removed"))
    assert bridge.controls == []
    assert "available" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_class_only_named_device_tile_stays_exact(monkeypatch):
    # A switch literally named "Light Switch": its tile query cleans to the
    # class-only phrase "light switch", which must still pin THAT entity (not be
    # treated as a generic light-class sweep that misses the switch).
    async def _get(path):
        if path == "/switches":
            return {"switches": [{"entity_id": "switch.light", "name": "Light Switch", "state": "off"}]}
        if path == "/lights":
            return {"lights": [{"entity_id": "light.lamp", "name": "Living Room Lamp", "state": "off"}]}
        return {"scenes": []}

    calls: list[dict] = []

    async def _post(path, payload):
        calls.append(payload)
        return {"message": "Successfully executed", "result": []}

    monkeypatch.setattr(smart_home_service, "_ha_get", _get)
    monkeypatch.setattr(smart_home_service, "_ha_post", _post)
    r = await smart_home_service.resolve_smart_home(_Intent("turn_on", query="light switch"))
    assert calls == [{"entity_id": "switch.light", "action": "turn_on"}]
    assert "on" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_resolve_scene_activates(bridge):
    r = await smart_home_service.resolve_smart_home(_Intent("activate_scene", query="movie time"))
    assert bridge.scenes_activated == ["scene.movie_time"]
    assert "Movie Time" in r["spoken_summary"]


@pytest.mark.asyncio
async def test_resolve_unknown_device_is_friendly(bridge):
    r = await smart_home_service.resolve_smart_home(_Intent("turn_on", query="nonexistent gizmo"))
    assert bridge.controls == []  # never blindly POSTs an unmatched target
    assert r["handled"] is True and "couldn't find" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_resolve_offline_bridge_degrades(bridge):
    bridge.reachable = False
    r = await smart_home_service.resolve_smart_home(_Intent("status"))
    assert r["handled"] is True
    assert r["cards"][0]["props"].get("offline") is True
    assert "home hub" in r["spoken_summary"].lower()


# ── Enriched card: input_boolean helpers, rooms, climate, add-device ──────────

class _HelperBridge:
    """A headless HA that models its home with input_boolean helpers + a
    thermostat (input_number) + a temperature sensor — the owner's real setup.
    None of these appear under /lights or /switches."""

    def __init__(self):
        self.controls: list[dict] = []

    async def get(self, path):
        if path == "/lights":
            return {"lights": [], "count": 0}
        if path == "/switches":
            return {"switches": [], "count": 0}
        if path == "/entities?domain=input_boolean":
            return {"entities": [
                {"entity_id": "input_boolean.living_room_light", "state": "on",
                 "attributes": {"icon": "mdi:ceiling-light", "friendly_name": "Living Room Light"}},
                {"entity_id": "input_boolean.kitchen_light", "state": "off",
                 "attributes": {"icon": "mdi:ceiling-light", "friendly_name": "Kitchen Light"}},
                {"entity_id": "input_boolean.fan", "state": "off",
                 "attributes": {"icon": "mdi:fan", "friendly_name": "Ceiling Fan"}},
                {"entity_id": "input_boolean.tv", "state": "off",
                 "attributes": {"icon": "mdi:television", "friendly_name": "TV"}},
            ]}
        if path == "/entities?domain=input_number":
            return {"entities": [
                {"entity_id": "input_number.thermostat_temperature", "state": "22.0",
                 "attributes": {"unit_of_measurement": "°C", "friendly_name": "Thermostat"}},
            ]}
        if path == "/sensors":
            return {"sensors": [
                {"entity_id": "sensor.current_temperature", "state": "21.0",
                 "attributes": {"device_class": "temperature", "unit_of_measurement": "°C",
                                "friendly_name": "Current Temperature"}},
            ]}
        if path == "/scenes":
            return {"scenes": [{"entity_id": "scene.good_night", "name": "Good Night"}]}
        return None

    async def post(self, path, payload):
        if path == "/devices/control":
            self.controls.append(payload)
            return {"message": f"Successfully executed {payload['action']} on {payload['entity_id']}", "result": []}
        return None


@pytest.fixture
def helper_bridge(monkeypatch):
    fake = _HelperBridge()
    monkeypatch.setattr(smart_home_service, "_ha_get", lambda path: fake.get(path))
    monkeypatch.setattr(smart_home_service, "_ha_post", lambda path, payload: fake.post(path, payload))
    return fake


@pytest.mark.asyncio
async def test_input_boolean_helpers_surface_as_devices(helper_bridge):
    # The real owner-visible fix: a home modelled with input_boolean helpers must
    # NOT read as empty — the helpers become controllable device tiles.
    devices = await smart_home_service.list_devices()
    by_name = {d["name"]: d for d in devices}
    assert {"Living Room Light", "Kitchen Light", "Ceiling Fan", "TV"} <= set(by_name)
    assert by_name["Living Room Light"]["domain"] == "light"
    assert by_name["Ceiling Fan"]["domain"] == "fan"
    assert by_name["TV"]["domain"] == "tv"
    assert by_name["Living Room Light"]["on"] is True
    # input_boolean has no brightness channel.
    assert by_name["Living Room Light"]["dimmable"] is False


@pytest.mark.asyncio
async def test_status_card_groups_rooms_and_reads_climate(helper_bridge):
    r = await smart_home_service.resolve_smart_home(_Intent("status"))
    props = r["cards"][0]["props"]
    # Grouped by inferred room; the fan/TV (no room word) fall into the catch-all.
    room_names = [g["name"] for g in props["rooms"]]
    assert "Living Room" in room_names and "Kitchen" in room_names
    assert room_names[-1] == "Around the home"  # catch-all sorts last
    around = next(g for g in props["rooms"] if g["name"] == "Around the home")
    assert {"Ceiling Fan", "TV"} == {d["name"] for d in around["devices"]}
    # Read-only comfort strip surfaces current temp + set-point.
    assert props["climate"] == {"current": 21.0, "target": 22.0, "unit": "°C"}
    # The grow affordance the owner asked for is always present.
    assert props["add_query"] == "add a device"


@pytest.mark.asyncio
async def test_input_boolean_tile_controls_exact_entity(helper_bridge):
    # A helper tile round-trips through the classifier and toggles THAT entity.
    i = classify_skybridge_intent("turn on Kitchen Light @input_boolean.kitchen_light", None)
    assert i is not None and i.entity_id == "input_boolean.kitchen_light"
    await smart_home_service.resolve_smart_home(i)
    assert helper_bridge.controls == [{"entity_id": "input_boolean.kitchen_light", "action": "turn_on"}]


@pytest.mark.asyncio
async def test_add_device_returns_setup_card_with_qr():
    # "Add a device" resolves without touching the hub — it shows a QR the owner
    # scans with their phone. No fake success: the card is a guided setup surface.
    r = await smart_home_service.resolve_smart_home(_Intent("add_device"))
    props = r["cards"][0]["props"]
    assert props["mode"] == "setup"
    assert props["qr_path"].startswith("/api/home/setup/qr?token=")
    assert props["back_query"] == "smart home"
    assert r["handled"] is True


@pytest.mark.asyncio
async def test_empty_home_is_inviting_not_offline(monkeypatch):
    # A reachable hub with zero devices is an inviting empty card (add-a-device),
    # NOT the offline error card.
    async def _get(path):
        if path in ("/lights",):
            return {"lights": []}
        if path == "/switches":
            return {"switches": []}
        if path == "/entities?domain=input_boolean":
            return {"entities": []}
        return None
    monkeypatch.setattr(smart_home_service, "_ha_get", _get)
    r = await smart_home_service.resolve_smart_home(_Intent("status"))
    props = r["cards"][0]["props"]
    assert props.get("offline") is not True
    assert props["devices"] == [] and props["rooms"] == []
    assert props["add_query"] == "add a device"


@pytest.mark.asyncio
async def test_voice_satellite_internals_are_hidden(monkeypatch):
    # Zoe's own satellite exposes switch.lva_*_mute / _thinking_sound — plumbing
    # that must never appear as a household device on the card.
    async def _get(path):
        if path == "/lights":
            return {"lights": []}
        if path == "/switches":
            return {"switches": [
                {"entity_id": "switch.lva_abc_mute", "name": "zoe-touch-pi Mute", "state": "unavailable"},
                {"entity_id": "switch.lva_abc_thinking_sound", "name": "zoe-touch-pi Thinking Sound", "state": "unavailable"},
                {"entity_id": "switch.coffee", "name": "Coffee Plug", "state": "on"},
            ]}
        if path == "/entities?domain=input_boolean":
            return {"entities": []}
        return None
    monkeypatch.setattr(smart_home_service, "_ha_get", _get)
    devices = await smart_home_service.list_devices()
    names = {d["name"] for d in devices}
    assert names == {"Coffee Plug"}  # both lva_ internals filtered out


@pytest.mark.parametrize("q", [
    "add a device",
    "set up a device",
    "add a new light",
    "connect a plug",
    "pair a sensor",
    "add a smart switch",
    "set up a speaker",
])
def test_add_device_classifier(q):
    i = classify_skybridge_intent(q, None)
    assert i is not None and i.domain == "smart_home" and i.action == "add_device", q
