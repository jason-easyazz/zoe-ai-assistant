"""Smart-home bridge latency: bounded calls + honest spoken fallback.

Live 2026-11-05: a plain "turn off the light" took ~24s on a slow bridge (a flat
6s per call across ~13 sequential calls). Reads are now concurrent, the post-command
refresh is bounded, the per-call timeout is env-tunable (default 4s) and the whole
turn runs under ONE budget. A timed-out command is never reported as "done" or as a
proven failure. Fake bridge only (ci_safe); synthetic device names."""
import asyncio

import pytest

import smart_home_service as shs

pytestmark = pytest.mark.ci_safe


class _Intent:
    def __init__(self, action, query="", entity_id=""):
        self.action, self.query, self.entity_id = action, query, entity_id
        self.entity_ids, self.duration_seconds = (), 0


def _install(monkeypatch, *, control):
    async def _get(path):
        if path == "/lights":
            return {"lights": [{"entity_id": "light.den", "state": "on", "attributes": {"friendly_name": "Den Light"}}]}
        if path in ("/switches", "/scenes") or path.startswith("/entities"):
            return {"switches": [], "entities": [], "scenes": []}
        return None

    async def _post(path, payload):
        return control(path, payload)

    monkeypatch.setattr(shs, "_ha_get", _get)
    monkeypatch.setattr(shs, "_ha_post", _post)


@pytest.mark.asyncio
async def test_control_timeout_is_spoken_honestly(monkeypatch):
    def control(path, payload):
        marks = shs._TIMED_OUT.get()
        marks.append(path)  # what _ha_post does on httpx.TimeoutException
        return None

    _install(monkeypatch, control=control)
    out = await shs.resolve_smart_home(_Intent("turn_off", query="light"))
    spoken = out["spoken_summary"]
    assert "taking longer than usual" in spoken
    assert not spoken.startswith("Turned") and "couldn't reach" not in spoken


@pytest.mark.asyncio
async def test_plain_failure_is_still_a_failure_and_success_still_success(monkeypatch):
    # negative controls: the new wording is reserved for timeouts only
    _install(monkeypatch, control=lambda p, pl: None)
    out = await shs.resolve_smart_home(_Intent("turn_off", query="light"))
    assert "couldn't reach Den Light" in out["spoken_summary"]
    _install(monkeypatch, control=lambda p, pl: {"message": "Successfully executed turn_off", "result": []})
    out = await shs.resolve_smart_home(_Intent("turn_off", query="light"))
    assert out["spoken_summary"] == "Turned Den Light off."


@pytest.mark.asyncio
async def test_turn_budget_bounds_a_hung_resolver(monkeypatch):
    async def hung(intent, room_eids=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(shs, "resolve_smart_home", hung)
    monkeypatch.setattr(shs, "turn_budget_s", lambda: 0.05)
    out = await asyncio.wait_for(shs.resolve_smart_home_bounded(_Intent("turn_off", query="light")), 2)
    assert "taking longer than usual" in out["spoken_summary"]
    assert out["handled"] is True


@pytest.mark.asyncio
async def test_budget_does_not_touch_a_fast_turn(monkeypatch):
    _install(monkeypatch, control=lambda p, pl: {"message": "Successfully executed turn_off", "result": []})
    out = await shs.resolve_smart_home_bounded(_Intent("turn_off", query="light"))
    assert out["spoken_summary"] == "Turned Den Light off."


def test_timeouts_are_env_tunable_and_clamped(monkeypatch):
    monkeypatch.delenv("ZOE_HA_CALL_TIMEOUT_S", raising=False)
    assert shs._call_timeout_s() == 4.0
    monkeypatch.setenv("ZOE_HA_CALL_TIMEOUT_S", "2.5")
    assert shs._call_timeout_s() == 2.5
    monkeypatch.setenv("ZOE_HA_CALL_TIMEOUT_S", "garbage")
    assert shs._call_timeout_s() == 4.0
    monkeypatch.setenv("ZOE_HA_TURN_BUDGET_S", "0")
    assert shs.turn_budget_s() == 1.0


def _install_two_lights_and_scene(monkeypatch, *, control):
    async def _get(path):
        if path == "/lights":
            return {"lights": [
                {"entity_id": "light.den", "state": "on", "attributes": {"friendly_name": "Den Light"}},
                {"entity_id": "light.hall", "state": "on", "attributes": {"friendly_name": "Hall Light"}}]}
        if path == "/scenes":
            return {"scenes": [{"entity_id": "scene.movie", "attributes": {"friendly_name": "Movie"}}]}
        if path in ("/switches",) or path.startswith("/entities"):
            return {"switches": [], "entities": []}
        return None

    async def _post(path, payload):
        return control(path, payload)

    monkeypatch.setattr(shs, "_ha_get", _get)
    monkeypatch.setattr(shs, "_ha_post", _post)


@pytest.mark.asyncio
async def test_partial_multi_device_timeout_is_not_reported_as_all_done(monkeypatch):
    def control(path, payload):
        if "hall" in str(payload):
            shs._TIMED_OUT.get().append(path)  # the second device timed out
            return None
        return {"message": "Successfully executed turn_off", "result": []}

    _install_two_lights_and_scene(monkeypatch, control=control)
    out = await shs.resolve_smart_home(_Intent("turn_off", query="lights"))
    spoken = out["spoken_summary"]
    assert "taking longer than usual" in spoken and "1 of 2" in spoken
    assert spoken != "Turned 2 devices off."


@pytest.mark.asyncio
async def test_scene_timeout_is_spoken_honestly_and_plain_failure_is_not(monkeypatch):
    def timed_out(path, payload):
        shs._TIMED_OUT.get().append(path)
        return None

    _install_two_lights_and_scene(monkeypatch, control=timed_out)
    out = await shs.resolve_smart_home(_Intent("activate_scene", query="movie"))
    assert "taking longer than usual" in out["spoken_summary"]
    _install_two_lights_and_scene(monkeypatch, control=lambda p, pl: None)
    out = await shs.resolve_smart_home(_Intent("activate_scene", query="movie"))
    assert "couldn't activate" in out["spoken_summary"]
