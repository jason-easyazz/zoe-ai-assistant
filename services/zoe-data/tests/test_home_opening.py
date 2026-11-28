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
ORDINARY = [("light.kitchen_lamp", "turn_on"), ("switch.kettle_plug", "toggle"), ("fan.attic", "turn_on"),      # scenes: test_home_runnables
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
    Control run: with ``record_turn_home_control`` noting the caller without its request the adult's session is refused."""
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


async def test_an_ordinary_turn_adds_no_lookup_and_an_opening_turn_resolves_its_panel_once(house, registry, monkeypatch):
    """Statement count on the panel registry, for the kiosk at its registered address (a guest with no ``panel_id``). Recording
    the turn runs the ONE statement ``may_control_home`` runs on main; an ordinary command adds none; the first opening
    command resolves the panel (the strict panel test and the id: two statements); every later one in the turn adds none.
    Control run: with ``record_turn_home_control`` resolving ``panel_of`` itself the first count is 3 and this fails."""
    statements, run = [], registry.execute

    async def counted(sql, params=()):
        statements.append(sql)
        return await run(sql, params)

    monkeypatch.setattr(registry, "execute", counted)
    kiosk = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"x-real-ip", PANEL_IP.encode())], "client": (PROXY, 1)})
    house["session"] = ADULT
    assert await panel_principal.record_turn_home_control(kiosk, dict(GUEST)) is True and len(statements) == 1
    assert await zoe_agent._ha_control("light.kitchen_lamp", "turn_on") == {"ok": True}
    assert await zoe_agent._ha_control("lock.front_door", "lock") == {"ok": True} and len(statements) == 1
    assert await zoe_agent._ha_control("lock.front_door", "unlock") == {"ok": True} and len(statements) == 3
    assert await zoe_agent._ha_control("cover.garage_door", "open_cover") == {"ok": True}
    assert await smart_home_service.set_device("cover.garage_door", True) is True and len(statements) == 3


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


@pytest.mark.parametrize("entity", ["", "   ", None])
async def test_a_command_with_no_entity_id_at_all_is_adult_only(house, entity):
    """An empty, blank or missing id names nothing: refused for anyone but an adult, never "an ordinary command".
    Control run: with the ``if not ids`` line removed from ``reaches_unnamed`` every case here returns False for the child."""
    assert home_opening.reaches_unnamed(entity) is True and home_opening.reaches_unnamed(entity, {"brightness": 3}) is True
    for turn in NOBODY + [KID]:
        home_opening.note_turn(turn)
        assert await home_opening.refused(entity, "turn_on", via="spoken") is True
    home_opening.note_turn(MEMBER)
    assert await home_opening.refused(entity, "turn_on", via="spoken") is False
    assert home_opening.reaches_unnamed("light.kitchen_lamp") is False      # a plain id is still an ordinary command


async def test_the_spoken_resolver_says_the_sentence_for_a_device_with_no_id(house, monkeypatch):
    """The REAL ``resolve_smart_home`` when the listing holds a device whose entity id is empty: a child, a guest and the bare
    panel hear the refusal sentence and nothing is sent (before, the empty id slipped past the check and they heard the
    ordinary "couldn't reach" failure). Control run: with the ``if not ids`` line removed the first assertion fails."""
    async def listing(path):
        rows = [{"entity_id": "", "name": "Womble Lamp", "state": "off"}]
        return {"/lights": {"lights": []}, "/switches": {"switches": rows}, "/scenes": {"scenes": []}}.get(path, {"entities": []})

    monkeypatch.setattr(smart_home_service, "_ha_get", listing)
    said = SimpleNamespace(action="turn_on", query="womble lamp", entity_id="")
    for turn in NOBODY + [KID]:
        home_opening.note_turn(turn)
        out = await smart_home_service.resolve_smart_home(said)
        assert out["spoken_summary"] == home_opening.REFUSAL and out["cards"] == [] and house["sent"] == []


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


# ---- review fix 2026-11-24: a directly sent command is a listed (domain, service) pair or it is adult-only -------------------------
#: every one was sent for the child on the old code (none names a lock, an alarm panel, a cover or a valve)
UNLISTED = [("remote.den", "send_command", {"command": "womble"}), ("remote.den", "send_command", None), ("remote.den", "learn_command", None),
            ("remote.den", "turn_on", {"activity": "womble"}), ("remote.den", "turn_on", None), ("remote.den", "toggle", None),
            ("button.gate", "press", None), ("input_button.gate", "press", None), ("vacuum.den", "send_command", None),
            ("media_player.den", "play_media", {"media_content_id": "http://relay.invalid/open"}),
            ("media_player.den", "play_media", {"media_content_id": " //relay.invalid/open"}),
            ("media_player.den", "play_media", {"media_content_id": "http:relay.invalid/open"}),      # a scheme with no slashes
            ("media_player.den", "play_media", {"media_content_id": "HTTP:relay.invalid/open"}),
            ("media_player.den", "play_media", {"media_content_id": "  https:relay.invalid/open"}),
            ("media_player.den", "play_media", {"media_content_id": "mailto:womble@relay.invalid"}),
            ("media_player.den", "play_media", {"media_content_id": "tel:0000"}),
            ("media_player.den", "play_media", {"media_content_id": "data:text/plain,womble"}),
            ("media_player.den", "play_media", {"media_content_id": ["womble"]}), ("media_player.den", "play_media", None),
            ("media_player.den", "play_media", {"media_content_id": "womble", "extra": {"x": 1}}),
            ("light.kitchen_lamp", "turn_on", {"payload": "womble"}), ("light.kitchen_lamp", "turn_on", {"nested": [{"URL ": "womble"}]}),
            ("light.kitchen_lamp", "some_new_service", None), ("womble.gate", "turn_on", None), ("text.gate_code", "set_value", None),
            ("light.kitchen_lamp", "turn_on", {"entity_id": ["light.a", "remote.den"]}), ("notify.womble", "send_message", None)]
