"""Music and home control go to the Zoe OS kernel, a flag each (``ZOE_OS_MUSIC=kernel``, ``ZOE_OS_HOME=kernel``;
docs/knowledge/zoe-os-music-home-door.md). Nothing is stored for either: the door sits at the one sender of each.

* any other flag value: NO kernel call and exactly main's outbound calls for all 45 scenarios; the same with the doors on and
  the kernel allowing; and a command nobody is acting for (automatic) is never asked or told;
* QUIET commands (stop / turn down the music; a light or a media player off) are SENT FIRST and told afterwards: a kernel that
  is slow, gone or says no neither delays nor stops them;
* SECURE commands (a lock or a cover, anything but closing it) are ALWAYS asked first and a kernel that cannot answer means NO;
* everything else asks first: refused = nothing is sent and each caller says so; kernel down or slow = sent, ``not_checked``;
* only ids and closed codes cross: never a track, a device name or a room;
* a child's level is set only through the ask (an adult only), and never while the kernel cannot be asked.

A fake kernel on a real Unix socket (``test_kernel_door.AreaKernel``); a fake Music Assistant and a fake Home Assistant bridge
(``actions_scenarios``). Each test names the control that was run against it. Invented ids and words only.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import action_doors
import actions_scenarios as scenarios
import home_level
import kernel_door
import os_shadow
import user_erase_gate
from actions_scenarios import ANN
from test_kernel_door import AreaKernel
from test_os_shadow import SVC

pytestmark = pytest.mark.ci_safe

ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "actions_as_on_main.json").read_text("utf-8"))
FLAGS = ("ZOE_OS_MUSIC", "ZOE_OS_HOME")
OTHER_FLAGS = ("ZOE_OS_LISTS", "ZOE_OS_REMINDERS", "ZOE_OS_ALARMS", "ZOE_OS_CALENDAR", "ZOE_OS_NOTES", "ZOE_OS_JOURNAL", "ZOE_OS_PEOPLE")


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    for flag in FLAGS:
        monkeypatch.setenv(flag, "kernel")
    for other in OTHER_FLAGS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    token = kernel_door._ACTOR.set(None)
    k.state = state
    yield k
    kernel_door._ACTOR.reset(token)
    scenarios._restore()
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _wait(door, op, n, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops(op)) < n:
        time.sleep(0.01)
    return door.ops(op)


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]


async def _settled(caplog, n, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(_lines(caplog)) < n:
        await asyncio.sleep(0.02)


def _sent(log, what) -> list:
    """The command calls in a scenario's log: Home Assistant command paths, or Music Assistant commands named ``what``."""
    if what == "house":
        return [e for e in log if e[0] == "http" and e[1] == "POST" and e[2] != "/api"]
    return [e for e in log if e[0] == "http" and e[2] == "/api" and (e[3] or {}).get("command") == what]


async def _run(name, log, *, who=ANN, setup=None):
    w = scenarios.World(log)
    if setup:
        setup(w)
    kernel_door.note_actor(who)
    return await scenarios.scenarios()[name](w)


# ---- flag off, and nobody acting ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "on", "KERNELS"])
def test_any_other_flag_value_sends_exactly_what_main_sent(door, monkeypatch, value):
    """Control run: with the flag check dropped from ``action_doors.before`` the ``never`` stub raises and the record differs."""
    def never(*a, **k):
        raise AssertionError("the kernel was reached with the flag off")

    for flag in FLAGS:
        monkeypatch.delenv(flag) if value is None else monkeypatch.setenv(flag, value)
    for name in ("ask_door", "record_door", "report_door", "door_unchecked", "stamp_snapshot"):
        monkeypatch.setattr(os_shadow, name, never)
    kernel_door.note_actor(ANN)                                 # a member IS acting: still nothing reaches the kernel
    assert scenarios.run_all() == ON_MAIN
    assert door.calls == [] and door.connections == 0 and os_shadow._door_worker is None


