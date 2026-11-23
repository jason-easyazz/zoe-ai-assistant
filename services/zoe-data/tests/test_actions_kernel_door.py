"""Music and home control go to the Zoe OS kernel, a flag each (``ZOE_OS_MUSIC=kernel``, ``ZOE_OS_HOME=kernel``;
docs/knowledge/zoe-os-music-home-door.md). Nothing is stored for either: the door sits at the one sender of each.

* any other flag value: NO kernel call and exactly main's outbound calls for every scenario; the same with the doors on and
  the kernel allowing; and a command nobody is acting for (automatic) is never asked or told;
* QUIET commands (stop / turn down the music; a light or a media player off) are SENT FIRST and told afterwards: a kernel that
  is slow, gone or says no neither delays nor stops them;
* SECURE commands (a lock or a cover, anything but closing it) are ALWAYS asked first and a kernel that cannot answer means NO;
* the service's own adults-only check for unlocking and opening (``home_opening``, main #2715) stays on whatever the flag
  says and runs first: BOTH must agree before anything is sent. A command it refuses is not sent and the kernel is never
  asked about it (nor told); a command it allows is still not sent when the kernel refuses or cannot answer;
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
import home_opening
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


#: scenario -> the ONE ask (verb, target) the kernel gets for it, as the acting member, with the doors on and allowing; every
#: scenario not listed asks nothing (a read, a refusal the service made first, nothing matched, an unknown action)
ASKS = {
    "music_screen_pause": ("music.stop", "ply0001"),
    "music_screen_next": ("music.play", "ply0001"),
    "music_screen_volume_set": ("music.quieter", "ply0001"),
    "music_screen_volume_up": ("music.louder", "ply0001"),
    "music_screen_seek": ("music.play", "ply0001"),
    "music_screen_transfer": ("music.play", None),
    "music_screen_play": ("music.play", "ply0001"),
    "music_screen_play_media": ("music.play", "ply0002"),
    "music_screen_queue_clear": ("music.queue", "ply0001"),
    "music_screen_queue_remove": ("music.queue", "ply0001"),
    "music_screen_queue_play_index": ("music.play", "ply0001"),
    "music_screen_favorite": ("music.library", None),
    "music_screen_unfavorite": ("music.library", None),
    "music_screen_group": ("music.play", "ply0001"),
    "music_screen_queue_move": ("music.queue", "ply0001"),
    "music_screen_queue_save": ("music.library", "ply0001"),
    "music_screen_playlist_add": ("music.library", None),
    "music_screen_dont_stop": ("music.play", "ply0001"),
    "music_screen_ungroup": ("music.play", "ply0002"),
    "music_spoken_play": ("music.play", "ply0001"),
    "music_spoken_play_guest": ("music.play", "ply0001"),
    "music_spoken_pause": ("music.stop", "ply0001"),
    "music_spoken_transfer": ("music.play", None),
    "music_spoken_discovery": ("music.play", "ply0001"),
    "music_intent_favorite": ("music.library", None),
    "music_intent_pause": ("music.stop", "ply0001"),
    "music_intent_volume_down": ("music.quieter", "ply0001"),
    "music_intent_volume_set": ("music.quieter", "ply0001"),
    "music_intent_play": ("music.play", "ply0001"),
    "music_house_pause": ("media.off", "media_player.all"),
    "music_house_volume_set": ("media.set", "media_player.all"),
    "home_screen_turn_on": ("light.on", "light.kitchen_lamp"),
    "home_screen_off": ("light.off", "light.hall_lamp"),
    "home_screen_toggle_a_switch": ("switch.set", "switch.kettle_plug"),
    "home_screen_scene": ("scene.on", "scene.movie_time"),
    "home_screen_open_a_cover": ("cover.open", "cover.garage_door"),
    "home_screen_unlock": ("lock.open", "lock.front_door"),
    "home_screen_unlock_panel_adult_voice_session": ("lock.open", "lock.front_door"),
    "home_screen_lock_bare_panel": ("lock.close", "lock.front_door"),
    "home_screen_close_a_cover_guest": ("cover.close", "cover.garage_door"),
    "home_screen_media_pause": ("media.off", "media_player.lounge"),
    "home_screen_bridge_refuses": ("light.on", "light.kitchen_lamp"),
    "home_screen_bridge_offline": ("light.on", "light.kitchen_lamp"),
    "home_spoken_turn_on": ("light.on", "light.kitchen_lamp"),
    "home_spoken_turn_off": ("light.off", "light.hall_lamp"),
    "home_spoken_brightness": ("light.on", "light.hall_lamp"),
    "home_spoken_scene": ("scene.on", "scene.movie_time"),
    "home_said_turn_on": ("light.on", "light.kitchen_lamp"),
    "home_said_open_a_cover": ("cover.on", "cover.garage_door"),
    "home_tool_turn_on": ("light.on", "light.kitchen_lamp"),
    "home_tool_unlock": ("lock.open", "lock.front_door"),
    "home_tool_lock_guest": ("lock.close", "lock.front_door"),
    "home_spoken_open_a_cover": ("cover.on", "cover.garage_door"),
    "home_helper_open_a_cover": ("cover.on", "cover.garage_door"),
    "home_helper_lamp_guest": ("light.on", "light.kitchen_lamp"),
    "home_tool_bridge_offline": ("light.on", "light.kitchen_lamp"),
}


def test_with_the_doors_on_and_allowing_every_entry_point_sends_exactly_what_main_sent(door):
    """The doors add no outbound call and change no result when the kernel allows; and EACH scenario makes exactly the ask
    listed in ``ASKS`` (its verb and target, as the acting member, once) and closes it with one report, and every other
    scenario asks nothing. (A union of the verbs over the whole run was checked before, which a command mapped to the wrong
    verb passed whenever another scenario asked that verb.) Controls run: with the gate sending the command twice when allowed,
    every command scenario gains a call; ``"volume_down": "off"`` back in ``_ACT_OF`` leaves this table unchanged (no scenario
    turns a house speaker down: that is ``test_a_child_at_level_none_turning_a_speaker_down_reaches_no_sender``), and
    ``media_pause`` read as ``set`` fails it at ``home_screen_media_pause`` and ``music_house_pause``."""
    def quiet_for(span=0.15, limit=3.0):            # the door's own thread has nothing more to deliver
        end, last, since = time.monotonic() + limit, -1, time.monotonic()
        while time.monotonic() < end:
            if len(door.calls) != last:
                last, since = len(door.calls), time.monotonic()
            elif time.monotonic() - since >= span:
                return
            time.sleep(0.01)

    kernel_door.note_actor(ANN)
    assert scenarios.run_all() == ON_MAIN
    assert set(ASKS) <= set(scenarios.scenarios())
    quiet_for()
    for name in scenarios.scenarios():
        want = [ASKS[name]] if name in ASKS else []
        door.calls.clear()
        kernel_door.note_actor(ANN)
        assert scenarios.run_one(name) == ON_MAIN[name], name
        _wait(door, "door_report", len(want), timeout=3.0)          # a told-afterwards verb arrives on the door's own thread
        quiet_for()
        asks, reports = door.ops("door_ask"), door.ops("door_report")
        assert [(a["verb"], a.get("target")) for a in asks] == want, name
        assert all(a["person"] == ANN and a["area"] == ("music" if a["verb"].startswith("music.") else "home") for a in asks), name
        assert len(door.ops("door_report")) == len(want), (name, reports)


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


# ---- turning a house speaker DOWN is not "off": asked first, a refusal is final (review #2703) -----------------------------------------
KID_AT_NONE = "member-kip"                        # a child whose level is ``none``: the kernel refuses them every home command


async def _turn_down_through(sender: str, log: list):
    """``volume_down`` on a house media player, by a child, through each entry point that can send it."""
    import panel_principal
    import zoe_agent
    from routers import ha_control

    child = {"user_id": KID_AT_NONE, "role": "user"}
    w = scenarios.World(log)
    kernel_door.note_actor(KID_AT_NONE)
    if sender == "screen":
        try:
            return await ha_control.ha_control(ha_control.HAControlPayload(entity_id="media_player.lounge", action="volume_down"),
                                               caller=dict(child))
        except kernel_door.Refused as exc:
            return exc.line()
    if sender == "tool":
        assert await panel_principal.record_turn_home_control(None, dict(child)) is True
        return (await zoe_agent._ha_control("media_player.lounge", "volume_down")).get("error")
    import intent_router                                          # "turn it down" while the music plays through the house

    w.ma_silent = True
    return await intent_router._execute_music_intent(intent_router.Intent("music_control", {"command": "volume_down"}), KID_AT_NONE)


@pytest.mark.parametrize("sender, entity", [("screen", "media_player.lounge"), ("tool", "media_player.lounge"), ("house_music", "media_player.all")])
@pytest.mark.asyncio
async def test_a_child_at_level_none_turning_a_speaker_down_reaches_no_sender(door, sender, entity):
    """``volume_down`` was mapped to ``off``, so it was ``media.off``: QUIET, sent before the kernel was asked, and a child the
    kernel refuses every home command (level ``none``) could turn a house speaker down. Now it is ``media.set``: asked first,
    as the child, and the refusal is final: nothing reaches the bridge and the caller says the kernel's sentence.
    Named control, in the test: the same child pausing the same speaker (``media_pause``: a media player OFF, quiet by the
    contract) is still sent first and only told. Control run (the old mapping): ``"volume_down": "off"`` put back in ``action_doors._ACT_OF`` ->
    the command is sent for every entry point, before the kernel is even told (``media.off``, afterwards)."""
    door.door = "refuse:adults_only"
    log: list = []
    told = await _turn_down_through(sender, log)
    assert _sent(log, "house") == [] and told == action_doors.HomeRefused("adults_only").line(), (sender, told)
    asks = _wait(door, "door_ask", 1)
    assert [(a["area"], a["verb"], a["person"], a["target"]) for a in asks] == [("home", "media.set", KID_AT_NONE, entity)]
    door.calls.clear()
    log = []
    scenarios.World(log)
    kernel_door.note_actor(KID_AT_NONE)
    from routers import ha_control

    out = await ha_control.ha_control(ha_control.HAControlPayload(entity_id="media_player.lounge", action="pause"),
                                      caller={"user_id": KID_AT_NONE, "role": "user"})
    assert out["ok"] and [e[3]["action"] for e in _sent(log, "house")] == ["media_pause"]      # the control: off is sent first
    told_after = _wait(door, "door_ask", 1)
    assert [(a["verb"], a["person"]) for a in told_after] == [("media.off", KID_AT_NONE)]


#: every (domain, service) the house treats as QUIET (sent first, told afterwards): turning a light or a media player OFF
_HOME_QUIET_PAIRS = {("light", "turn_off"), ("light", "off"), ("media_player", "turn_off"), ("media_player", "off"),
                     ("media_player", "media_pause"), ("media_player", "media_stop")}
#: every Music Assistant command (with its arguments) the music door treats as QUIET: stopping or turning down, as documented
_MUSIC_QUIET_COMMANDS = {("player_queues/pause", "{}"), ("player_queues/stop", "{}"), ("players/cmd/volume_down", "{}"),
                         ("players/cmd/volume_set", "{}"), ("players/cmd/volume_mute", '{"muted": true}'),
                         ("players/cmd/power", '{"powered": false}')}


def test_only_turning_off_is_quiet_in_the_house_and_only_stopping_or_turning_down_for_the_music():
    """EVERY mapping-table entry and every quiet rule, by what it lets through BEFORE the ask: over every domain the door
    knows (and one it does not) and every service it maps or the bridge accepts, the quiet ones are exactly
    ``_HOME_QUIET_PAIRS``; over every Music Assistant command it maps (and the argument-dependent ones both ways) exactly
    ``_MUSIC_QUIET_COMMANDS``. A new alias mapped to ``off`` (as ``volume_down`` and ``volume_mute`` were), or ``media_pause``
    read as ``off`` for a light, fails here. Control run: ``"volume_mute": "off"`` put back in ``_ACT_OF`` fails this."""
    bridge = ("turn_on", "turn_off", "toggle", "set_brightness", "set_color", "set_temperature", "media_play", "media_pause",
              "media_next_track", "media_previous_track", "volume_up", "volume_down", "volume_set", "play_media", "volume_mute",
              "media_stop", " Turn_Off ")
    quiet = set()
    for domain in (*action_doors._CLASS_OF, "vacuum"):
        for action in {*action_doors._ACT_OF, *bridge}:
            verb, _ = action_doors.home_verb("/devices/control", {"entity_id": f"{domain}.thing", "action": action})
            if verb in action_doors.HOME_QUIET:
                quiet.add((domain, action.strip().lower()))
    assert quiet == _HOME_QUIET_PAIRS
    assert action_doors.home_verb("/devices/control", {"entity_id": "media_player.x", "action": "volume_down"})[0] == "media.set"
    assert action_doors.home_verb("/scenes/activate", {"scene_id": "scene.movie_time"})[0] not in action_doors.HOME_QUIET
    commands = {(c, "{}") for c in action_doors._MUSIC_EXACT} | {("players/cmd/volume_mute", '{"muted": true}'),
                ("players/cmd/volume_mute", '{"muted": false}'), ("players/cmd/power", '{"powered": false}'),
                ("players/cmd/power", '{"powered": true}'), ("players/cmd/some_new_thing", "{}")}
    assert {(c, a) for c, a in commands if action_doors.music_verb(c, json.loads(a)) in action_doors.MUSIC_QUIET} == _MUSIC_QUIET_COMMANDS


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


# ---- BOTH must agree: the service's adults-only check first, then the kernel (main #2715 under the home door) ----------------
GROWN_UP, CHILD = {"user_id": ANN, "role": "user"}, {"user_id": "member-kip", "role": "user"}


async def _open_through(sender, who):
    """One command that opens the house, through one sender, for caller ``who``, entered the way that sender is entered.
    Returns ``(what the caller is told, the scenario log)``; the telling is the refusal sentence when there is one."""
    import panel_principal
    import skybridge_service as sky
    import smart_home_service
    import zoe_agent
    from fastapi import HTTPException
    from routers import ha_control

    log: list = []
    w = scenarios.World(log)
    if sender == "screen":                      # the panel's route: its dependency notes the caller for the kernel
        panel_principal._note_actor(who)
        try:
            out = await ha_control.ha_control(ha_control.HAControlPayload(entity_id="lock.front_door", action="unlock"), caller=dict(who))
            return ("sent" if out["ok"] else out), log
        except HTTPException as exc:
            return exc.detail, log
        except kernel_door.Refused as exc:
            return exc.line(), log
    assert await panel_principal.record_turn_home_control(None, dict(who)) is True      # a chat / voice turn of a member
    if sender == "tool":
        out = await zoe_agent._ha_control("lock.front_door", "unlock")
        return out.get("error", "sent"), log
    if sender == "helper":
        try:
            return ("sent" if await smart_home_service.set_device("cover.garage_door", True) else "not sent"), log
        except kernel_door.Refused as exc:
            return exc.line(), log
    _cover_listed_as_a_switch(w)
    out = await sky.resolve_skybridge_request("turn on the garage door", who["user_id"])
    return ("sent" if out.get("controlled_entities") else out["spoken_summary"]), log


SENDERS_THAT_OPEN = ("screen", "tool", "spoken", "helper")


@pytest.mark.parametrize("how", ["allow", "refuse:adults_only", "close", "slow"])
@pytest.mark.parametrize("sender", SENDERS_THAT_OPEN)
@pytest.mark.asyncio
async def test_what_the_service_refuses_is_never_sent_and_the_kernel_is_never_asked_about_it(door, sender, how):
    """A child (a member, so the house is theirs to control, but not an adult) asks to unlock or open. The service's own
    check refuses, on whatever the flag says: nothing is sent, the service's sentence is what the caller gets, and the kernel
    is neither asked nor told (the command never reaches the door in ``home_control.send``). The kernel ALLOWING does not
    send it. Control run: with ``home_opening.refused`` answering False for every caller, the allowing kernel's case sends the
    command for each of the four senders."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    told, log = await _open_through(sender, CHILD)
    assert _sent(log, "house") == []
    assert told == ("not sent" if sender == "helper" else home_opening.REFUSAL)
    await asyncio.sleep(0.2)
    assert door.ops("door_ask") == [] and door.ops("door_report") == []


