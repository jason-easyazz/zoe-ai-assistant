"""Unlocking or opening the house needs an adult (``home_opening.py``; security fix, on by default).

Every member-driven path that can send a command to Home Assistant: the panel's ``/api/ha/control`` route, the brain's
``ha_control`` tool, and the spoken / Skybridge device command. For each: an adult member is allowed; a child, a guest and the
bare panel are refused and NOTHING is sent; the panel with an adult's verified voice session open is allowed; lock and close
are unchanged for everyone; an unknown service on a lock is refused for a non-adult.

Each test names the control that was run against it (the check removed at that entry point, the test seen to fail, the code
restored). Invented people and devices only.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import auth
import home_opening
import manner_block
import panel_kiosk_credential
import panel_principal
import persona_layer
import smart_home_service
import voice_session
import zoe_agent
from routers import ha_control, panel_auth
from test_ha_control_auth import CF, GUEST_SID, PANEL_IP, PANEL_TOKEN, PHONE_IP, PROXY, _env, _ha, registry  # noqa: F401 - fixtures

pytestmark = pytest.mark.ci_safe

ADULT, CHILD, PANEL = "member-ova", "member-kip", "kitchen-panel"      # the panel the shared registry fixture registers
REAL_LOOKUP = manner_block._account_is_adult
MEMBER = {"user_id": ADULT, "role": "user"}
KID = {"user_id": CHILD, "role": "user"}
GUEST = {"user_id": "guest", "role": "guest"}
BARE_PANEL = {"user_id": "guest", "role": "guest", "panel_id": PANEL}
NOBODY = [KID, GUEST, BARE_PANEL, {}, None]
OPENING = [("lock.front_door", "unlock"), ("cover.garage_door", "open_cover"), ("cover.garage_door", "toggle"),
           ("alarm_control_panel.house", "alarm_disarm"), ("valve.water_main", "open_valve"), ("lock.front_door", "some_new_service"),
           ("cover.blind", "set_cover_position"), ("cover.garage_door", "turn_on")]
CLOSING = [("lock.front_door", "lock"), ("cover.garage_door", "close_cover"), ("cover.blind", "stop_cover"),
           ("alarm_control_panel.house", "alarm_arm_away"), ("valve.water_main", "close_valve")]
ORDINARY = [("light.kitchen_lamp", "turn_on"), ("switch.kettle_plug", "toggle"), ("scene.movie_time", "turn_on"),
            ("media_player.den", "media_pause")]


@pytest.fixture
def house(monkeypatch):
    """The account record says who is an adult; the panel's voice session is whoever ``session`` names; ``sent`` is every
    command that reached the bridge."""
    state = {"session": None, "sent": []}

    async def account_is_adult(user_id, db=None):      # the account record; the real ``manner_block.is_adult`` runs over it
        return user_id == ADULT

    async def bridge(path, body):
        state["sent"].append((path, body["entity_id"], body["action"]))
        return {"ok": True}

    async def spoken_bridge(path, payload):
        state["sent"].append((path, payload["entity_id"], payload["action"]))
        return {"message": "Successfully executed", "result": {}}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            state["sent"].append(("/devices/control", json["entity_id"], json["action"]))

            class R:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"ok": True}

            return R()

    monkeypatch.setattr(manner_block, "_account_is_adult", account_is_adult)
    monkeypatch.setattr(voice_session, "active_member", lambda panel: state["session"] if panel == PANEL else None)
    monkeypatch.setattr(ha_control, "_bridge_post", bridge)
    monkeypatch.setattr(smart_home_service, "_ha_post", spoken_bridge)
    monkeypatch.setattr(zoe_agent.httpx, "AsyncClient", Client)
    token, turn = home_opening._TURN.set(None), panel_principal._home_control_turn.set(None)
    yield state
    home_opening._TURN.reset(token)
    panel_principal._home_control_turn.reset(turn)


def test_what_counts_as_opening_the_house():
    """A lock or a cover, and any service but closing, locking or arming it; a service's own target counts too.
    Control run: with ``CLOSING`` holding ``unlock`` the first assertion fails."""
    assert all(home_opening.opens(e, a) for e, a in OPENING)
    assert not any(home_opening.opens(e, a) for e, a in CLOSING + ORDINARY)
    assert home_opening.opens("light.kitchen_lamp", "turn_on", {"entity_id": "lock.front_door"})
    assert home_opening.opens("light.kitchen_lamp", "turn_on", {"entity_id": ["light.a", "cover.garage_door"]})
    assert home_opening.opens("LOCK.Front_Door", " Unlock ")


# ---- the panel's control route ---------------------------------------------------------------------------------------------------
async def _route(caller, entity, action, params=None):
    return await ha_control.ha_control(ha_control.HAControlPayload(entity_id=entity, action=action, params=params), caller=caller)


@pytest.mark.parametrize("entity, action", OPENING)
async def test_the_route_opens_for_an_adult_and_for_nobody_else(house, entity, action):
    """Control run: with the check dropped from ``routers/ha_control.ha_control`` the child's unlock is sent."""
    for caller in NOBODY:
        with pytest.raises(HTTPException) as exc:
            await _route(caller or {}, entity, action)
        assert exc.value.status_code == 403 and exc.value.detail == home_opening.REFUSAL
    assert house["sent"] == []                                                          # nothing reached Home Assistant
    assert (await _route(MEMBER, entity, action))["ok"] is True and len(house["sent"]) == 1