def test_with_the_doors_on_and_allowing_every_entry_point_sends_exactly_what_main_sent(door):
    """The doors add no outbound call and change no result when the kernel allows. Control run: with the gate sending the
    command twice when allowed, every command scenario gains a call."""
    kernel_door.note_actor(ANN)
    assert scenarios.run_all() == ON_MAIN
    end = time.monotonic() + 8                                  # the told-afterwards verbs arrive on the door's own thread
    want = {"music.play", "music.stop", "music.quieter", "music.louder", "music.queue", "music.library", "light.on", "light.off",
            "switch.set", "scene.on", "cover.open", "lock.open", "media.off"}
    while time.monotonic() < end and not want <= {a["verb"] for a in door.ops("door_ask")}:
        time.sleep(0.02)
    assert want <= {a["verb"] for a in door.ops("door_ask")}


def test_a_command_nobody_is_acting_for_is_never_asked_or_told(door):
    """An alarm's music, the discovery jobs, a routine: no turn, no caller, so no actor is noted. Control run: with
    ``before`` asking as the panel when nobody is noted, asks are seen."""
    assert kernel_door.acting() is None
    assert scenarios.run_all() == ON_MAIN
    time.sleep(0.3)
    assert door.calls == []


# ---- QUIET: sent first, told afterwards -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kernel", ["allow", "refuse:unknown_person", "slow", "gone"])
@pytest.mark.parametrize("name, what, verb", [("music_screen_pause", "player_queues/pause", "music.stop"),
                                              ("music_intent_volume_down", "players/cmd/volume_down", "music.quieter"),
                                              ("music_screen_volume_set", "players/cmd/volume_set", "music.quieter"),
                                              ("home_screen_off", "house", "light.off"),
                                              ("home_screen_media_pause", "house", "media.off")])
@pytest.mark.asyncio
async def test_stopping_and_turning_off_are_sent_at_once_whatever_the_kernel_does(door, caplog, monkeypatch, kernel, name, what, verb):
    """The safety rule: the command is SENT FIRST and the kernel is told afterwards. Slow (0.8 s here), gone or refusing, the
    command has left when the call returns and the call did not wait. Control run: with these verbs asked first (``quiet``
    forced False) the refusing kernel stops the command and the slow one makes the call wait out the ask budget."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 1.0)
    if kernel == "slow":
        door.delay = 0.8
    elif kernel == "gone":
        door.close()
    else:
        door.door = kernel
    log: list = []
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        out = await _run(name, log)
        took = time.monotonic() - started
        assert len(_sent(log, what)) == 1 and took < 0.4, took
        assert out == ON_MAIN[name][-1][1]
        if kernel in ("allow", "refuse:unknown_person"):
            (ask,) = _wait(door, "door_ask", 1)
            assert (ask["verb"], ask["person"], ask["visibility"]) == (verb, ANN, "household")
            await _settled(caplog, 1)
            assert ("decision=recorded" in _lines(caplog)[-1]) is (kernel == "allow")
            assert ("decision=objected" in _lines(caplog)[-1]) is (kernel != "allow")
        else:
            await _settled(caplog, 1)


# ---- asked first: refused = nothing is sent ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_refused_music_action_is_not_sent_and_each_caller_says_so(door):
    """Control run: with the refusal not raised in ``before`` the play command is sent."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import music as rest

    door.door = "refuse:unknown_person"
    line = action_doors.MusicRefused("unknown_person").line()
    for name, what in (("music_screen_play", "player_queues/play_media"), ("music_screen_next", "player_queues/next"),
                       ("music_screen_volume_up", "players/cmd/volume_up"), ("music_screen_queue_clear", "player_queues/clear"),
                       ("music_screen_favorite", "music/favorites/add_item")):
        log: list = []
        with pytest.raises(action_doors.MusicRefused):
            await _run(name, log)
        assert _sent(log, what) == [], name
    import skybridge_service as sky

    log = []
    scenarios.World(log)
    kernel_door.note_actor(ANN)
    out = await sky.resolve_skybridge_request("play womble", ANN)
    assert out["spoken_summary"] == line and out["intent"]["status"] == "refused" and _sent(log, "player_queues/play_media") == []
    log = []
    assert await _run("music_intent_play", log) == line and _sent(log, "player_queues/play_media") == []
    assert door.ops("door_report") == []
    app = FastAPI()
    app.include_router(rest.router)
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)

    async def member():
        kernel_door.note_actor(ANN)
        return {"user_id": ANN, "role": "user"}

    import music_jukebox
    import panel_principal

    app.dependency_overrides[panel_principal.require_member_or_panel] = member
    app.dependency_overrides[music_jukebox.require_member_panel_or_jukebox] = member
    scenarios.World([])
    with TestClient(app) as client:
        out = client.post("/api/music/control", json={"action": "next"})
        assert (out.status_code, out.json()) == (403, {"detail": line})