@pytest.mark.parametrize("how,reason", [("refuse:adults_only", "adults_only"), ("close", "unavailable"), ("old_kernel", "unavailable"),
                                        ("slow", "unavailable")])
@pytest.mark.parametrize("sender", SENDERS_THAT_OPEN)
@pytest.mark.asyncio
async def test_what_the_service_allows_is_still_not_sent_when_the_kernel_refuses_or_cannot_answer(door, sender, how, reason):
    """An adult member asks: the service's check passes, so the kernel is asked, as her. The kernel refusing, or gone, old or
    slow, means nothing is sent and the kernel's sentence is said. Controls run: with the secure rule dropped from
    ``action_doors.before`` (kernel gone = proceed, as for a light) the kernel-gone case sends; with the refusal not raised
    there either, the refused case sends."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    told, log = await _open_through(sender, GROWN_UP)
    assert _sent(log, "house") == []
    assert told == action_doors.HomeRefused(reason).line()
    if how == "refuse:adults_only":
        (ask,) = _wait(door, "door_ask", 1)
        assert ask["person"] == ANN and ask["verb"] in ("lock.open", "cover.on")


@pytest.mark.parametrize("sender", SENDERS_THAT_OPEN)
@pytest.mark.asyncio
async def test_when_the_service_and_the_kernel_both_allow_the_command_is_sent_once_and_reported(door, sender):
    """Control run: with ``home_opening.refused`` answering True for every caller the adult's command is not sent."""
    told, log = await _open_through(sender, GROWN_UP)
    assert told == "sent" and len(_sent(log, "house")) == 1
    (ask,) = _wait(door, "door_ask", 1)
    assert (ask["person"], ask["target"]) == (ANN, "lock.front_door" if sender in ("screen", "tool") else "cover.garage_door")
    assert [r["outcome"] for r in _wait(door, "door_report", 1)] == ["done"]


