"""Running a scene, a script or an automation: adult-only when it unlocks or opens anything, or when Zoe cannot read it
(``home_runnables.py`` + ``home_opening.refused``; owner's rule 2026-11-23, no flag).

Every path that can run one: the panel's ``/api/ha/control`` route, the brain's ``ha_control`` tool, and the spoken scene
(``smart_home_service.activate_scene`` and the resolver's scene action). What each one DOES is read from the bridge; here the
bridge's answer is faked per entity, except the last section, which runs the bridge's REAL endpoint code over a faked Home
Assistant. Each test names the control that was run against it. Invented people, scenes and devices only.
"""
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import home_opening
import home_runnables
import smart_home_service
import zoe_agent
from routers import ha_control
from test_ha_control_auth import PANEL_IP, PROXY, _env, _ha, registry  # noqa: F401 - fixtures
from test_home_opening import ADULT, BARE_PANEL, CHILD, GUEST, KID, MEMBER, NOBODY, house  # noqa: F401 - fixtures

pytestmark = pytest.mark.ci_safe
REAL_READ = home_runnables._read      # captured at import, before the conftest fixture replaces it


def _acts(*rows):
    return {"readable": True, "actions": [dict(zip(("entity_id", "service", "state"), r)) for r in rows]}


DEFS = {
    "scene.movie_time": _acts(("light.den_lamp", "", "on"), ("media_player.den", "", "playing")),
    "scene.leaving": _acts(("light.hall", "", "off"), ("lock.front_door", "", "locked"), ("cover.garage_door", "", "closed"),
                           ("alarm_control_panel.house", "", "armed_away")),
    "script.goodnight": _acts(("light.hall", "light.turn_off", ""), ("lock.front_door", "lock.lock", ""),
                              ("cover.blind", "cover.close_cover", ""), ("", "notify.send_message", "")),
    "scene.arrive_home": _acts(("light.hall", "", "on"), ("lock.front_door", "", "unlocked")),
    "scene.air_the_house": _acts(("cover.garage_door", "", "open")),
    "scene.members_only": _acts(("valve.water_main", "", "")),                     # a member with no known target state
    "script.let_in": _acts(("lock.front_door", "lock.unlock", "")),
    "script.generic_on": _acts(("cover.garage_door", "homeassistant.turn_on", "")),
    "script.chain": _acts(("script.other", "script.turn_on", "")),
    "script.calls_a_scene": _acts(("scene.arrive_home", "scene.turn_on", "")),
    "script.templated": _acts(("{{ target }}", "light.turn_on", "")),
    "script.group": _acts(("group.all_doors", "homeassistant.turn_off", "")),
    "script.shell": _acts(("", "shell_command.open_gate", "")),
    "script.said_unreadable": {"readable": False, "reason": "step_kind"},
    "scene.garbage": ["not", "the", "shape"],
    "scene.empty": {"readable": True, "actions": []},
}
SAFE = ["scene.movie_time", "scene.leaving", "script.goodnight"]
ADULT_ONLY = ["scene.arrive_home", "scene.air_the_house", "scene.members_only", "script.let_in", "script.generic_on",
              "script.chain", "script.calls_a_scene", "script.templated", "script.group", "script.shell",
              "script.said_unreadable", "scene.garbage", "scene.empty", "scene.not_there", "scene.slow", "automation.morning"]


@pytest.fixture
def reads(house, monkeypatch):
    """The bridge's answers; ``reads`` lists every read. An entity not in ``DEFS`` is a 404 (None); ``scene.slow`` times out.
    ``house["sent"]`` (from ``test_home_opening``) is every command that reached the bridge."""
    calls = []

    async def read(entity_id):
        calls.append(entity_id)
        if entity_id == "scene.slow":
            raise httpx.ReadTimeout("")
        return DEFS.get(entity_id)

    async def spoken_bridge(path, payload):
        house["sent"].append((path, payload.get("scene_id") or payload.get("entity_id"), payload.get("action", "turn_on")))
        return {"message": "Successfully executed", "result": {}}

    monkeypatch.setattr(home_runnables, "_read", read)
    monkeypatch.setattr(smart_home_service, "_ha_post", spoken_bridge)
    return calls


def test_the_verdict_for_each_definition():
    """Control run: with ``unlocked`` added to ``CLOSED_STATES`` ``scene.arrive_home`` reads safe and this fails; with the
    runnable / group line dropped from ``classify`` the chained script reads safe and this fails."""
    assert [home_runnables.classify(DEFS[e]) for e in SAFE] == ["safe"] * len(SAFE)
    assert {e: home_runnables.classify(DEFS.get(e)) for e in ADULT_ONLY[:5]} == dict.fromkeys(ADULT_ONLY[:5], "opens")
    assert {home_runnables.classify(DEFS.get(e)) for e in ADULT_ONLY[5:]} == {"unreadable"}
    for junk in (None, "", 7, {"readable": "yes", "actions": []}, {"readable": True, "actions": [["light.a", "", ""]]},
                 {"readable": True, "actions": [{"entity_id": "light.a", "service": "", "state": None}]},
                 {"readable": True, "actions": [{"entity_id": "all", "service": "light.turn_on", "state": ""}]}):
        assert home_runnables.classify(junk) == "unreadable"