def _kiosk_cookie(monkeypatch):
    """A REAL kiosk credential, minted from a device token issued to the panel."""
    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")
    monkeypatch.setitem(panel_auth._token_cache, "hash-of-a-test-token", dict(panel_id=PANEL, role="voice", expires_at=None, revoked=0))
    return "%s=%s" % (panel_kiosk_credential.COOKIE_NAME, panel_kiosk_credential.mint_credential(PANEL, "hash-of-a-test-token"))


def _panel_request(way, monkeypatch):
    """(peer, headers) for each way a panel authenticates; ``get_current_user`` and ``panel_principal`` make the caller."""
    return {"device_token": (PHONE_IP, {"X-Device-Token": PANEL_TOKEN}),
            "kiosk_credential": (PROXY, {"X-Real-IP": PHONE_IP, "Cookie": _kiosk_cookie(monkeypatch)}),
            "registered_address": (PROXY, {"X-Real-IP": PANEL_IP}),
            "registered_address_guest_session": (PROXY, {"X-Real-IP": PANEL_IP, "X-Session-ID": GUEST_SID})}[way]


def _unlock(peer, headers):
    return _ha(peer).post("/api/ha/control", json={"entity_id": "lock.front_door", "action": "unlock"}, headers=headers)


@pytest.mark.parametrize("way", ["device_token", "kiosk_credential", "registered_address", "registered_address_guest_session"])
def test_every_kind_of_panel_opens_only_while_an_adults_verified_voice_session_is_open_on_it(house, registry, monkeypatch, way):
    """Real HTTP, the real ``get_current_user`` and the real panel test: the caller is whatever each authorisation path makes.
    Controls run: with ``may_open`` reading only the caller's own ``panel_id`` the kiosk-credential and registered-address
    panels refuse the adult; with it not asking whether the session's member is an adult the child's session opens the door."""
    peer, headers = _panel_request(way, monkeypatch)
    house["session"] = ADULT
    assert _unlock(peer, headers).status_code == 200 and len(house["sent"]) == 1
    house["session"] = CHILD
    r = _unlock(peer, headers)
    assert r.status_code == 403 and r.json()["detail"] == home_opening.REFUSAL
    house["session"] = None
    assert _unlock(peer, headers).json()["detail"] == home_opening.REFUSAL
    house["session"] = ADULT
    assert _unlock(peer, {**headers, **CF}).status_code == 403 and len(house["sent"]) == 1      # never over the tunnel