@pytest.mark.parametrize("data", [{"entity_id": "lock.front_door"}, {"entity_id": ["light.hall_lamp", "cover.garage_door"]},
                                  {"entity_id": "LOCK.Front_Door"}])
@pytest.mark.asyncio
async def test_a_lock_or_cover_named_as_the_services_own_target_is_asked_about_as_that_and_never_sent_unchecked(door, data):
    """A service's own target (``data.entity_id``) overrides the entity in the path. The verb was read from the path alone: a
    turn-on of a lamp whose data named a lock was asked about as ``light.on``, so with the kernel gone it was sent
    ``not_checked`` for anyone the service's check let through. It is asked about as the lock or cover now: secure, and a
    kernel that cannot answer means no. Control run: with ``home_verb`` reading the path's entity only, the adult's command
    is sent with the kernel gone."""
    from routers import ha_control

    verb, entity = action_doors.home_verb("/devices/control", {"entity_id": "light.kitchen_lamp", "action": "turn_on", "data": data})
    assert action_doors.home_secure(verb) and verb in ("lock.on", "cover.on") and entity.lower() in ("lock.front_door", "cover.garage_door")
    door.door = "close"
    log: list = []
    scenarios.World(log)
    kernel_door.note_actor(ANN)
    with pytest.raises(action_doors.HomeRefused) as exc:
        await ha_control.ha_control(ha_control.HAControlPayload(entity_id="light.kitchen_lamp", action="turn_on", params=data), caller=dict(GROWN_UP))
    assert exc.value.reason == "unavailable" and _sent(log, "house") == []
    for odd in (None, "x", ["lock.front_door"], {"entity_id": None}, {"entity_id": 7}):      # never raises; a lamp stays a lamp
        assert action_doors.home_verb("/devices/control", {"entity_id": "light.kitchen_lamp", "action": "turn_on", "data": odd}) == ("light.on", "light.kitchen_lamp")