@pytest.mark.parametrize("reason", ["adults_only", "unknown_person"])
@pytest.mark.asyncio
async def test_a_refused_command_to_the_house_is_not_sent_and_each_caller_says_so(door, reason):
    """The panel route raises (403 through the one app handler), the spoken path says the sentence, the brain's tool returns an
    error result, music through a house speaker says it. Control run: with the refusal swallowed as "the bridge is down" in
    ``smart_home_service._ha_post`` the spoken turn says it could not reach the house instead of the sentence."""
    import skybridge_service as sky

    door.door = f"refuse:{reason}"
    line = action_doors.HomeRefused(reason).line()
    for name in ("home_screen_turn_on", "home_screen_toggle_a_switch", "home_screen_scene"):
        log: list = []
        with pytest.raises(action_doors.HomeRefused) as exc:
            await _run(name, log)
        assert exc.value.reason == reason and _sent(log, "house") == [], name
    log = []
    assert await _run("home_tool_turn_on", log) == {"error": line, "status": "refused"} and _sent(log, "house") == []
    log = []
    assert await _run("music_house_volume_set", log) == line and _sent(log, "house") == []
    log = []
    w = scenarios.World(log)
    kernel_door.note_actor(ANN)
    out = await sky.resolve_skybridge_request("turn on the kitchen lamp", ANN)
    assert out["spoken_summary"] == line and out["intent"]["status"] == "refused" and _sent(log, "house") == []
    assert door.ops("door_report") == [] and w is not None


@pytest.mark.parametrize("how,reason", [("close", "unavailable"), ("old_kernel", "protocol"), ("slow", "timeout")])
@pytest.mark.asyncio
async def test_an_ordinary_command_proceeds_not_checked_when_the_kernel_cannot_answer(door, caplog, how, reason):
    """Control run: with ``not_checked`` treated as a refusal for every command, the light and the music are not changed."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    log: list = []
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        assert await _run("home_screen_turn_on", log) == ON_MAIN["home_screen_turn_on"][-1][1]
        assert await _run("music_screen_next", log) == ON_MAIN["music_screen_next"][-1][1]
    assert len(_sent(log, "house")) == 1 and len(_sent(log, "player_queues/next")) == 1
    assert f"area=home verb=light.on decision=not_checked reason={reason}" in _lines(caplog)[0]


# ---- SECURE: opening the house is asked first, and kernel down means NO --------------------------------------------------------------------
@pytest.mark.parametrize("how", ["close", "old_kernel", "slow", "refuse:adults_only"])
@pytest.mark.parametrize("name", ["home_screen_unlock", "home_screen_open_a_cover", "home_tool_unlock"])
@pytest.mark.asyncio
async def test_opening_the_house_is_never_done_unchecked(door, how, name):
    """A lock or a cover, and any act but closing it: ALWAYS asked first, and a kernel that is down, old or slow means NO.
    Control run: with ``secure`` forced False (kernel down = proceed, as for a light) the unlock is sent with the kernel gone."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    log: list = []
    if name == "home_tool_unlock":
        out = await _run(name, log)
        assert out["status"] == "refused" and out["error"] == action_doors.HomeRefused("adults_only" if "refuse" in how else "unavailable").line()
    else:
        with pytest.raises(action_doors.HomeRefused) as exc:
            await _run(name, log)
        assert exc.value.reason == ("adults_only" if "refuse" in how else "unavailable")
    assert _sent(log, "house") == []