def test_an_address_that_names_no_single_panel_and_a_header_less_promoted_caller_open_nothing(house, registry, monkeypatch):
    """Controls run: with ``_panel_id_at`` taking the first of two panels at one address the first assertion fails; with the
    ``unauthenticated_override`` branch dropped from ``may_open`` the last one fails (the promoted id reads as an adult)."""
    registry.con.execute("INSERT INTO panels VALUES ('other-panel', ?, 1, 1)", (PANEL_IP,))
    house["session"] = ADULT
    assert _unlock(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 403
    registry.con.execute("DELETE FROM panels WHERE panel_id = 'other-panel'")
    house["session"] = None

    async def everyone_is_an_adult(user_id, db=None):
        return True

    monkeypatch.setattr(manner_block, "_account_is_adult", everyone_is_an_adult)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")      # the legacy setting: a header-less caller is "admin"
    assert _unlock(PROXY, {"X-Real-IP": PANEL_IP}).json()["detail"] == home_opening.REFUSAL and house["sent"] == []


# ---- the real bridge: this PR decides who MAY ask, it does not make unlocking work ------------------------------------------------
async def test_today_the_real_bridge_carries_no_unlock_or_open_for_anyone(house, monkeypatch):
    """Every other test here fakes the bridge as ACCEPTING, to show what Zoe sends. The REAL bridge (``homeassistant-mcp-bridge``
    ``control_device``, only Home Assistant faked) has no mapping for lock, unlock, open, close or arm / disarm, so an adult's
    allowed unlock reaches the bridge and is answered 400 (a 502 from the route): nothing reaches Home Assistant for anybody.
    The mapping arrives in a later PR, once scenes, scripts and automation entities are adult-only too; this test changes then.
    Control run: with the check dropped from ``routers/ha_control.ha_control`` the child's unlock reaches the bridge."""
    spec = importlib.util.spec_from_file_location(
        "ha_mcp_bridge_main", Path(__file__).resolve().parents[2] / "homeassistant-mcp-bridge" / "main.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    reached, called = [], []

    class FakeHomeAssistant:
        async def call_service(self, domain, service, entity_id=None, data=None):
            called.append((domain, service, entity_id))
            return {"ok": True}

    async def real_bridge(path, body):
        reached.append(body["action"])
        return await bridge.control_device(bridge.DeviceControlRequest(**body))

    monkeypatch.setattr(bridge, "ha_bridge", FakeHomeAssistant())
    monkeypatch.setattr(ha_control, "_bridge_post", real_bridge)
    with pytest.raises(HTTPException) as exc:
        await _route(KID, "lock.front_door", "unlock")
    assert exc.value.status_code == 403 and reached == []                               # refused by Zoe: never asked of the bridge
    for entity, action in [("lock.front_door", "unlock"), ("cover.garage_door", "open"), ("alarm_control_panel.house", "alarm_disarm"),
                           ("lock.front_door", "lock"), ("cover.garage_door", "close")]:
        with pytest.raises(HTTPException) as exc:
            await _route(MEMBER, entity, action)
        assert exc.value.status_code == 502                                             # allowed by Zoe, unknown to the bridge
    assert len(reached) == 5 and called == []
    assert (await _route(KID, "light.kitchen_lamp", "turn_on"))["ok"] is True and called == [("light", "turn_on", "light.kitchen_lamp")]


@pytest.mark.parametrize("caller", [MEMBER, KID, GUEST, BARE_PANEL])
async def test_the_route_locks_closes_and_does_everything_else_for_everyone_as_before(house, caller):
    """Control run: with ``opens`` true for every service on a lock, the child's ``lock`` is refused."""
    for entity, action in CLOSING + ORDINARY:
        assert (await _route(caller, entity, action))["ok"] is True
    assert len(house["sent"]) == len(CLOSING + ORDINARY)
    with pytest.raises(HTTPException) if caller is not MEMBER else _nothing():
        await _route(caller, "light.kitchen_lamp", "turn_on", {"entity_id": "lock.front_door"})      # a target hidden in the data


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---- the brain's tool ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("entity, action", OPENING)
async def test_the_tool_opens_for_an_adults_turn_and_for_nobody_else(house, entity, action):
    """Who the turn is for is noted where the turn's home-control verdict is recorded. A call with no turn at all is nobody.
    Control run: with the check dropped from ``zoe_agent._ha_control`` the child's unlock is sent."""
    for turn in NOBODY:
        home_opening.note_turn(turn)
        assert await zoe_agent._ha_control(entity, action) == {"error": home_opening.REFUSAL, "status": "refused"}
    home_opening._TURN.set(None)                                                        # not a turn at all
    assert (await zoe_agent._ha_control(entity, action)).get("status") == "refused" and house["sent"] == []
    home_opening.note_turn(MEMBER)
    assert await zoe_agent._ha_control(entity, action) == {"ok": True} and len(house["sent"]) == 1
    house["session"] = ADULT
    home_opening.note_turn(BARE_PANEL)
    assert await zoe_agent._ha_control(entity, action) == {"ok": True}                  # the panel, inside an adult's voice session


async def test_the_tool_locks_closes_and_does_everything_else_for_every_turn_as_before(house):
    for turn in (MEMBER, KID, GUEST, BARE_PANEL):
        home_opening.note_turn(turn)
        for entity, action in CLOSING + ORDINARY:
            assert await zoe_agent._ha_control(entity, action) == {"ok": True}
    assert len(house["sent"]) == 4 * len(CLOSING + ORDINARY)


async def test_a_turn_notes_who_it_is_for(house, monkeypatch):
    """Control run: with ``note_turn`` dropped from ``panel_principal.record_turn_home_control`` the adult's turn is refused."""
    async def allowed(request, user, db=None):
        return True

    monkeypatch.setattr(panel_principal, "may_control_home", allowed)
    await panel_principal.record_turn_home_control(None, MEMBER)
    assert await zoe_agent._ha_control("lock.front_door", "unlock") == {"ok": True}
    await panel_principal.record_turn_home_control(None, KID)
    assert (await zoe_agent._ha_control("lock.front_door", "unlock"))["status"] == "refused"


async def test_a_turn_from_the_kiosk_at_its_registered_address_notes_which_panel_it_is(house, registry):
    """The real ``may_control_home`` and ``panel_of`` over a real request: the kiosk browser's turn carries no ``panel_id``.
    Control run: with ``record_turn_home_control`` noting the caller without its panel the adult's session is refused."""
    kiosk = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"x-real-ip", PANEL_IP.encode())], "client": (PROXY, 1)})
    phone = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": (PHONE_IP, 1)})
    house["session"] = ADULT
    assert await panel_principal.record_turn_home_control(kiosk, dict(GUEST)) is True
    assert await zoe_agent._ha_control("lock.front_door", "unlock") == {"ok": True}
    house["session"] = CHILD
    assert (await zoe_agent._ha_control("lock.front_door", "unlock"))["status"] == "refused"
    house["session"] = ADULT
    assert await panel_principal.record_turn_home_control(phone, dict(GUEST)) is False      # a phone on the Wi-Fi is no panel
    assert await home_opening.refused("lock.front_door", "unlock") is True and len(house["sent"]) == 1


