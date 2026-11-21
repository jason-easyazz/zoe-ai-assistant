"""Unlocking or opening the house needs an adult (``home_opening.py``; security fix, on by default).

Every member-driven path that can send a command to Home Assistant: the panel's ``/api/ha/control`` route, the brain's
``ha_control`` tool, and the spoken / Skybridge device command. For each: an adult member is allowed; a child, a guest and the
bare panel are refused and NOTHING is sent; the panel with an adult's verified voice session open is allowed; lock and close
are unchanged for everyone; an unknown service on a lock is refused for a non-adult.

Each test names the control that was run against it (the check removed at that entry point, the test seen to fail, the code
restored). Invented people and devices only.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

import home_opening
import manner_block
import panel_principal
import smart_home_service
import voice_session
import zoe_agent
from routers import ha_control

pytestmark = pytest.mark.ci_safe

ADULT, CHILD, PANEL = "member-ova", "member-kip", "panel-hall"
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

    async def is_adult(user_id, db=None):
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

    monkeypatch.setattr(manner_block, "is_adult", is_adult)
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


async def test_the_route_opens_at_the_panel_only_while_an_adults_verified_voice_session_is_open(house):
    """Control run: with ``may_open`` ignoring the voice session the adult's session is refused; with it not asking whether
    the session's member is an adult the child's session opens the door."""
    house["session"] = ADULT
    assert (await _route(BARE_PANEL, "lock.front_door", "unlock"))["ok"] is True
    house["session"] = CHILD
    with pytest.raises(HTTPException):
        await _route(BARE_PANEL, "lock.front_door", "unlock")
    house["session"] = None
    with pytest.raises(HTTPException):
        await _route(BARE_PANEL, "lock.front_door", "unlock")
    assert len(house["sent"]) == 1


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


def test_the_spoken_resolver_says_the_sentence_before_it_sends_anything():
    """Control run: with the refusal loop dropped from ``resolve_smart_home`` this fails."""
    import inspect

    src = inspect.getsource(smart_home_service.resolve_smart_home)
    loop = src.index('home_opening.refused(str(d.get("entity_id")), "turn_on" if on else "turn_off", via="spoken")')
    assert loop < src.index("if await set_device(str(d.get(\"entity_id\")), on") and '"spoken_summary": home_opening.REFUSAL' in src


async def test_a_lookup_that_fails_and_an_unknown_account_are_not_an_adult(house, monkeypatch):
    """Positively an adult or refused. Control run: with ``_adult`` answering True when the lookup raises, this fails."""
    async def boom(user_id, db=None):
        raise RuntimeError("no database")

    monkeypatch.setattr(manner_block, "is_adult", boom)
    assert await home_opening.may_open(MEMBER) is False
    assert await home_opening.refused("lock.front_door", "unlock", user=MEMBER) is True
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