def test_whatever_the_service_counts_as_opening_the_kernel_door_treats_as_secure():
    """The two checks read a command the same way: every (entity, service, service target) that ``home_opening.opens`` counts as
    opening the house maps to a SECURE verb at the door, so "kernel down means no" covers all of it. Control run: with
    ``valve`` dropped from ``action_doors._CLASS_OF`` an open valve is opening for the service and an ordinary command at the
    door, and this fails."""
    domains = (*sorted(home_opening.SECURE_DOMAINS), "light", "switch", "scene", "media_player", "vacuum")
    actions = (*sorted(home_opening.CLOSING), "unlock", "open", "open_cover", "open_valve", "alarm_disarm", "toggle", "turn_on",
               "turn_off", "set_cover_position", "some_new_service", " Unlock ", "")
    seen = 0
    for domain in domains:
        for action in actions:
            for data in (None, {}, {"entity_id": "lock.front_door"}, {"entity_id": ["light.a", "valve.water_main"]}):
                entity = f"{domain}.thing"
                if home_opening.opens(entity, action, data):
                    verb, _ = action_doors.home_verb("/devices/control", {"entity_id": entity, "action": action, "data": data})
                    assert action_doors.home_secure(verb), (entity, action, data, verb)
                    seen += 1
    assert seen > 150


