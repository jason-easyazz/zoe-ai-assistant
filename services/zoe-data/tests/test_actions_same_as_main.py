"""Music and home control each have ONE way out of zoe-data, and nothing any entry point SENDS has changed against ``main``.

Neither area stores anything in Zoe: what an entry point does is the calls it makes to Music Assistant and to the Home
Assistant bridge. ``tests/fixtures/actions_as_on_main.json`` is the record of ``main`` (c1a350458, which has the adults-only
rule for unlocking and opening, ``home_opening.py``): for each entry point and
action, every outbound call (method, path, body, timeout; reads too) to a FAKE Music Assistant and a FAKE bridge, and the result
or refusal. It was made by running ``tests/actions_scenarios.py`` against an export of that commit, never by this branch's own
code. The same scenarios run here and must give the same record. It is also the "before the door" record the kernel-door tests
compare flag-off behaviour with.

Covered. MUSIC: the screen's control (pause, next, volume, an unknown action), seek, transfer, play, play a chosen result, the
queue routes (clear, remove, play an index, move, save as a playlist), favourite and unfavourite (the removal itself: the uri
resolves to its library row), add to a playlist, "don't stop", group and ungroup, the speaker picker (the household's default
speaker, a known and an unknown one); the spoken play (an adult and a guest) / pause / move and "play my discovery playlist",
each through Skybridge's own entry point ``resolve_skybridge_request`` (its routing, the user id it hands on, its whole
answer); the intent executor's pause, volume down, volume set, play, a delayed request and "I like this song"; music
through a house media player. HOME: the panel's
control route (on with brightness, off, toggle, a scene, open a cover, unlock, pause a media player, no entity, the bridge
refusing, the bridge offline); the spoken on / off / brightness / scene / no match at the resolver, and on / opening (an adult,
a guest) through Skybridge's entry point; the brain's tool (on, unlock, offline). A record holds each call's service and
header names, the user each play is journalled for and each device receipt is written for, and the whole return value
(``tests/actions_scenarios.py`` says exactly what). Which entry point each scenario RAN, and that it reached the sender, is
checked by execution (``test_every_listed_scenario_runs_the_entry_point_it_is_listed_for``), not by name.
UNLOCKING AND OPENING, each BOTH ways (``test_unlocking_or_opening_is_recorded_both_ways``): sent for an adult member (and for
the panel while an adult's verified voice session is open on it), refused with nothing sent for the bare panel, a guest, a
child, a child's voice session and a command on no turn at all; through the panel's route, the brain's tool, the spoken
resolver and the device helper under it. Locking and closing are sent for anyone. The scenarios' "account record" and "voice
session" are the fake world's (``World.adults``, ``World.voice_sessions``): no account store is read.

Negative controls run (each broke the code, saw the named scenario fail, restored): the scene path sent to the device path in
``smart_home_service.activate_scene`` -> ``home_spoken_scene``; ``raise_for_status`` dropped from the panel route's sender ->
``home_screen_bridge_refuses``; the body's ``data`` dropped in the brain's tool -> ``home_tool_turn_on``; ``home_control.send``
ignoring the caller's client (opening its own) -> ``music_house_pause``; ``args`` sent flat in ``music_service._ma_response`` ->
``music_screen_seek``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import actions_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "actions_as_on_main.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


_HOME, _MUSIC, _SCREEN, _ROUTER = "home_control", "music_service", "routers/music.py", "intent_router.py"
#: HOME: every function of the service that names ``home_control.send`` -> the scenarios that record it
HOME_RECORDED = {
    ("routers/ha_control.py", "_bridge_post", "send"): ("home_screen_turn_on", "home_screen_scene", "home_screen_bridge_offline",
                                                        "home_screen_unlock", "home_screen_unlock_guest", "home_screen_open_a_cover",
                                                        "home_screen_open_a_cover_bare_panel"),
    ("smart_home_service.py", "_ha_post", "send"): ("home_spoken_turn_on", "home_spoken_scene", "home_spoken_open_a_cover",
                                                    "home_spoken_open_a_cover_guest", "home_helper_open_a_cover",
                                                    "home_helper_open_a_cover_bare_panel"),
    ("zoe_agent.py", "_ha_control", "send"): ("home_tool_turn_on", "home_tool_bridge_offline", "home_tool_unlock",
                                              "home_tool_unlock_bare_panel"),
    (_ROUTER, "_post_music_ha_control", "send"): ("music_house_pause", "music_house_volume_set"),
}
HOME_RECORDED[("smart_home_service.py", "_ha_post", "send")] += ("home_spoken_turn_off", "home_spoken_brightness", "home_said_turn_on",
                                                                  "home_said_open_a_cover", "home_said_open_a_cover_guest",
                                                                  "home_helper_lamp_guest")
HOME_RECORDED[("routers/ha_control.py", "_bridge_post", "send")] += (
    "home_screen_off", "home_screen_toggle_a_switch", "home_screen_unlock_panel_adult_voice_session", "home_screen_lock_bare_panel",
    "home_screen_close_a_cover_guest", "home_screen_media_pause", "home_screen_bridge_refuses", "home_screen_unlock_child",
    "home_screen_unlock_bare_panel", "home_screen_unlock_panel_child_voice_session", "home_screen_no_entity")
HOME_RECORDED[("zoe_agent.py", "_ha_control", "send")] += ("home_tool_lock_guest", "home_tool_unlock_child", "home_tool_unlock_no_turn")
#: MUSIC: every function of the service that names a function of ``music_service`` that reaches Music Assistant -> its scenarios
MUSIC_RECORDED = {
    (_SCREEN, "music_control", _MUSIC): ("music_screen_pause", "music_screen_next", "music_screen_volume_set", "music_screen_volume_up",
                                         "music_screen_unknown_action"),
    (_SCREEN, "music_seek", _MUSIC): ("music_screen_seek",),
    (_SCREEN, "music_transfer", _MUSIC): ("music_screen_transfer",),
    (_SCREEN, "music_play", _MUSIC): ("music_screen_play",),
    (_SCREEN, "music_play_media", _MUSIC): ("music_screen_play_media",),
    (_SCREEN, "music_queue_clear", _MUSIC): ("music_screen_queue_clear",),
    (_SCREEN, "music_queue_remove", _MUSIC): ("music_screen_queue_remove",),
    (_SCREEN, "music_queue_play_index", _MUSIC): ("music_screen_queue_play_index",),
    (_SCREEN, "music_favorite", _MUSIC): ("music_screen_favorite",),
    (_SCREEN, "music_unfavorite", _MUSIC): ("music_screen_unfavorite",),
    (_SCREEN, "music_group", _MUSIC): ("music_screen_group",),
    (_SCREEN, "music_queue_move", _MUSIC): ("music_screen_queue_move",),
    (_SCREEN, "music_queue_save", _MUSIC): ("music_screen_queue_save",),
    (_ROUTER, "_execute_music_intent", _MUSIC): ("music_intent_favorite",),
    ("music_discovery.py", "play_discovery", _MUSIC): ("music_spoken_discovery",),
    (_SCREEN, "music_playlist_add", _MUSIC): ("music_screen_playlist_add",),
    (_SCREEN, "music_dont_stop", _MUSIC): ("music_screen_dont_stop",),
    (_SCREEN, "music_ungroup", _MUSIC): ("music_screen_ungroup",),
    ("skybridge_service.py", "resolve_skybridge_request", _MUSIC): ("music_spoken_play", "music_spoken_play_guest", "music_spoken_pause",
                                                                    "music_spoken_transfer", "music_spoken_discovery"),
    (_SCREEN, "set_preferred_player", _MUSIC): ("music_screen_set_preferred_player", "music_screen_set_preferred_unknown"),
    (_ROUTER, "_music_assistant_control", _MUSIC): ("music_intent_pause", "music_intent_volume_down", "music_intent_volume_set"),
    (_ROUTER, "_music_assistant_play", _MUSIC): ("music_intent_play", "music_intent_delayed"),
    (_ROUTER, "_music_assistant_active_player", _MUSIC): ("music_intent_pause", "music_intent_volume_down", "music_intent_volume_set",
                                                          "music_house_pause", "music_house_volume_set"),
}
_READ = "a read: it asks Music Assistant what is there or playing and sends nothing that changes it"
_SETUP = "provider sign-in / set-up: outside the door, as the record says (a config flow, not a command to a speaker)"
_AUTO = "automatic (the discovery playlist, the listening history): nobody is acting"
MUSIC_NOT_RECORDED = {
    **{(_SCREEN, fn, _MUSIC): _READ for fn in ("_queue_item_art", "music_groups", "music_now_playing", "music_playlist_tracks",
                                               "music_playlists", "music_recently_played", "music_recommendations", "music_search")},
    (_ROUTER, "_music_assistant_now_playing", _MUSIC): _READ, (_ROUTER, "_execute_music_setup", _MUSIC): _READ,
    ("routers/panel_config.py", "_known_player_ids", _MUSIC): _READ, ("routers/voice_tts.py", "_media_playing", _MUSIC): _READ,
    ("routers/music_setup.py", "setup_catalogue", _MUSIC): _READ, ("routers/music_setup.py", "setup_form", _MUSIC): _SETUP,
    ("routers/music_setup.py", "setup_save", _MUSIC): _SETUP, ("routers/music_setup.py", "setup_start", _MUSIC): _SETUP,
    ("routers/handoff.py", "handoff_start", _MUSIC): _SETUP,
    ("music_oauth.py", "_run_flow", _MUSIC): _SETUP, ("music_oauth.py", "_run_setup_flow", _MUSIC): _SETUP,
    ("ytmusic_signin.py", "_finish_connect", _MUSIC): _SETUP, ("ytmusic_signin.py", "refresh_now", _MUSIC): _SETUP,
    # finding the discovery playlist by name: the weekly job's, and "play my discovery playlist"'s first step
    ("music_discovery.py", "get_discovery_playlist", _MUSIC): _READ,
    **{("music_discovery.py", fn, _MUSIC): _AUTO for fn in ("replace_discovery_playlist", "resolve_recommendation_tracks", "taste_seed")},
    ("music_history.py", "observe_once", _MUSIC): _AUTO,
}


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    """The entry points are READ FROM THE CODE (``tests/door_callers.py``), not counted by name prefix: every function of the
    service that names ``home_control.send``, and every function that names a function of ``music_service`` that reaches Music
    Assistant (``_ma_response`` or anything built on it). Each is tied to named scenarios, or listed with its reason: a read,
    the provider set-up or automatic work. Every command has a scenario: nothing that SENDS is exempt (the five that had none,
    group, queue move, queue save, the spoken favourite and discovery playback, each have one now). A new caller of either door
    fails here until it is given one or the other.
    Controls run: ``async def sneak(): await music_service.control("pause")`` added to ``routers/music.py`` fails this naming
    it; ``async def sneak(): await home_control.send("u", home_control.DEVICE, {})`` added to ``routers/ha_control.py`` too."""
    import door_callers

    assert set(ON_MAIN) == set(scenarios.scenarios())
    root = Path(__file__).resolve().parents[1]
    door_callers.check_coverage(door_callers.entry_points(root, _HOME, {"send"}), HOME_RECORDED, {}, set(ON_MAIN))
    reaches = door_callers.door_functions((root / "music_service.py").read_text("utf-8"), {"_ma_response"})
    assert len(reaches) >= 40 and {"control", "play_media", "search_and_play", "transfer", "queue_clear"} <= reaches
    callers = {key: [_MUSIC] for key in door_callers.entry_points(root, _MUSIC, reaches)}
    door_callers.check_coverage(callers, MUSIC_RECORDED, MUSIC_NOT_RECORDED, set(ON_MAIN))
    assert set(MUSIC_NOT_RECORDED.values()) == {_READ, _SETUP, _AUTO}                # no exemption for a command that sends


def _tops(path: Path, cache: dict) -> list:
    if path not in cache:
        import ast

        import door_callers

        tree = ast.parse(path.read_text("utf-8"))
        cache[path] = [(node.lineno, node.end_lineno, name) for name, node in door_callers._top_functions(tree)]
    return cache[path]


def _executed(names, calls=None) -> dict:
    """``{scenario: {(module, function, door) that RAN and REACHED THE SENDER}}``: every function of a door is wrapped
    (``home_control.send``; every function of ``music_service`` that reaches Music Assistant); a call from outside the door
    modules notes its caller's ``(module, top-level function)``, and counts only if, while it ran, the fake network was sent
    a command of that door (a POST to the bridge; anything to Music Assistant's ``/api``, reads included, as its record)."""
    import inspect
    import sys
    from urllib.parse import urlsplit

    import door_callers
    import home_control
    import music_service

    root = Path(__file__).resolve().parents[1]
    door_files = {str((root / f).resolve()): door for f, door in (("music_service.py", _MUSIC), ("home_control.py", "send"))}
    sends = {_MUSIC: lambda m, path: path == "/api", "send": lambda m, path: m == "POST" and path != "/api"}
    traffic: list = []
    seen: set = set()
    cache: dict = {}
    real_answer = scenarios.World.answer

    def answer(self, method, url, *a, **k):
        traffic.append((method, urlsplit(str(url)).path))
        return real_answer(self, method, url, *a, **k)

    def spy(original, door):
        def call(*a, **k):
            caller = sys._getframe(1)
            path = Path(caller.f_code.co_filename).resolve()
            result = original(*a, **k)
            if str(path) in door_files or not inspect.isawaitable(result) or root not in path.parents:
                return result
            fn = next((name for first, last, name in _tops(path, cache) if first <= caller.f_lineno <= last), "<module>")
            key = (path.relative_to(root).as_posix(), fn, door)

            async def tracked():
                before = len(traffic)
                try:
                    return await result
                finally:
                    if any(sends[door](m, p) for m, p in traffic[before:]):
                        seen.add(key)
            return tracked()
        return call

    mp = pytest.MonkeyPatch()
    out = {}
    try:
        mp.setattr(scenarios.World, "answer", answer)
        mp.setattr(home_control, "send", spy(home_control.send, "send"))
        for fn in door_callers.door_functions((root / "music_service.py").read_text("utf-8"), {"_ma_response"}):
            if "." not in fn:
                mp.setattr(music_service, fn, spy(getattr(music_service, fn), _MUSIC))
        for name in names:
            seen.clear()
            scenarios.run_one(name, (calls or {}).get(name))
            out[name] = set(seen)
    finally:
        mp.undo()
    return out


def test_every_listed_scenario_runs_the_entry_point_it_is_listed_for():
    """The coverage test above checks NAMES: that each entry point has scenarios. This checks that each scenario RUNS it and
    that it REACHED THE SENDER (``_executed``). A scenario whose record on main sends to that door (a POST to the bridge for
    home; anything to Music Assistant for music) must have run exactly the entry points it is listed for; one whose main record
    sends nothing there (a refusal, nothing to do) and every scenario listed nowhere must have run none; and every listed entry
    point is run by a scenario of its own.
    Controls run: the spoken music scenarios put back to calling ``music_service.resolve_music`` directly (as before this check)
    fails here (``resolve_skybridge_request`` never ran) while the coverage test still passes; ``music_spoken_pause`` moved from
    the Skybridge entry to ``_music_assistant_control``'s in ``MUSIC_RECORDED`` fails here too. Planted below: the old direct call
    is not seen as running Skybridge's entry point."""
    listed = {**HOME_RECORDED, **MUSIC_RECORDED}
    seen = _executed(scenarios.scenarios())

    def sends(name, door):
        return any(e[0] == "http" and (e[2] == "/api" if door == _MUSIC else (e[1] == "POST" and e[2] != "/api")) for e in ON_MAIN[name])

    want = {name: set() for name in seen}
    for key, names in listed.items():
        for name in names:
            if sends(name, key[2]):
                want[name].add(key)
    reads = {key for key, why in MUSIC_NOT_RECORDED.items() if why == _READ}       # a read may run anywhere (it sends no command:
    seen = {name: got - reads for name, got in seen.items()}                        # ``test_every_read_exemption_...`` below)
    wrong = {name: {"ran": sorted(seen[name]), "listed": sorted(want[name])} for name in seen if seen[name] != want[name]}
    assert not wrong, "\n".join(f"{name}: {json.dumps(w)}" for name, w in sorted(wrong.items()))
    assert set().union(*seen.values()) == set(listed)                 # every listed entry point is run by a scenario of its own

    async def direct(w):                                               # planted: the spoken scenario as it was
        import music_service
        import skybridge_service as sky

        return await music_service.resolve_music(sky.SkybridgeIntent("music", "pause"), scenarios.ANN)
    planted = _executed(["music_spoken_pause"], {"music_spoken_pause": direct})["music_spoken_pause"]
    assert planted and ("skybridge_service.py", "resolve_skybridge_request", _MUSIC) not in planted


#: the only commands a read may send Music Assistant
_READ_COMMANDS = {"players/all", "player_queues/all", "music/search", "config/providers", "music/recently_played_items",
                  "music/recommendations", "music/playlists/library_items", "music/playlists/playlist_tracks"}
_PLUMBING = {"_ma_response", "_ma_headers", "_ma_info"}             # build the address and headers: their f-strings name no command


def test_every_read_exemption_sends_music_assistant_only_reads():
    """``MUSIC_NOT_RECORDED`` exempts the reads by the CALLER; the coverage test keys music by module only, so an exempt read
    that began to call ``control`` or ``play_media`` would pass it unseen. Here every function of ``music_service`` an exempt
    read names is followed through ``music_service`` (any depth): every command it can send is a literal read command, and no
    function on the way builds a command from parts. Control run: ``if False: await control("pause")`` planted in
    ``music_service.get_players`` fails this (``music_screen``/``set_preferred_player`` and every ``get_players`` reader);
    ``setup_form`` labelled a read again fails it (its form opens a provider set-up flow)."""
    import ast
    import re

    import door_callers

    root = Path(__file__).resolve().parents[1]
    source = (root / "music_service.py").read_text("utf-8")
    fns = dict(door_callers._top_functions(ast.parse(source)))
    reaches = door_callers.door_functions(source, {"_ma_response"})
    callers = door_callers.entry_points(root, _MUSIC, reaches)

    def closure(name):
        found, todo = set(), [name]
        while todo:
            n = todo.pop()
            if n in found or n not in fns:
                continue
            found.add(n)
            named = {x.id for x in ast.walk(fns[n]) if isinstance(x, ast.Name)} | {x.attr for x in ast.walk(fns[n]) if isinstance(x, ast.Attribute)}
            todo += [m for m in fns if m in named]
        return found

    def commands_in(node) -> set:
        return {c.value for c in ast.walk(node)
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and re.fullmatch(r"[a-z_]+(/[a-z_]+)+", c.value)
                and not c.value.startswith("application/")}                  # a content type, not a command

    reads = [key for key, why in MUSIC_NOT_RECORDED.items() if why == _READ]
    assert len(reads) >= 14
    for key in reads:
        caller = dict(door_callers._top_functions(ast.parse((root / key[0]).read_text("utf-8"))))[key[1]]
        own = commands_in(caller) if {"_ma", "_ma_ok", "_ma_response"} & set(callers[key[:2]]) else set()
        for fn in callers[key[:2]]:
            path = closure(fn)
            commands = own | {c for n in path for c in commands_in(fns[n])}
            built = sorted(n for n in path - _PLUMBING if any(isinstance(x, ast.JoinedStr) for x in ast.walk(fns[n])))
            assert commands and commands <= _READ_COMMANDS and not built, (key, fn, sorted(commands - _READ_COMMANDS), built)


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_music_and_home_entry_point_sends_exactly_what_main_sent(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_the_calls_not_only_the_replies(here):
    """The comparison has something to bite on: a command's record holds the call with its body, timeout, the service it went to
    and its header names; a refusal has its words; music through the house goes to the bridge, not to Music Assistant's command
    list; a spoken play is journalled for the user Skybridge was handed (an adult by her id, a guest as the guest), a spoken
    switch leaves its device receipt for that user, and a spoken answer is recorded whole (its cards and context).
    Controls run (each broke the code, saw the main-record test fail, restored): ``resolve_skybridge_request`` handing
    ``user_id=""`` to ``resolve_music`` -> ``music_spoken_play``; ``record_device_receipt`` given ``"voice"`` as its source ->
    ``home_said_turn_on``; the bridge address in ``routers/ha_control.py`` hard-coded to another host ->
    ``home_screen_turn_on``; ``headers=_ma_headers()`` dropped from ``music_service._ma_response`` -> ``music_screen_seek``;
    ``_attach_skybridge_context`` leaving the cards out of the context -> ``music_spoken_pause``; ``set_preferred_player_id`` dropped from the
    speaker picker -> ``music_screen_set_preferred_player``."""
    sent = [e for e in here["home_screen_turn_on"] if e[0] == "http" and e[1] == "POST"]
    assert sent == [["http", "POST", "/devices/control", {"entity_id": "light.kitchen_lamp", "action": "turn_on", "data": {"brightness_pct": 80}},
                     sent[0][4], "ha-bridge", sent[0][6]]] and isinstance(sent[0][4], (int, float))
    assert {e[5] for e in here["music_screen_seek"] if e[0] == "http"} == {"music-assistant"}
    assert all("Content-Type" in e[6] for e in here["music_screen_seek"] if e[0] == "http")
    assert [e[1] for e in here["music_spoken_play"] if e[0] == "journal"] == [scenarios.ANN]
    assert [e[1] for e in here["music_spoken_play_guest"] if e[0] == "journal"] == ["guest"]
    assert [e[1:] for e in here["home_said_turn_on"] if e[0] == "receipt"] == [[scenarios.ANN, "device", "light.kitchen_lamp", "skybridge"]]
    said = here["music_spoken_pause"][-1]
    assert said[0] == "returned" and said[1]["handled"] is True and said[1]["cards"] and "skybridge_context" in said[1]
    assert [e for e in here["music_screen_set_preferred_player"] if e[0] == "preferred speaker"] == [["preferred speaker", "ply0002"]]
    assert not [e for e in here["music_screen_set_preferred_unknown"] if e[0] == "preferred speaker"]
    scene = [e for e in here["home_spoken_scene"] if e[0] == "http" and e[1] == "POST"]
    assert [e[2:4] for e in scene] == [["/scenes/activate", {"scene_id": "scene.movie_time"}]]
    seek = [e[3] for e in here["music_screen_seek"] if e[0] == "http" and (e[3] or {}).get("command") == "player_queues/seek"]
    assert seek == [{"command": "player_queues/seek", "args": {"queue_id": "ply0001", "position": 42}}]
    assert here["home_screen_bridge_offline"][-1] == ["raised", 503, "HA bridge offline"]
    assert [e[2] for e in here["music_house_pause"] if e[0] == "http" and e[1] == "POST" and e[2] != "/api"] == ["/devices/control"]


_REFUSAL = "Unlocking or opening that needs a grown-up I can recognise, so I haven't done it."
_UNLOCK = {"entity_id": "lock.front_door", "action": "unlock", "data": {}}
_OPEN = {"entity_id": "cover.garage_door", "action": "open_cover", "data": {}}
_COVER_ON = {"entity_id": "cover.garage_door", "action": "turn_on"}
#: the scenario an actor main ALLOWS (the one command it sends) -> the same command by actors main REFUSES (how each is told)
_OPENING = {
    "home_screen_unlock": (_UNLOCK, {name: ["raised", 403, _REFUSAL] for name in (
        "home_screen_unlock_guest", "home_screen_unlock_child", "home_screen_unlock_bare_panel",
        "home_screen_unlock_panel_child_voice_session")}),
    "home_screen_unlock_panel_adult_voice_session": (_UNLOCK, {"home_screen_unlock_bare_panel": ["raised", 403, _REFUSAL]}),
    "home_screen_open_a_cover": (_OPEN, {"home_screen_open_a_cover_bare_panel": ["raised", 403, _REFUSAL]}),
    "home_tool_unlock": (_UNLOCK, {name: ["returned", {"error": _REFUSAL, "status": "refused"}] for name in (
        "home_tool_unlock_bare_panel", "home_tool_unlock_child", "home_tool_unlock_no_turn")}),
    "home_spoken_open_a_cover": (_COVER_ON, {name: ["returned", {"action": "turn_on", "spoken_summary": _REFUSAL}] for name in (
        "home_spoken_open_a_cover_guest", "home_spoken_open_a_cover_no_turn")}),
    "home_helper_open_a_cover": (_COVER_ON, {"home_helper_open_a_cover_bare_panel": ["returned", False]}),
    # through Skybridge's entry point: its whole answer is recorded, so the refusal is pinned by its words in that answer
    "home_said_open_a_cover": (_COVER_ON, {"home_said_open_a_cover_guest": _REFUSAL}),
}
#: locking, closing and an ordinary light stay open to anyone who may control the house: sent
_ANYONE = {"home_screen_lock_bare_panel": {"entity_id": "lock.front_door", "action": "lock", "data": {}},
           "home_screen_close_a_cover_guest": {"entity_id": "cover.garage_door", "action": "close_cover", "data": {}},
           "home_tool_lock_guest": {"entity_id": "lock.front_door", "action": "lock", "data": {}},
           "home_helper_lamp_guest": {"entity_id": "light.kitchen_lamp", "action": "turn_on"}}


def _commands(record) -> list:
    return [e[2:4] for e in record if e[0] == "http" and e[1] == "POST" and e[2] != "/api"]


@pytest.mark.parametrize("allowed", sorted(_OPENING))
def test_unlocking_or_opening_is_recorded_both_ways(here, allowed):
    """Main (#2715) sends a command that unlocks or opens only for an adult member, or the panel while an adult's verified voice
    session is open on it. Each such command is pinned BOTH ways, on main (the fixture) AND here: by an actor main allows it is
    SENT (exactly one command, this body), by each actor main refuses NOTHING is sent and the refusal is the recorded one. A
    fixture holding only refusals would pin no send (as the pre-#2715 record, replayed on the new main, would have); one holding
    only sends would pin no refusal.
    Controls run (each broke the code, saw the named scenario fail here and against the main record, restored): the check
    dropped from ``routers/ha_control.ha_control`` -> ``home_screen_unlock_guest``; from ``zoe_agent._ha_control`` ->
    ``home_tool_unlock_bare_panel``; from ``smart_home_service.set_device`` -> ``home_helper_open_a_cover_bare_panel``; the
    refusal loop dropped from ``resolve_smart_home`` -> ``home_spoken_open_a_cover_guest``; ``set_device`` sending to the scene
    path -> ``home_helper_open_a_cover``; the voice session ignored in ``home_opening.may_open`` ->
    ``home_screen_unlock_panel_adult_voice_session``; ``set_device`` refusing every caller -> ``home_helper_lamp_guest``."""
    body, refused = _OPENING[allowed]
    for record in (ON_MAIN, here):
        assert _commands(record[allowed]) == [["/devices/control", body]], allowed
        assert record[allowed][-1][0] == "returned" and _REFUSAL not in json.dumps(record[allowed][-1])
        for name, told in refused.items():
            assert _commands(record[name]) == [], name
            if isinstance(told, str):           # a whole spoken answer: it is a reply, and it says the refusal
                assert record[name][-1][0] == "returned" and record[name][-1][1]["spoken_summary"] == told, name
            else:
                assert record[name][-1] == told, name


def test_every_opening_scenario_is_in_a_pair_and_closing_is_sent_for_anyone(here):
    """Nothing that unlocks or opens sits in the fixture outside a pair, and the commands that only lock or close (and a lamp)
    are sent for a guest and for the bare panel. Control run: ``lock`` removed from ``home_opening.CLOSING`` ->
    ``home_screen_lock_bare_panel`` is refused and this fails."""
    import home_opening

    assert home_opening.REFUSAL == _REFUSAL
    paired = set(_OPENING) | {name for _, refused in _OPENING.values() for name in refused}
    for name, record in ON_MAIN.items():
        opens = any(home_opening.opens(body.get("entity_id"), body.get("action"), body.get("data")) for _, body in _commands(record))
        if opens or _REFUSAL in json.dumps(record):
            assert name in paired, name
    assert len(paired) == 19
    for record in (ON_MAIN, here):
        for name, body in _ANYONE.items():
            assert _commands(record[name]) == [["/devices/control", body]], name


#: scenario -> the one changing command it must send to Music Assistant, with its arguments (on main, so in the fixture too)
_MUSIC_COMMANDS = {
    "music_screen_unfavorite": {"command": "music/favorites/remove_item", "args": {"media_type": "track", "library_item_id": 2}},
    "music_screen_group": {"command": "players/cmd/set_members",
                           "args": {"target_player": "ply0001", "player_ids_to_add": ["ply0002"], "player_ids_to_remove": None}},
    "music_screen_queue_move": {"command": "player_queues/move_item", "args": {"queue_id": "ply0001", "queue_item_id": "qi1", "pos_shift": 2}},
    "music_screen_queue_save": {"command": "player_queues/save_as_playlist", "args": {"queue_id": "ply0001", "name": "Womble mix"}},
    "music_intent_favorite": {"command": "music/favorites/add_item", "args": {"item": "library://track/1"}},
    "music_spoken_discovery": {"command": "player_queues/play_media", "args": {"queue_id": "ply0001", "media": "library://playlist/7",
                                                                                "option": "replace", "radio_mode": False}},
}


@pytest.mark.parametrize("name", sorted(_MUSIC_COMMANDS))
def test_each_music_command_scenario_actually_sends_its_command(here, name):
    """A scenario that only READS would pin nothing about the command (the unfavourite scenario once recorded only the lookup and
    ``{"ok": false}``): each of these must send its changing command, with these arguments, in the fixture AND here.
    Controls run (each changed one outbound command or argument, saw this and the main-record test fail, restored):
    ``library_item_id`` -> ``item_id`` in ``favorite_remove``; ``player_ids_to_add`` dropped in ``group_players``; ``pos_shift`` sign
    flipped in ``queue_move``; ``name`` -> ``title`` in ``queue_save_playlist``; ``favorite_add``'s ``item=`` given the title in
    ``favorite_now_playing``; discovery's ``play_media`` given the household playlist name instead of its uri."""
    for record in (ON_MAIN[name], here[name]):
        sent = [e[3] for e in record if e[0] == "http" and e[2] == "/api" and (e[3] or {}).get("command") == _MUSIC_COMMANDS[name]["command"]]
        assert sent == [_MUSIC_COMMANDS[name]], (name, sent)