# ---- the panel's control route ---------------------------------------------------------------------------------------------------
async def _route(caller, entity, action="turn_on", params=None):
    return await ha_control.ha_control(ha_control.HAControlPayload(entity_id=entity, action=action, params=params), caller=caller)


@pytest.mark.parametrize("entity", ADULT_ONLY)
async def test_the_route_runs_an_opening_or_unreadable_one_for_an_adult_only(reads, house, entity):
    """Control run: with the scene / script branch dropped from ``home_opening.refused`` the child's run is sent."""
    for caller in NOBODY:
        with pytest.raises(HTTPException) as exc:
            await _route(caller or {}, entity)
        assert exc.value.status_code == 403 and exc.value.detail == home_opening.REFUSAL
    assert house["sent"] == []
    assert (await _route(MEMBER, entity))["ok"] is True and house["sent"] == [("/devices/control", entity, "turn_on")]


@pytest.mark.parametrize("caller", [MEMBER, KID, GUEST, BARE_PANEL])
async def test_the_route_runs_a_harmless_one_for_everyone_but_only_as_it_is_defined(reads, house, caller):
    """Control run: with ``classify`` answering unreadable for everything the child's film scene is refused; with the
    ``RUN_SERVICES`` test dropped a child's ``apply`` on a harmless scene is sent."""
    for entity in SAFE:
        assert (await _route(caller, entity))["ok"] is True
    assert (await _route(caller, "light.kitchen_lamp", "turn_on", {"entity_id": ["light.a", "scene.movie_time"]}))["ok"] is True
    assert len(house["sent"]) == len(SAFE) + 1
    with pytest.raises(HTTPException) if caller is not MEMBER else _nothing():
        await _route(caller, "scene.movie_time", "apply", {"entities": {"lock.front_door": "unlocked"}})
    with pytest.raises(HTTPException) if caller is not MEMBER else _nothing():
        await _route(caller, "light.kitchen_lamp", "turn_on", {"entity_id": ["light.a", "script.let_in"]})


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_panel_runs_an_opening_scene_only_inside_an_adults_verified_voice_session(reads, house, registry):
    """Real HTTP and the real panel test, the kiosk at its registered address. Control run: with ``may_open`` not asking
    whether the session's member is an adult the child's session runs the scene."""
    def run(entity):
        return _ha(PROXY).post("/api/ha/control", json={"entity_id": entity, "action": "turn_on"}, headers={"X-Real-IP": PANEL_IP})

    assert run("scene.arrive_home").json()["detail"] == home_opening.REFUSAL and run("scene.movie_time").status_code == 200
    house["session"] = CHILD
    assert run("scene.arrive_home").status_code == 403 and run("automation.morning").status_code == 403
    house["session"] = ADULT
    assert run("scene.arrive_home").status_code == 200 and run("automation.morning").status_code == 200
    assert [s[1] for s in house["sent"]] == ["scene.movie_time", "scene.arrive_home", "automation.morning"]


# ---- the brain's tool ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("entity", ADULT_ONLY)
async def test_the_tool_runs_an_opening_or_unreadable_one_for_an_adults_turn_only(reads, house, entity):
    """Control run: with the scene / script branch dropped from ``home_opening.refused`` the child's turn runs it."""
    for turn in NOBODY:
        home_opening.note_turn(turn)
        assert await zoe_agent._ha_control(entity, "turn_on") == {"error": home_opening.REFUSAL, "status": "refused"}
    assert house["sent"] == []
    home_opening.note_turn(MEMBER)
    assert await zoe_agent._ha_control(entity, "turn_on") == {"ok": True}
    home_opening.note_turn(KID)
    assert all([await zoe_agent._ha_control(e, "turn_on") == {"ok": True} for e in SAFE]) and len(house["sent"]) == 1 + len(SAFE)


# ---- the spoken scene ------------------------------------------------------------------------------------------------------------
async def test_a_spoken_scene_is_run_for_an_adult_only_when_it_opens_or_cannot_be_read(reads, house, monkeypatch):
    """The REAL ``resolve_smart_home`` scene action (a scene chip's exact id, then a spoken name) and ``activate_scene``.
    Controls run: with the check dropped from the resolver's scene action the sentence is not said; with it dropped from
    ``activate_scene`` the child's direct call is sent."""
    async def listing(path):
        scenes = [{"entity_id": e, "name": e.split(".")[1].replace("_", " ")} for e in ("scene.arrive_home", "scene.movie_time")]
        return {"/lights": {"lights": []}, "/switches": {"switches": []}, "/scenes": {"scenes": scenes}}.get(path, {"entities": []})

    monkeypatch.setattr(smart_home_service, "_ha_get", listing)
    for turn in NOBODY:
        home_opening.note_turn(turn)
        for said in (SimpleNamespace(action="activate_scene", query="", entity_id="scene.arrive_home"),
                     SimpleNamespace(action="activate_scene", query="arrive home", entity_id=""),
                     SimpleNamespace(action="activate_scene", query="", entity_id="script.let_in")):
            out = await smart_home_service.resolve_smart_home(said)
            assert out["spoken_summary"] == home_opening.REFUSAL and out["cards"] == []
        assert await smart_home_service.activate_scene("scene.arrive_home") is False
        assert await smart_home_service.activate_scene("scene.not_there") is False
    assert house["sent"] == []
    home_opening.note_turn(KID)
    out = await smart_home_service.resolve_smart_home(SimpleNamespace(action="activate_scene", query="movie time", entity_id=""))
    assert out["spoken_summary"].startswith("Activated")
    home_opening.note_turn(MEMBER)
    out = await smart_home_service.resolve_smart_home(SimpleNamespace(action="activate_scene", query="arrive home", entity_id=""))
    assert out["spoken_summary"].startswith("Activated")
    assert [s[:2] for s in house["sent"]] == [("/scenes/activate", "scene.movie_time"), ("/scenes/activate", "scene.arrive_home")]