#: the named controls: the same domains with a listed service and plain data
LISTED = [("remote.den", "turn_off", None), ("media_player.den", "media_pause", None), ("media_player.den", "volume_set", {"volume_level": 0.3}),
          ("media_player.den", "play_media", {"media_content_id": "womble song", "media_content_type": "music"}),
          ("media_player.den", "play_media", {"media_content_id": "media-source://womble/one"}),
          ("media_player.den", "play_media", {"media_content_id": "library://track/1"}), ("vacuum.den", "start", None),
          ("media_player.den", "play_media", {"media_content_id": " Media-Source://womble/two"}),
          ("media_player.den", "play_media", {"media_content_id": "womble song 12:30 mix"}),      # a colon after a space is not a scheme
          ("light.kitchen_lamp", "set_brightness", {"brightness_pct": 40}), ("light.kitchen_lamp", "set_color", {"rgb_color": [1, 2, 3]}),
          ("climate.den", "set_temperature", {"temperature": 21}), ("input_number.den_setpoint", "set_value", {"value": 20})]


@pytest.mark.parametrize("entity, action, params", UNLISTED)
async def test_a_command_that_is_not_a_listed_pair_or_carries_a_payload_is_adult_only(house, entity, action, params):
    """``remote.send_command`` can transmit the code that opens a garage or a gate; a button press triggers whatever it is
    wired to; ``play_media`` with an address makes the player fetch it; an unknown service or domain could do anything.
    Refused at the route and at the brain's tool for everyone but an adult, nothing sent. Fails on the old code (each was
    sent for the child: the direct rule looked only at the four secure domains).
    Control run (named): ``LISTED`` in the next test. Control run (code): with ``home_opening.unlisted`` answering False
    every case is sent for the child."""
    for caller in NOBODY:
        with pytest.raises(HTTPException) as exc:
            await _route(caller or {}, entity, action, params)
        assert exc.value.status_code == 403 and exc.value.detail == home_opening.REFUSAL
        home_opening.note_turn(caller)
        assert (await zoe_agent._ha_control(entity, action, params)).get("status") == "refused"
    assert house["sent"] == []
    assert (await _route(MEMBER, entity, action, params))["ok"] is True and len(house["sent"]) == 1


async def test_a_listed_pair_with_plain_data_is_still_for_everyone_and_looks_nobody_up(house, monkeypatch, caplog):
    """The other side: a listed service, words or a library id to play, the bridge's own light actions. Nobody is looked up
    for one. Control run: with ``home_opening.unlisted`` answering True every one is refused for the child."""
    async def nobody_is_asked(*a, **k):
        raise AssertionError("an ordinary command looked somebody up")

    with monkeypatch.context() as only_here:
        only_here.setattr(home_opening, "may_open", nobody_is_asked)
        for entity, action, params in LISTED + [(e, a, None) for e, a in ORDINARY + CLOSING]:
            assert (await _route(KID, entity, action, params))["ok"] is True, (entity, action)
    assert len(house["sent"]) == len(LISTED + ORDINARY + CLOSING)
    with caplog.at_level(logging.WARNING, logger="home_opening"):
        assert await home_opening.refused("remote.den", "send_command", {"command": "womble"}, user=KID, via="panel_route") is True
    assert [r.getMessage() for r in caplog.records if "HOME_OPENING" in r.getMessage()] == [
        "HOME_OPENING refused via=panel_route domain=unlisted_service"]
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "home_opening")      # this module's own lines only: the
    assert ours and "womble" not in ours and "remote" not in ours                             # route's INFO line names the entity


async def test_the_music_sender_asks_too_so_play_an_address_is_adult_only(house):
    """The spoken music fallback posts ``play_media`` to the bridge with the caller's own words as the content id. It now
    asks the same door: an address is adult-only, words play for anyone. Fails on the old code (the sender never asked).
    Control run: with the ``refused`` call dropped from ``intent_router._post_music_ha_control`` the child's address is posted."""
    import intent_router

    class Client:
        posted: list = []

        async def post(self, url, json=None):
            self.posted.append(json["data"]["media_content_id"])
            return SimpleNamespace(status_code=200)

    def play(what):
        return {"entity_id": "media_player.den", "action": "play_media", "data": {"media_content_id": what, "media_content_type": "music"}}

    client = Client()
    panel_principal.record_home_control(True)
    home_opening.note_turn(KID)
    assert await intent_router._post_music_ha_control(client, "http://bridge.invalid", play("http://relay.invalid/open")) == home_opening.REFUSAL
    assert await intent_router._post_music_ha_control(client, "http://bridge.invalid", play("womble song")) is None
    home_opening.note_turn(MEMBER)
    assert await intent_router._post_music_ha_control(client, "http://bridge.invalid", play("http://relay.invalid/open")) is None
    assert client.posted == ["womble song", "http://relay.invalid/open"]