#: every function of the service that sends a DEVICE command (it names ``home_control.DEVICE``) -> how the service's
#: adults-only check stands before that send
_CHECKED = "home_opening.refused is awaited in this function before the send"
DEVICE_SENDERS = {
    ("smart_home_service.py", "set_device"): _CHECKED,
    ("zoe_agent.py", "_ha_control"): _CHECKED,
    ("routers/ha_control.py", "ha_control"): _CHECKED,
    ("intent_router.py", "_post_music_ha_control"): "music through a house speaker: its target is the configured or listed MEDIA PLAYER, never an id a caller supplies",
}


def test_the_services_adults_only_check_stands_before_every_send_that_could_open_the_house():
    """READ FROM THE CODE: every function of the service that names the device command path is one of the four known senders;
    three await ``home_opening.refused`` BEFORE the call that sends, the fourth can only reach a media player. A new sender,
    or a check moved after its send, fails here. (Scenes go by their own path and are not under the check yet: #2716.)
    Controls run: with the check moved below the send in ``smart_home_service.set_device`` this fails naming it; with
    ``async def sneak(): await _bridge_post(home_control.DEVICE, {})`` added to ``routers/ha_control.py`` it fails naming that."""
    found = {}
    for path in sorted(SVC.rglob("*.py")):
        rel = path.relative_to(SVC).as_posix()
        if rel.startswith(("tests/", "alembic/", ".venv", "venv")) or "home_control.DEVICE" not in (text := path.read_text("utf-8")):
            continue
        for fn in ast.walk(ast.parse(text)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            sends = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and "home_control.DEVICE" in ast.unparse(n)
                     and not any(isinstance(inner, ast.Call) and inner is not n and "home_control.DEVICE" in ast.unparse(inner) for inner in ast.walk(n))]
            if sends:
                checks = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Await) and ast.unparse(n.value).startswith("home_opening.refused(")]
                found[(rel, fn.name)] = bool(checks) and min(checks) < min(sends)
    assert set(found) == set(DEVICE_SENDERS), {"sends a device command, not listed": sorted(set(found) - set(DEVICE_SENDERS)),
                                              "listed, sends none now": sorted(set(DEVICE_SENDERS) - set(found))}
    for key, why in DEVICE_SENDERS.items():
        assert found[key] is (why == _CHECKED), key