# ---- the cost, the cache and the log ---------------------------------------------------------------------------------------------
async def test_an_ordinary_command_and_an_adult_read_nothing(reads, house):
    """Outbound-read count. Control run: with ``refused`` classifying before it asks whether the target is a scene, script or
    automation (or before it asks who is calling) the count is not zero."""
    for turn in (KID, MEMBER):
        home_opening.note_turn(turn)
        assert (await _route(turn, "light.kitchen_lamp"))["ok"] is True
        assert await zoe_agent._ha_control("switch.kettle_plug", "toggle") == {"ok": True}
        assert await smart_home_service.set_device("light.kitchen_lamp", True) is True
        assert await zoe_agent._ha_control("lock.front_door", "lock") == {"ok": True}
    assert (await _route(MEMBER, "scene.arrive_home"))["ok"] is True and await zoe_agent._ha_control("script.let_in", "turn_on") == {"ok": True}
    assert reads == []


async def test_a_reading_is_kept_for_a_while_and_an_unreadable_one_only_briefly(reads, house, monkeypatch):
    """Controls run: with the cache never consulted the second run reads again; with an unreadable verdict kept for the full
    ``TTL_S`` the restarted bridge is not noticed after ``UNREADABLE_TTL_S``."""
    now = [1000.0]
    monkeypatch.setattr(home_runnables, "_clock", lambda: now[0])
    for _ in range(3):
        assert await home_opening.refused("scene.movie_time", "turn_on", user=KID) is False
        assert await home_opening.refused("scene.not_there", "turn_on", user=KID) is True
    assert reads == ["scene.movie_time", "scene.not_there"]
    now[0] += home_runnables.UNREADABLE_TTL_S + 1
    monkeypatch.setitem(DEFS, "scene.not_there", DEFS["scene.movie_time"])           # the bridge was restarted and can read it
    assert await home_opening.refused("scene.not_there", "turn_on", user=KID) is False
    assert await home_opening.refused("scene.movie_time", "turn_on", user=KID) is False and len(reads) == 3
    now[0] += home_runnables.TTL_S + 1
    monkeypatch.setitem(DEFS, "scene.movie_time", DEFS["scene.arrive_home"])         # the scene was edited to unlock the door
    assert await home_opening.refused("scene.movie_time", "turn_on", user=KID) is True and len(reads) == 4
    assert set(home_runnables._CACHE) == {"scene.movie_time", "scene.not_there"}     # ids and verdicts, nothing else
    assert {v for v, _ in home_runnables._CACHE.values()} <= {"safe", "opens", "unreadable"}


async def test_a_refused_run_logs_closed_codes_only(reads, house, caplog):
    """Control run: with the entity id added to the log line this fails."""
    with caplog.at_level(logging.DEBUG):
        assert await home_opening.refused("scene.arrive_home", "turn_on", user=KID, via="panel_route") is True
        assert await home_opening.refused("automation.morning", "trigger", user=KID, via="brain_tool") is True
    assert [r.getMessage() for r in caplog.records if "HOME_OPENING" in r.getMessage()] == [
        "HOME_OPENING refused via=panel_route domain=scene", "HOME_OPENING refused via=brain_tool domain=automation"]
    assert "arrive_home" not in caplog.text and "morning" not in caplog.text