# ---- the spoken / Skybridge device command ---------------------------------------------------------------------------------------
async def test_a_spoken_on_or_off_never_reaches_a_lock_or_a_cover_for_anyone_but_an_adult(house):
    """The spoken resolver sends only ``turn_on`` / ``turn_off``; aimed at a lock or a cover that is an unknown service on a
    secure thing. Control run: with the check dropped from ``smart_home_service.set_device`` the child's command is sent."""
    for turn in NOBODY:
        home_opening.note_turn(turn)
        assert await smart_home_service.set_device("cover.garage_door", True) is False
        assert await smart_home_service.set_device("lock.front_door", False) is False
    assert house["sent"] == []
    home_opening.note_turn(KID)
    assert await smart_home_service.set_device("light.kitchen_lamp", True) is True      # a light: unchanged for a child
    home_opening.note_turn(MEMBER)
    assert await smart_home_service.set_device("cover.garage_door", True) is True
    assert house["sent"] == [("/devices/control", "light.kitchen_lamp", "turn_on"), ("/devices/control", "cover.garage_door", "turn_on")]


async def test_the_spoken_resolver_says_the_sentence_and_sends_nothing_for_anyone_but_an_adult(house, monkeypatch):
    """The REAL ``resolve_smart_home`` on a spoken "turn on the garage door" with only the bridge faked (its listing carries a
    cover and a light). Control run: with the refusal loop dropped from ``resolve_smart_home`` the sentence is not said (the
    ``set_device`` backstop alone answers "I couldn't reach") and the first assertion fails."""
    async def listing(path):
        rows = [{"entity_id": "cover.garage_door", "name": "Garage Door", "state": "off"},
                {"entity_id": "switch.porch_lamp", "name": "Porch Lamp", "state": "off"}]
        return {"/lights": {"lights": []}, "/switches": {"switches": rows}, "/scenes": {"scenes": []}}.get(path, {"entities": []})

    monkeypatch.setattr(smart_home_service, "_ha_get", listing)
    said = SimpleNamespace(action="turn_on", query="garage door", entity_id="")
    for turn in NOBODY:
        home_opening.note_turn(turn)
        out = await smart_home_service.resolve_smart_home(said)
        assert out["spoken_summary"] == home_opening.REFUSAL and out["cards"] == [] and house["sent"] == []
    home_opening.note_turn(KID)                                                         # a lamp: unchanged for a child
    await smart_home_service.resolve_smart_home(SimpleNamespace(action="turn_on", query="porch lamp", entity_id=""))
    home_opening.note_turn(MEMBER)
    out = await smart_home_service.resolve_smart_home(said)
    assert out["controlled_entities"] == ["cover.garage_door"] and out["spoken_summary"] != home_opening.REFUSAL
    assert house["sent"] == [("/devices/control", "switch.porch_lamp", "turn_on"), ("/devices/control", "cover.garage_door", "turn_on")]