# ---- a TYPED Skybridge command: the route notes who is acting (review #2703) -----------------------------------------------------------
KID = "member-kip"


async def _typed(message, who, *, setup=None):
    """The typed Skybridge route itself (``POST /api/skybridge/resolve``), as a signed-in member ``who`` calls it, against the fake
    house and music engine. Nothing is noted beforehand: the route must note its caller."""
    from routers import skybridge

    log: list = []
    w = scenarios.World(log)
    if setup:
        setup(w)
    out = await skybridge.resolve_skybridge_command({"message": message}, user={"user_id": who, "role": "user"}, db=None,
                                                    request=None)
    return out, log


def _cover_listed_as_a_switch(w):
    """The worst case for a typed command: a bridge that lists a garage door among the switches. A turn-on of it is a cover
    ``on``: SECURE (asked first; a kernel that cannot answer means no)."""
    w.ha_answers["/switches"] = {"switches": [*scenarios.HA_ANSWERS["/switches"]["switches"], scenarios.GARAGE]}


@pytest.mark.asyncio
async def test_a_childs_typed_home_command_is_asked_as_the_child_and_a_refusal_sends_nothing(door):
    """Before the fix the typed route noted nobody: the command was AUTOMATIC, never asked, and the kettle plug was switched on
    for a child whose level does not allow switches. Control run: with ``panel_principal._note_actor(user)`` removed from
    ``routers/skybridge.resolve_skybridge_command`` no ask is made and the switch is sent."""
    door.door = "refuse:adults_only"
    out, log = await _typed("turn on the kettle plug", KID)
    assert out["spoken_summary"] == action_doors.HomeRefused("adults_only").line() and out["intent"]["status"] == "refused"
    assert _sent(log, "house") == []
    (ask,) = _wait(door, "door_ask", 1)
    assert (ask["area"], ask["verb"], ask["person"], ask["target"]) == ("home", "switch.on", KID, "switch.kettle_plug")


@pytest.mark.parametrize("who", [KID, ANN])
@pytest.mark.parametrize("how", ["allow", "refuse:adults_only", "close", "slow"])
@pytest.mark.asyncio
async def test_a_typed_command_that_could_open_the_house_is_refused_by_the_services_own_check_whatever_the_kernel_says(door, how, who):
    """The typed route notes who is acting for the kernel, but it is not a chat or voice turn for the service's adults-only
    check (``home_opening``): that check refuses an opening command there for everyone, exactly as with the flag off. The
    kernel allowing changes nothing, and the kernel is never asked about a command the service refused.
    Control run: with ``home_opening.refused`` answering False for every caller, the garage door's command is sent when the
    kernel allows and the kernel's own sentence (not the service's) is said in the other cases."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    out, log = await _typed("turn on the garage door", who, setup=_cover_listed_as_a_switch)
    assert out["spoken_summary"] == home_opening.REFUSAL and out["cards"] == []
    assert _sent(log, "house") == []
    await asyncio.sleep(0.2)
    assert door.ops("door_ask") == [] and door.ops("door_report") == []


@pytest.mark.parametrize("how,reason", [("refuse:adults_only", "adults_only"), ("close", "unavailable"), ("slow", "unavailable")])
@pytest.mark.asyncio
async def test_a_childs_typed_command_that_could_open_the_house_is_refused_and_never_sent_unchecked(door, monkeypatch, how, reason):
    """The KERNEL's layer of the typed route, on its own: with the service's check out of the way (stubbed to allow, as if
    the caller were someone it lets through), SECURE through the typed route is refused by the kernel, or not checkable
    (kernel gone or slow), and nothing is sent. Before the fix it went out unasked, kernel or no kernel. The ask itself is
    required, as the child, about the garage door (it was a subset check, which passed with no ask at all). Controls run: the
    same removal of the route's actor note sends the garage door's command in every case; with ``before`` refusing
    ``unavailable`` for a secure verb WITHOUT asking, nothing is sent and only the ask assertion fails."""
    async def allows(*a, **k):
        return True

    monkeypatch.setattr(home_opening, "may_open", allows)
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    out, log = await _typed("turn on the garage door", KID, setup=_cover_listed_as_a_switch)
    assert out["spoken_summary"] == action_doors.HomeRefused(reason).line()
    assert _sent(log, "house") == []
    asks = _wait(door, "door_ask", 1)       # the ask is REQUIRED: the kernel's refusal (or silence) is what stopped it
    assert [(a["area"], a["verb"], a["person"], a["target"]) for a in asks] == [("home", "cover.on", KID, "cover.garage_door")]


@pytest.mark.asyncio
async def test_a_typed_music_command_is_asked_and_a_refusal_plays_nothing(door):
    """Control run: with the route's actor note removed the music plays unasked."""
    door.door = "refuse:unknown_person"
    out, log = await _typed("play womble", KID)
    assert out["spoken_summary"] == action_doors.MusicRefused("unknown_person").line()
    assert _sent(log, "player_queues/play_media") == []
    assert [(a["area"], a["verb"], a["person"]) for a in _wait(door, "door_ask", 1)] == [("music", "music.play", KID)]