# ---- the bridge's real endpoint, over a faked Home Assistant ---------------------------------------------------------------------
HA = {
    "states/scene.movie_time": {"attributes": {"friendly_name": "Film night", "entity_id": ["light.den_lamp"], "id": "1700000000001"}},
    "config/scene/config/1700000000001": {"name": "Film night", "entities": {"light.den_lamp": {"state": "on", "brightness": 90}}},
    "states/scene.arrive_home": {"attributes": {"entity_id": ["light.hall", "lock.front_door"], "id": "1700000000002"}},
    "config/scene/config/1700000000002": {"entities": {"light.hall": {"state": "on"}, "lock.front_door": {"state": "unlocked"}}},
    "states/scene.leaving": {"attributes": {"entity_id": ["lock.front_door"], "id": "1700000000003"}},
    "config/scene/config/1700000000003": {"entities": {"lock.front_door": "locked"}},
    "states/scene.yaml_lights": {"attributes": {"entity_id": ["light.hall", "switch.kettle_plug"]}},      # no config id
    "states/scene.yaml_lock": {"attributes": {"entity_id": ["lock.front_door"]}},
    "states/scene.no_members": {"attributes": {"friendly_name": "Odd one"}},
    "config/script/config/goodnight": {"alias": "Good night all", "sequence": [
        {"action": "light.turn_off", "target": {"entity_id": ["light.hall", "light.den_lamp"]}},
        {"delay": "00:00:02"},
        {"if": [{"condition": "state", "entity_id": "lock.front_door", "state": "unlocked"}],
         "then": [{"service": "lock.lock", "entity_id": "lock.front_door"}], "else": [{"action": "notify.send_message", "data": {"message": "Sleep well"}}]},
        {"choose": [{"conditions": [], "sequence": [{"action": "cover.close_cover", "data": {"entity_id": "cover.blind, cover.garage_door"}}]}],
         "default": [{"repeat": {"count": 2, "sequence": [{"parallel": [{"action": "light.turn_off", "target": {"entity_id": "light.porch"}}]}]}}]}]},
    "config/script/config/let_in": {"sequence": [{"action": "light.turn_on", "target": {"entity_id": "light.hall"}},
                                                 {"choose": [{"sequence": [{"action": "lock.unlock", "target": {"entity_id": "lock.front_door"}}]}]}]},
    "config/script/config/by_area": {"sequence": [{"action": "cover.close_cover", "target": {"area_id": "garage"}}]},
    "config/script/config/by_device": {"sequence": [{"action": "light.turn_on", "target": {"entity_id": "light.hall"}},
                                                    {"device_id": "abc123", "domain": "lock", "type": "unlock", "entity_id": "lock.front_door"}]},
    "config/script/config/templated": {"sequence": [{"action": "light.turn_on", "target": {"entity_id": "{{ which }}"}}]},
    "config/script/config/templated_service": {"sequence": [{"action": "{{ what }}", "target": {"entity_id": "light.hall"}}]},
    "config/script/config/runs_scene": {"sequence": [{"scene": "scene.arrive_home"}]},
    "config/script/config/fires_event": {"sequence": [{"event": "let_them_in"}]},
    "config/script/config/odd_shape": {"sequence": "light.turn_on"},
}
BRIDGE_SAFE = ["scene.movie_time", "scene.leaving", "script.goodnight"]
BRIDGE_ADULT_ONLY = ["scene.arrive_home", "scene.yaml_lights", "scene.yaml_lock", "scene.no_members", "scene.not_there", "script.let_in", "script.by_area",
                     "script.by_device", "script.templated", "script.templated_service", "script.runs_scene", "script.fires_event",
                     "script.odd_shape", "script.not_there", "script.ha_is_down", "script.Bad-Id", "automation.morning"]


