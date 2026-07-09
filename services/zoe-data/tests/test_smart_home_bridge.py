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
    def __init__(self, action, query="", brightness=0):
        self.domain = "smart_home"
        self.action = action
        self.query = query
        self.duration_seconds = brightness


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
            ]}
        if path == "/switches":
            return {"switches": [
                {"entity_id": "switch.plug", "name": "Coffee Plug", "state": self._switch_state},
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
    assert {d["name"] for d in props["devices"]} == {"Living Room Lamp", "Coffee Plug"}
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
    await smart_home_service.resolve_smart_home(_Intent("set_brightness", query="lamp", brightness=40))
    assert bridge.controls[-1] == {
        "entity_id": "light.lamp", "action": "turn_on", "data": {"brightness_pct": 40}}


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