async def test_a_lookup_that_fails_is_not_an_adult_and_logs_no_id_and_no_traceback(house, monkeypatch, caplog):
    """Positively an adult or refused, with ``is_adult`` NOT mocked: the member-mode read underneath it raises. Controls run:
    with ``manner_block.is_adult`` answering True when its lookup raises the first assertion fails; with ``quiet=True`` dropped from
    ``home_opening._adult`` the log holds the member's id and a traceback and the log assertions fail."""
    async def boom(user_id, db=None):
        raise RuntimeError(f"no database for {user_id}")

    monkeypatch.setattr(manner_block, "_account_is_adult", REAL_LOOKUP)
    monkeypatch.setattr(persona_layer, "load_member_mode", boom)
    with caplog.at_level(logging.DEBUG):
        assert await home_opening.refused("lock.front_door", "unlock", user=MEMBER, via="panel_route") is True
    assert [r.getMessage() for r in caplog.records] == ["HOME_OPENING refused via=panel_route domain=lock"]
    assert ADULT not in caplog.text and "Traceback" not in caplog.text and not any(r.exc_info for r in caplog.records)
    assert await manner_block.is_adult(ADULT) is False and ADULT in caplog.text         # other callers still get their warning
    assert await home_opening.refused("lock.front_door", "unlock", user={"user_id": "member-unknown"}) is True
    assert await home_opening.refused("lock.front_door", "lock", user=None) is False


# ---- the log ---------------------------------------------------------------------------------------------------------------------
async def test_a_refusal_logs_closed_codes_only(house, caplog):
    """Control run: with the entity id added to the log line this fails."""
    with caplog.at_level(logging.WARNING, logger="home_opening"):
        assert await home_opening.refused("lock.front_door", "unlock", user=KID, via="panel_route") is True
    lines = [r.getMessage() for r in caplog.records if "HOME_OPENING" in r.getMessage()]
    assert lines == ["HOME_OPENING refused via=panel_route domain=lock"]
    assert CHILD not in lines[0] and "front_door" not in lines[0]