@pytest.mark.asyncio
async def test_an_allowed_unlock_is_sent_and_closing_or_locking_is_an_ordinary_command(door, caplog):
    """Allowed by the kernel, the unlock goes out and is reported. LOCKING and CLOSING cannot open the house: with the kernel
    gone they proceed like any other command. An unknown service on a lock is treated as opening it. Control run: with
    ``home_secure`` true for ``close`` too, the lock command is refused with the kernel gone."""
    from routers import ha_control

    log: list = []
    assert (await _run("home_screen_unlock", log))["ok"] is True and len(_sent(log, "house")) == 1
    (ask,) = _wait(door, "door_ask", 1)
    assert (ask["verb"], ask["target"], ask["person"]) == ("lock.open", "lock.front_door", ANN)
    assert [r["outcome"] for r in _wait(door, "door_report", 1)] == ["done"]
    door.door = "close"
    log = []
    w = scenarios.World(log)
    kernel_door.note_actor(ANN)
    caller = {"user_id": ANN, "role": "user"}
    for entity, action in (("lock.front_door", "lock"), ("cover.garage_door", "close")):
        assert (await ha_control.ha_control(ha_control.HAControlPayload(entity_id=entity, action=action), caller=caller))["ok"] is True
    assert len(_sent(log, "house")) == 2 and w is not None
    with pytest.raises(action_doors.HomeRefused):
        await ha_control.ha_control(ha_control.HAControlPayload(entity_id="lock.front_door", action="some_new_service"), caller=caller)
    assert len(_sent(log, "house")) == 2


# ---- what crosses, and how a send that failed is reported -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ask_carries_ids_and_closed_codes_only_and_the_panel_is_never_named(door, caplog):
    """Control run: with the friendly name sent as the target, the name assertion fails."""
    log: list = []
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        await _run("home_spoken_turn_on", log)
        await _run("music_screen_play", log)
        await _run("home_screen_turn_on", log, who="guest")
    asks = _wait(door, "door_ask", 3)
    assert [(a["area"], a["verb"], a["target"]) for a in asks] == [("home", "light.on", "light.kitchen_lamp"), ("music", "music.play", "ply0001"),
                                                                   ("home", "light.on", "light.kitchen_lamp")]
    for a in asks[:2]:
        assert set(a) == {"area", "verb", "person", "target", "visibility", "stamps"} and a["person"] == ANN and a["visibility"] == "household"
    assert (asks[2].get("via"), asks[2]["person"]) == ("panel", "panel") and "stamps" not in asks[2]
    sent = json.dumps(door.calls)
    assert all(word not in sent for word in ("Kitchen", "Womble", "womble", "Quib", "guest", "brightness"))
    assert [r["outcome"] for r in _wait(door, "door_report", 3)] == ["done"] * 3
    assert all(word not in "".join(_lines(caplog)) for word in ("kitchen", ANN, "ply0001"))


@pytest.mark.asyncio
async def test_a_command_the_house_or_the_music_engine_did_not_take_is_reported_failed(door):
    """Control run: with ``after`` always reporting ``done`` both of these say ``done``."""
    def refuses(w):
        w.ha_post = (500, {"message": "no"})

    import music_service

    log: list = []
    with pytest.raises(Exception):
        await _run("home_screen_turn_on", log, setup=refuses)
    w = scenarios.World(log)
    w.ma_status = 500
    kernel_door.note_actor(ANN)
    assert await music_service._ma_ok("player_queues/next", queue_id="ply0001") is False
    assert [r["outcome"] for r in _wait(door, "door_report", 2)] == ["failed", "failed"]


# ---- the verbs --------------------------------------------------------------------------------------------------------------------------
def test_every_command_maps_to_a_closed_verb_and_reads_map_to_none():
    """Control run: with ``players/cmd/volume_set`` mapped to ``music.louder`` (asked first) the quiet assertion fails."""
    music = os_shadow.AREAS["music"].verbs
    for command, verb in action_doors._MUSIC_EXACT.items():
        assert verb in music and action_doors.music_verb(command, {}) == verb
    assert action_doors.music_verb("players/cmd/volume_mute", {"muted": True}) == "music.stop"
    assert action_doors.music_verb("players/cmd/volume_mute", {"muted": False}) == "music.louder"
    assert action_doors.music_verb("players/cmd/some_new_thing", {}) == "music.play"            # an unknown command is still asked
    for read in ("players/all", "player_queues/all", "player_queues/items", "music/search", "music/item_by_uri", "config/providers",
                 "music/recently_played_items", "providers"):
        assert action_doors.music_verb(read, {}) is None, read
    assert {action_doors.music_verb(c, {}) for c in ("player_queues/pause", "player_queues/stop", "players/cmd/volume_down",
                                                     "players/cmd/volume_set")} == set(action_doors.MUSIC_QUIET)
    home = os_shadow.AREAS["home"].verbs
    seen = set()
    for domain in (*action_doors._CLASS_OF, "vacuum"):
        for action in (*action_doors._ACT_OF, "toggle", "set_temperature"):
            verb, entity = action_doors.home_verb("/devices/control", {"entity_id": f"{domain}.thing", "action": action})
            assert verb in home and entity == f"{domain}.thing"
            seen.add(verb)
    assert action_doors.home_verb("/scenes/activate", {"scene_id": "scene.movie_time"}) == ("scene.on", "scene.movie_time")
    assert {"light.off", "media.off"} == set(action_doors.HOME_QUIET) <= seen
    assert [v for v in sorted(seen) if action_doors.home_secure(v)] == ["cover.off", "cover.on", "cover.open", "cover.set", "lock.off",
                                                                         "lock.on", "lock.open", "lock.set"]
    assert not action_doors.home_secure("lock.close") and not action_doors.home_secure("cover.close")