@pytest.mark.parametrize("value", [None, "off"])
@pytest.mark.asyncio
async def test_with_the_doors_off_a_typed_command_is_sent_as_before_and_the_kernel_is_never_called(door, monkeypatch, value):
    """Flag off: noting the actor changes nothing; the same typed commands are sent exactly as with no note at all, and the
    kernel is not called. The typed command that could open the house is NOT sent either way: the service's own check
    refuses it (main #2715). Control run: with ``before`` ignoring the area flag this sees a ``door_ask``."""
    for flag in FLAGS:
        if value is None:
            monkeypatch.delenv(flag, raising=False)
        else:
            monkeypatch.setenv(flag, value)
    sent = {}
    for message, what, setup in (("turn on the kettle plug", "house", None), ("turn on the garage door", "house", _cover_listed_as_a_switch),
                                 ("play womble", "player_queues/play_media", None)):
        out, log = await _typed(message, KID, setup=setup)
        sent[message] = [e[2:4] for e in _sent(log, what)]
        kernel_door._ACTOR.set(None)
        bare, bare_log = await _typed_without_note(message, KID, setup=setup)
        opening = "garage" in message
        assert [e[2:4] for e in _sent(bare_log, what)] == sent[message] and len(sent[message]) == (0 if opening else 1), message
        assert (out["spoken_summary"] == home_opening.REFUSAL) is opening
        assert {k: v for k, v in out.items() if k != "skybridge_context"} == {k: v for k, v in bare.items() if k != "skybridge_context"}
    assert door.ops("door_ask") == [] and door.ops("door_report") == []


async def _typed_without_note(message, who, *, setup=None):
    """The same command through the resolver the route calls, with no actor noted: what the route did before the fix."""
    import skybridge_service as sky

    log: list = []
    w = scenarios.World(log)
    if setup:
        setup(w)
    return await sky.resolve_skybridge_request(message, who, context=None, db=None), log