@pytest.fixture
def real_bridge(house, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "ha_mcp_bridge_main", Path(__file__).resolve().parents[2] / "homeassistant-mcp-bridge" / "main.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    async def home_assistant(method, endpoint, data=None):
        if endpoint.endswith("ha_is_down"):
            raise bridge.HTTPException(status_code=503, detail="Cannot connect to Home Assistant")
        if endpoint in HA_FAILS:
            raise bridge.HTTPException(status_code=HA_FAILS[endpoint], detail="Home Assistant request failed")
        if endpoint in MORE_HA:
            return MORE_HA[endpoint]
        if method != "GET" or endpoint not in HA:
            raise bridge.HTTPException(status_code=404, detail="Home Assistant request failed")
        return HA[endpoint]

    async def read(entity_id):
        return json.loads(json.dumps(await bridge.get_definition(entity_id)))      # what crosses the wire

    monkeypatch.setattr(bridge.ha_bridge, "_make_request", home_assistant)
    monkeypatch.setattr(home_runnables, "_read", read)
    return bridge


async def test_the_real_bridge_endpoint_reads_each_definition_and_zoe_decides(real_bridge, house):
    """The bridge's REAL ``get_definition`` (only Home Assistant is faked) feeding the real reader, classifier and route.
    Controls run: with the bridge treating a device action as a no-op ``script.by_device`` runs for the child; with its
    area / device target test dropped ``script.by_area`` does; with a scene that has no config id read from its member list
    alone (the old behaviour) ``scene.yaml_lights`` does."""
    for entity in BRIDGE_SAFE:
        assert (await _route(KID, entity))["ok"] is True
    for entity in BRIDGE_ADULT_ONLY:
        with pytest.raises(HTTPException) as exc:
            await _route(KID, entity)
        assert exc.value.status_code == 403, entity
    assert [s[1] for s in house["sent"]] == BRIDGE_SAFE
    assert (await _route(MEMBER, "script.let_in"))["ok"] is True
    body = await real_bridge.get_definition("script.goodnight")
    assert body == {"readable": True, "actions": [
        {"service": s, "entity_id": e, "state": ""} for s, e in [
            ("light.turn_off", "light.hall"), ("light.turn_off", "light.den_lamp"), ("lock.lock", "lock.front_door"),
            ("notify.send_message", ""), ("cover.close_cover", "cover.blind"), ("cover.close_cover", "cover.garage_door"),
            ("light.turn_off", "light.porch")]]}
    texts = json.dumps([await real_bridge.get_definition(e) for e in BRIDGE_SAFE + BRIDGE_ADULT_ONLY])
    assert "Film night" not in texts and "Good night" not in texts and "Sleep well" not in texts and "brightness" not in texts


async def test_a_scene_without_a_config_id_is_unreadable_and_free_text_states_are_never_repeated(real_bridge, monkeypatch):
    """A YAML scene has no config the API can read: unreadable, whatever its members are. And a member's target state is
    repeated only from a fixed vocabulary, so an input_text's words never leave the bridge.
    Controls run: the ``no_config_id`` refusal removed -> the first assert fails; ``_scene_state`` returning any string ->
    the last assert fails."""
    for entity in ("scene.yaml_lights", "scene.yaml_lock"):
        assert await real_bridge.get_definition(entity) == {"readable": False, "reason": "no_config_id"}
    monkeypatch.setitem(MORE_HA, "states/scene.words", {"attributes": {"entity_id": ["input_text.note", "light.hall"], "id": "1700000000009"}})
    monkeypatch.setitem(MORE_HA, "config/scene/config/1700000000009", {"entities": {"input_text.note": {"state": "womble at noon"},
                                                                                   "light.hall": {"state": "ON "}}})
    body = await real_bridge.get_definition("scene.words")
    assert body == {"readable": True, "actions": [{"entity_id": "input_text.note", "service": "", "state": ""},
                                                  {"entity_id": "light.hall", "service": "", "state": "on"}]}


async def test_a_bridge_without_the_endpoint_makes_every_scene_adult_only_and_never_an_error(house, monkeypatch):
    """Day one: the bridge container has not been restarted, so ``GET /definitions/...`` is a 404. Real ``_read`` over a
    faked HTTP client. Controls run: with ``_read`` returning the body of a non-200 the 500's body is classified and the child
    runs the scene; with ``verdict`` not catching a failed read the timeout raises out of the route."""
    answers = {"status": 404}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            if answers["status"] is None:
                raise httpx.ConnectTimeout("")
            return SimpleNamespace(status_code=answers["status"], json=lambda: {"detail": "Not Found"} if answers["status"] == 404 else DEFS["scene.movie_time"])

    monkeypatch.setattr(home_runnables, "_read", REAL_READ)
    monkeypatch.setattr(home_runnables.httpx, "AsyncClient", Client)
    for status in (404, None, 500):
        answers["status"] = status
        home_runnables._CACHE.clear()
        for caller in (KID, GUEST, BARE_PANEL):
            with pytest.raises(HTTPException) as exc:
                await _route(caller, "scene.movie_time")
            assert exc.value.status_code == 403 and exc.value.detail == home_opening.REFUSAL
        assert await smart_home_service.activate_scene("scene.movie_time") is False
    assert house["sent"] == [] and (await _route(MEMBER, "scene.movie_time"))["ok"] is True      # an adult still runs it
    answers["status"] = 200
    home_runnables._CACHE.clear()                                                                # the bridge was restarted
    assert (await _route(KID, "scene.movie_time"))["ok"] is True


# ---- review fixes 2026-11-23: what the reader must not miss -----------------------------------------------------------------------
def _script(*steps):
    return {"sequence": list(steps)}


def _ui_scene(number, entities, members=("light.hall",)):
    return {"states/scene.s%d" % number: {"attributes": {"entity_id": list(members), "id": "17000000009%02d" % number}},
            "config/scene/config/17000000009%02d" % number: {"entities": entities}}


_GARAGE = {"action": "homeassistant.turn_on", "data": {"area_id": "garage"}}
MORE_HA = {
    # an area, a device, a label or a floor in ``data`` / ``service_data``: Home Assistant resolves it exactly as in ``target``
    "config/script/config/area_in_data": _script(_GARAGE),
    "config/script/config/area_in_service_data": _script(
        {"service": "homeassistant.turn_on", "target": {"entity_id": "light.hall"}, "service_data": {"area_id": ["garage"]}}),
    "config/script/config/device_in_data": _script({"action": "homeassistant.toggle", "data": {"device_id": "abc123"}}),
    "config/script/config/label_in_data": _script({"action": "homeassistant.turn_on", "data": {"label_id": "doors"}}),
    "config/script/config/floor_in_data": _script({"action": "homeassistant.turn_on", "data": {"floor_id": "ground"}}),
    "config/script/config/templated_area": _script({"action": "homeassistant.turn_on", "data": {"area_id": "{{ where }}"}}),
    "config/script/config/area_beside_an_entity": _script(
        {"action": "homeassistant.turn_on", "data": {"entity_id": "light.hall", "area_id": "garage"}}),
    "config/script/config/area_deep_in_data": _script(
        {"action": "homeassistant.turn_on", "data": {"entity_id": "light.hall", "target": {"area_id": "garage"}}}),
    "config/script/config/area_in_a_nest": _script({"choose": [{"conditions": [], "sequence": [
        {"if": [], "then": [{"repeat": {"count": 1, "sequence": [{"parallel": [{"sequence": [_GARAGE]}]}]}}]}]}]}),
    # both blocks are sent: the lock in the second one was never read
    "config/script/config/lock_in_the_second_block": _script(
        {"action": "homeassistant.turn_on", "data": {"entity_id": "light.hall"}, "service_data": {"entity_id": "lock.front_door"}}),
    "config/script/config/two_names_for_a_call": _script({"action": "notify.send_message", "service": "lock.unlock"}),
    "config/script/config/whole_data_templated": _script({"action": "homeassistant.turn_on", "data": "{{ what }}"}),
    "config/script/config/whole_target_templated": _script({"action": "cover.open_cover", "target": "{{ what }}"}),
    "config/script/config/service_template": _script({"service_template": "{{ what }}", "entity_id": "light.hall"}),
    "config/script/config/data_template": _script(
        {"service": "homeassistant.turn_on", "data_template": {"entity_id": "{{ which }}"}}),
    "config/script/config/templated_in_a_list": _script({"action": "light.turn_on", "target": {"entity_id": ["light.hall", "{{ x }}"]}}),
    "config/script/config/entity_all": _script({"action": "homeassistant.turn_off", "entity_id": "all"}),
    "config/script/config/toggles_a_lock": _script({"action": "homeassistant.toggle", "target": {"entity_id": "lock.front_door"}}),
    "config/script/config/runs_a_script_by_name": _script({"action": "script.let_in"}),
    "config/script/config/turns_on_a_script": _script({"action": "script.turn_on", "target": {"entity_id": "script.let_in"}}),
    "config/script/config/turns_on_a_scene": _script({"action": "scene.turn_on", "target": {"entity_id": "scene.arrive_home"}}),
    "config/script/config/applies_a_scene": _script({"action": "scene.apply", "data": {"entities": {"lock.front_door": "unlocked"}}}),
    "config/script/config/triggers_an_automation": _script({"action": "automation.trigger", "entity_id": "automation.morning"}),
    "config/script/config/generic_on_an_automation": _script({"action": "homeassistant.turn_on", "entity_id": "automation.morning"}),
    "config/script/config/a_group": _script({"action": "homeassistant.turn_on", "entity_id": "group.all_doors"}),
    "config/script/config/names_no_entity": _script({"action": "homeassistant.turn_on"}),
    "config/script/config/radio_command": _script({"action": "zha.issue_zigbee_cluster_command", "data": {"ieee": "00:11:22"}}),
    "config/script/config/own_service": _script({"action": "womble.release", "target": {"entity_id": "light.hall"}}),
    "config/script/config/generic_on_an_unknown_domain": _script({"action": "homeassistant.turn_on", "entity_id": "womble.gate"}),
    "config/script/config/restarts": _script({"action": "homeassistant.restart"}),
    "config/script/config/from_a_blueprint": {"use_blueprint": {"path": "womble/gate.yaml", "input": {"door": "lock.front_door"}}},
    "config/script/config/waits_for_a_trigger": _script({"wait_for_trigger": [{"platform": "state", "entity_id": "light.hall"}]}),
    # scenes: the config read is the only place a target state comes from
    "states/scene.config_gone": {"attributes": {"entity_id": ["light.hall"], "id": "1700000000801"}},
    "states/scene.config_slow": {"attributes": {"entity_id": ["light.hall"], "id": "1700000000802"}},
    "states/scene.config_errors": {"attributes": {"entity_id": ["light.hall"], "id": "1700000000803"}},
    "states/scene.config_denied": {"attributes": {"entity_id": ["light.hall"], "id": "1700000000804"}},
    **_ui_scene(1, ["light.hall"]),                                                       # entities is not a mapping
    **_ui_scene(2, None),
    "states/scene.s3": {"attributes": {"entity_id": ["light.hall"], "id": "1700000000903"}},
    "config/scene/config/1700000000903": {"name": "No entities key"},
    "states/scene.s4": {"attributes": {"entity_id": ["light.hall"], "id": "1700000000904"}},
    "config/scene/config/1700000000904": ["not", "a", "mapping"],
    **_ui_scene(5, {"light.hall": {"state": "on"}, "cover.garage_door": {"state": "closed", "current_position": 100}}),
    **_ui_scene(6, {"cover.blind": {"state": "closed", "current_tilt_position": "{{ 100 }}"}}),
    **_ui_scene(7, {"light.hall": {"state": "on"}, "lock.front_door": {"state": "unlocked"}}),      # the state names no lock
    **_ui_scene(8, {"cover.blind": {"state": "closed", "current_position": 0, "current_tilt_position": 0.0}}),
}
HA_FAILS = {"config/scene/config/1700000000801": 404, "config/scene/config/1700000000802": 408,
            "config/scene/config/1700000000803": 500, "config/scene/config/1700000000804": 401}
SELECTOR_SCRIPTS = ["script.area_in_data", "script.area_in_service_data", "script.device_in_data", "script.label_in_data",
                    "script.floor_in_data", "script.templated_area", "script.area_beside_an_entity", "script.area_deep_in_data",
                    "script.area_in_a_nest"]
FAILED_SCENE_READS = ["scene.config_gone", "scene.config_slow", "scene.config_errors", "scene.config_denied", "scene.s1", "scene.s2",
                      "scene.s3", "scene.s4"]
#: read in full, and what they do opens something
READ_AS_OPENING = ["script.lock_in_the_second_block", "script.toggles_a_lock", "scene.s5", "scene.s6", "scene.s7"]
#: everything else an action can hold that reaches further than the entities it names
OTHER_UNREADABLE = ["script.two_names_for_a_call", "script.whole_data_templated", "script.whole_target_templated",
                    "script.service_template", "script.data_template", "script.templated_in_a_list", "script.entity_all",
                    "script.runs_a_script_by_name", "script.turns_on_a_script", "script.turns_on_a_scene", "script.applies_a_scene",
                    "script.triggers_an_automation", "script.generic_on_an_automation", "script.a_group", "script.names_no_entity",
                    "script.radio_command", "script.own_service", "script.generic_on_an_unknown_domain", "script.restarts",
                    "script.from_a_blueprint", "script.waits_for_a_trigger"]


async def _verdicts(bridge, entities):
    out = {}
    for entity in entities:
        home_runnables._CACHE.clear()
        out[entity] = (await bridge.get_definition(entity)).get("readable"), await home_runnables.verdict(entity)
    return out


async def test_an_area_device_label_or_floor_anywhere_in_a_call_is_unreadable(real_bridge, house):
    """Blocking finding 1: the reader looked for a selector in ``target`` only, so ``homeassistant.turn_on`` with
    ``data: {area_id: garage}`` read as a call that names nothing and ran for a child. Fails on the old reader (all nine
    read ``safe``). Control run: with the two ``_names_a_selector`` tests dropped from the bridge's ``_script_calls`` the three
    that also name a light read safe and this fails (the six that name no entity are then still held by the classifier's
    no-entity rule, the second lock on the same door; the bridge-alone test below fails for all nine)."""
    assert await _verdicts(real_bridge, SELECTOR_SCRIPTS) == dict.fromkeys(SELECTOR_SCRIPTS, (False, "unreadable"))
    for entity in SELECTOR_SCRIPTS:
        with pytest.raises(HTTPException) as exc:
            await _route(KID, entity)
        assert exc.value.status_code == 403
    assert house["sent"] == []


async def test_the_bridge_alone_refuses_a_selector_in_data(real_bridge):
    """The bridge's half of finding 1 with nothing behind it: the body it returns. Control run: with the two
    ``_names_a_selector`` tests dropped from ``_script_calls`` every one of these is ``readable: true``."""
    for entity in SELECTOR_SCRIPTS:
        assert await real_bridge.get_definition(entity) == {"readable": False, "reason": "target_kind"}, entity


async def test_a_scene_whose_config_cannot_be_read_is_unreadable(real_bridge, house):
    """Blocking finding 2: a failed config read became "members with unknown states", so a scene whose reported members are
    lights read as safe whatever its config holds. A 404, a timeout (the bridge's 408), a 500, a 401 and a body without an
    ``entities`` mapping are all unreadable. Fails on the old reader (seven of the eight read ``safe``). Controls run: with the
    config read wrapped in ``try / except HTTPException: config = {"entities": {}}`` the four failed reads are safe again;
    with the ``config_shape`` refusal replaced by an empty mapping ``scene.s1`` to ``scene.s4`` read safe."""
    assert await _verdicts(real_bridge, FAILED_SCENE_READS) == dict.fromkeys(FAILED_SCENE_READS, (False, "unreadable"))
    reasons = {e: (await real_bridge.get_definition(e))["reason"] for e in FAILED_SCENE_READS}
    assert [reasons[e] for e in FAILED_SCENE_READS[:4]] == ["home_assistant_404", "home_assistant_408", "home_assistant_500",
                                                            "home_assistant_401"]
    assert {reasons[e] for e in FAILED_SCENE_READS[4:]} == {"config_shape"}
    with pytest.raises(HTTPException):
        await _route(KID, "scene.config_gone")
    assert house["sent"] == []


async def test_what_else_a_definition_can_hold_is_read_or_is_unreadable(real_bridge, house):
    """Everything else checked in review that reaches a lock, a cover, a valve or an alarm without naming it: a second data
    block, two names for one call, a templated block / service / list entry, ``entity_id: all``, another script, scene or
    automation however it is called, a group, a call that names no entity, an integration's own service, the generic call on
    a domain this does not know, a blueprint, an unknown step; a scene that gives a "closed" cover a position, and a lock the
    scene's state does not list. Old reader: ``lock_in_the_second_block``, ``two_names_for_a_call``, ``names_no_entity``,
    ``radio_command``, ``own_service``, ``generic_on_an_unknown_domain``, ``restarts``, ``scene.s5`` and ``scene.s6`` read safe.
    Controls run: with ``service_data``'s ids dropped from ``_script_calls`` the second-block lock reads safe; with
    ``_scene_state`` ignoring positions ``scene.s5`` reads safe; with ``_reads_as_its_targets`` answering True the radio
    command reads safe."""
    assert await _verdicts(real_bridge, READ_AS_OPENING) == dict.fromkeys(READ_AS_OPENING, (True, "opens"))
    got = await _verdicts(real_bridge, OTHER_UNREADABLE)
    assert {e: v for e, (_, v) in got.items()} == dict.fromkeys(OTHER_UNREADABLE, "unreadable")
    assert await _verdicts(real_bridge, ["scene.s8"]) == {"scene.s8": (True, "safe")}       # a closed blind at position 0
    for entity in READ_AS_OPENING + OTHER_UNREADABLE:
        with pytest.raises(HTTPException):
            await _route(KID, entity)
    assert (await _route(KID, "scene.s8"))["ok"] is True and [s[1] for s in house["sent"]] == ["scene.s8"]


def test_a_call_this_reader_does_not_know_is_unreadable():
    """The classifier alone, whatever bridge is behind it: a script call is read only when its service is KNOWN to act on
    the entities it names. Control run: with ``_reads_as_its_targets`` answering True every one of these reads safe."""
    unknown = [("", "homeassistant.turn_on"), ("", "light.turn_on"), ("", "cover.close_cover"), ("", "mqtt.publish"),
               ("", "zha.issue_zigbee_cluster_command"), ("", "zwave_js.set_value"), ("light.hall", "womble.release"),
               ("", "homeassistant.restart"), ("light.hall", "homeassistant.reload_all"), ("womble.gate", "homeassistant.turn_on"),
               ("homeassistant.x", "homeassistant.turn_on"), ("", "conversation.process"), ("", "rest_command.gate")]
    assert {(e, s): home_runnables.classify(_acts((e, s, ""))) for e, s in unknown} == dict.fromkeys(unknown, "unreadable")
    known = [("light.hall", "homeassistant.turn_off"), ("", "notify.send_message"), ("", "tts.speak"), ("light.hall", "light.toggle"),
             ("lock.front_door", "lock.lock"), ("input_boolean.away", "input_boolean.turn_on")]
    assert {home_runnables.classify(_acts(k + ("",))) for k in known} == {"safe"}
    assert home_runnables.classify(_acts(("lock.front_door", "homeassistant.toggle", ""))) == "opens"
    assert home_runnables.classify(_acts(("cover.blind", "homeassistant.turn_off", ""))) == "opens"


async def test_reading_stops_at_the_first_adult_only_target(reads, house):
    """One command naming several runnables: nothing is read after the first adult-only verdict. Fails on the old code
    (three reads). Control run: with ``_runs_adult_only`` back to ``any([... for i in runnable])`` this reads all three."""
    params = {"entity_id": ["script.let_in", "scene.movie_time", "scene.arrive_home"]}
    with pytest.raises(HTTPException):
        await _route(KID, "scene.movie_time", "turn_on", params)
    assert reads == ["scene.movie_time", "script.let_in"] and house["sent"] == []
    assert await home_opening.refused("light.hall", "turn_on", {"entity_id": ["scene.leaving", "scene.movie_time"]}, user=KID) is False


UNNAMED = [("scene.movie_time", {"area_id": "hall"}), ("script.goodnight", {"device_id": "abc123"}),
           ("scene.movie_time", {"label_id": ["doors"]}), ("scene.movie_time", {"floor_id": "ground"}),
           ("scene.movie_time", {"target": {"area_id": "hall"}}), ("light.kitchen_lamp", {"area_id": "garage"}),
           ("homeassistant.anything", {"entity_id": "light.kitchen_lamp"}), ("group.all_doors", None),
           ("light.kitchen_lamp", {"entity_id": "light.a, cover.garage_door"}), ("light.kitchen_lamp", {"entity_id": "all"}),
           ("light.kitchen_lamp", {"entity_id": ["light.a", "{{ which }}"]}), ("light.kitchen_lamp", {"entity_id": {"a": 1}}),
           ("scene.movie_time,scene.arrive_home", None)]


@pytest.mark.parametrize("entity, params", UNNAMED)
async def test_a_command_that_reaches_entities_it_does_not_name_is_adult_only(reads, house, entity, params):
    """The same hole at the SENDERS: the bridge passes a command's data straight to Home Assistant, so a harmless scene run
    with ``area_id`` in its params ran every scene in the area, and the generic ``homeassistant`` domain with a comma list
    reached a cover. A selector anywhere in the data, an id that is not one plain ``domain.object_id``, and the generic
    ``homeassistant`` / ``group`` domains are adult-only, at the route and at the brain's tool, and nothing is read for them.
    Fails on the old code (every one was sent for the child). Control run: with ``reaches_unnamed`` answering False every
    case is sent for the child."""
    for caller in NOBODY:
        with pytest.raises(HTTPException) as exc:
            await _route(caller or {}, entity, "turn_on", params)
        assert exc.value.status_code == 403 and exc.value.detail == home_opening.REFUSAL
        home_opening.note_turn(caller)
        assert (await zoe_agent._ha_control(entity, "turn_on", params)).get("status") == "refused"
    assert house["sent"] == [] and reads == []
    assert (await _route(MEMBER, entity, "turn_on", params))["ok"] is True and len(house["sent"]) == 1


async def test_a_plain_command_with_ordinary_data_is_still_for_everyone(reads, house):
    """The other side of the rule above: brightness, a temperature, a plain list of lights. Control run: with
    ``reaches_unnamed`` answering True the child's lamp is refused."""
    for params in (None, {}, {"brightness_pct": 80}, {"entity_id": ["light.a", "light.b"]}, {"entity_id": "LIGHT.A "},
                   {"rgb_color": [255, 0, 0]}, {"value": 21}):
        assert (await _route(KID, "light.kitchen_lamp", "turn_on", params))["ok"] is True
    assert (await _route(KID, "climate.den", "set_temperature", {"temperature": 21.5}))["ok"] is True
    assert reads == [] and len(house["sent"]) == 8