# ---- a child's level ------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_level_is_set_only_through_the_ask_and_never_while_the_kernel_cannot_be_asked(door, tmp_path, monkeypatch):
    """Control run: with ``not_checked`` treated as allowed in ``home_level.set_level`` the level is written with the kernel gone."""
    where = tmp_path / home_level.FILE
    assert await home_level.set_level(ANN, "member-kip", "comfort", where=where) == "comfort"
    assert await home_level.set_level(ANN, "member-kop", "light,switch,light", where=where) == ["light", "switch"]
    assert home_level.read(where) == {"member-kip": "comfort", "member-kop": ["light", "switch"]} and os.stat(where).st_mode & 0o777 == 0o600
    (ask, _) = _wait(door, "door_ask", 2)
    assert (ask["area"], ask["verb"], ask["person"], ask["owner"]) == ("home", "level.set", ANN, "member-kip") and "target" not in ask
    assert [r["outcome"] for r in _wait(door, "door_report", 2)] == ["done", "done"]
    for bad in ("everything", "light,rocket", ""):
        with pytest.raises(home_level.LevelError):
            await home_level.set_level(ANN, "member-kip", bad, where=where)
    for kernel, reason in (("refuse:adults_only", "adults_only"), ("close", "unavailable")):
        door.door = kernel
        with pytest.raises(action_doors.HomeRefused) as exc:
            await home_level.set_level("member-kip", "member-kip", "most", where=where)
        assert exc.value.reason == reason
    assert home_level.read(where)["member-kip"] == "comfort"
    monkeypatch.setenv("ZOE_OS_HOME", "off")                    # door off: written as the roster tool writes its own
    assert await home_level.set_level(ANN, "member-kip", "none", where=where) == "none" and home_level.read(where)["member-kip"] == "none"


# ---- structure --------------------------------------------------------------------------------------------------------------------
def test_each_sender_asks_before_it_sends_and_every_entry_point_notes_who_is_acting():
    """In the two senders the door's ``before`` comes before the POST and ``after`` follows it. And each authenticated entry
    point notes the caller for the doors. Control run: with ``before`` moved after the POST in ``home_control.send`` this
    fails; with ``_note_actor`` dropped from ``panel_principal.record_turn_home_control`` the pin fails."""
    for module, fn_name in (("home_control.py", "send"), ("music_service.py", "_ma_response")):
        tree = ast.parse((SVC / module).read_text("utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        calls = [(n.lineno, getattr(n.func, "attr", "")) for n in ast.walk(fn) if isinstance(n, ast.Call)]
        before = [ln for ln, name in calls if name == "before"]
        posts = [ln for ln, name in calls if name == "post"]
        after = [ln for ln, name in calls if name == "after"]
        assert len(before) == 1 and posts and before[0] < min(posts) and after and max(after) > min(posts), module
    pp = (SVC / "panel_principal.py").read_text("utf-8")
    for needle in ("    record_home_control(allowed)\n    _note_actor(user)\n", "    if await may_control_home(request, user):\n        _note_actor(user)\n"):
        assert needle in pp, needle
    assert "panel_principal._note_actor(user)" in (SVC / "routers" / "ha_control.py").read_text("utf-8")
    assert (SVC / "music_jukebox.py").read_text("utf-8").count("panel_principal._note_actor(") == 2
    assert '_pp._note_actor({"user_id": user_id})' in (SVC / "routers" / "system.py").read_text("utf-8")