def test_power_on_is_asked_first_and_only_power_off_is_a_stop():
    """``players/cmd/power`` was always the quiet ``music.stop``: a power-ON sent first and told afterwards, a refusal ignored.
    Control run: with the old mapping (every power command a stop) the power-on assertion fails."""
    assert action_doors.music_verb("players/cmd/power", {"player_id": "ply0001", "powered": False}) == "music.stop"
    for args in ({"player_id": "ply0001", "powered": True}, {"player_id": "ply0001"}, {"powered": "false"}):
        assert action_doors.music_verb("players/cmd/power", args) == "music.play", args


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
def test_each_sender_asks_before_it_sends():
    """In the two senders the door's ``before`` comes before the POST and ``after`` follows it. Control run: with ``before``
    moved after the POST in ``home_control.send`` this fails."""
    for module, fn_name in (("home_control.py", "send"), ("music_service.py", "_ma_response")):
        tree = ast.parse((SVC / module).read_text("utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        calls = [(n.lineno, getattr(n.func, "attr", "")) for n in ast.walk(fn) if isinstance(n, ast.Call)]
        before = [ln for ln, name in calls if name == "before"]
        posts = [ln for ln, name in calls if name == "post"]
        after = [ln for ln, name in calls if name == "after"]
        assert len(before) == 1 and posts and before[0] < min(posts) and after and max(after) > min(posts), module


SENDERS = {("home_control.py", "send"), ("music_service.py", "_ma_response")}
NOTERS = {"note_actor", "_note_actor"}
_SETUP = "music provider sign-in / set-up: it reaches Music Assistant only with config and provider commands (no action verb)"
_READ = "a read: it asks Music Assistant which speakers there are and sends no command"
_AUTO = "automatic: nobody is acting (started by the service itself, not by a request)"
_INHERITS = "run as a task of a turn that has already noted its actor: a task copies its spawner's context, the actor with it"
_ROOM = "the LiveKit room agent: each room turn notes its own speaker (``_run_pipeline``, ``_run_text_pipeline``)"
#: entry points (routes, websocket handlers, spawned tasks) from which a sender is reachable but that note NO actor: each with why
NOTES_NOBODY = {
    ("routers/handoff.py", "handoff_start"): _SETUP,
    **{("routers/music_setup.py", fn): _SETUP for fn in ("browser_start", "oauth_start", "setup_form", "setup_save")},
    ("music_oauth.py", "_run_and_report"): _SETUP,
    ("ytmusic_signin.py", "_run_watcher"): _SETUP,
    ("ytmusic_signin.py", "refresh_now"): _AUTO,
    ("music_history.py", "observe_once"): _AUTO,
    ("routers/panel_config.py", "put_panel_config"): _READ,
    ("brain_dispatch.py", "brain_oneshot"): _INHERITS,                     # the chat stream's own brain call
    ("pi_intent_lab.py", "_execute_safe_fulfillment_intent"): _INHERITS,     # the lab route's (its dependency notes the operator)
    ("routers/voice_tts.py", "get_livekit_token"): _ROOM,
    **{("routers/voice_livekit.py", fn): _ROOM for fn in ("_agent_loop", "_collect_audio_stream", "_smart_turn_check",
                                                          "ensure_livekit_started")},
}


@pytest.fixture(scope="module")
def code():
    import actor_reach

    return actor_reach.Code(SVC)


def _unnoted(code) -> dict:
    """Every entry point from which a sender is reachable and that notes nobody: ``{entry point: 'route' | 'task'}``. An entry
    point NOTES when its own body (or a function it calls whose own body does) calls ``note_actor`` / ``_note_actor``, or one of
    its ``Depends(...)`` (any depth) does."""
    reach = code.reaching(SENDERS)
    direct = {k for k in code.edges if code.calls(k, NOTERS)}

    def notes(key) -> bool:
        return key in direct or bool(code.edges.get(key, set()) & direct)

    routes, tasks = code.routes(), code.spawned()
    out = {}
    for key in sorted((set(routes) | set(tasks)) & reach):
        deps = set(routes.get(key, ()))
        deps |= {d2 for d in list(deps) for d2 in code.dependencies(d)}
        if not (notes(key) or any(notes(d) for d in deps)):
            out[key] = "route" if key in routes else "task"
    return out


def test_every_entry_point_that_can_reach_a_sender_notes_who_is_acting(code):
    """The ENTRY POINTS ARE DERIVED FROM THE CODE (``tests/actor_reach.py``): the call graph, by import resolution, walked back
    from the two senders to every route, websocket handler and spawned task it reaches. Each must note who is acting (itself, a
    function it calls, or a dependency of it), or be listed in ``NOTES_NOBODY`` with why it acts for nobody. A command sent with
    nobody noted is AUTOMATIC: not asked, not told, so a child's level and the opening rule would not apply to it.
    Controls run: with the actor note removed from the typed Skybridge route (``routers/skybridge.py``) this fails naming
    ``resolve_skybridge_command`` (it is how review #2703 found the hole the old fixed list of snippets missed); with it removed
    from the LiveKit room turn (``_run_pipeline``) it fails naming that; with ``_note_actor`` dropped from
    ``panel_principal.record_turn_home_control`` it fails naming the chat and voice routes."""
    found = set(_unnoted(code))
    assert found == set(NOTES_NOBODY), {
        "reaches a sender, notes nobody, not listed": sorted(found - set(NOTES_NOBODY)),
        "listed, but notes an actor now or no longer reaches a sender": sorted(set(NOTES_NOBODY) - found)}
    for key, why in NOTES_NOBODY.items():
        assert isinstance(why, str) and len(why) > 30, key


def test_the_graph_reaches_the_entry_points_it_must(code):
    """The derivation has something to bite on: the routes every review has named are among the entry points it finds, and
    they note an actor. Control run: with the module-attribute edges (``m.fn``) dropped from ``actor_reach.Code._named`` the
    panel's control route is no longer found."""
    reach = code.reaching(SENDERS)
    routes = code.routes()
    for key in (("routers/skybridge.py", "resolve_skybridge_command"), ("routers/ha_control.py", "ha_control"),
                ("routers/chat.py", "chat"), ("routers/voice_tts.py", "voice_command"), ("routers/music.py", "music_play"),
                ("routers/system.py", "intent_dispatch"), ("routers/system.py", "a2a_task_stream"),
                ("routers/ui_actions.py", "confirm_action_form"), ("routers/proactive.py", "accept_pending_suggestion")):
        assert key in routes and key in reach, key
    assert len(routes) > 300 and len(reach) > 100
