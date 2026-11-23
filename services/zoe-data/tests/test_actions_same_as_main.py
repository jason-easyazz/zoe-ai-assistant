"""Music and home control each have ONE way out of zoe-data, and nothing any entry point SENDS has changed against ``main``.

Neither area stores anything in Zoe: what an entry point does is the calls it makes to Music Assistant and to the Home
Assistant bridge. ``tests/fixtures/actions_as_on_main.json`` is the record of ``main`` (b2bbe84ca): for each entry point and
action, every outbound call (method, path, body, timeout; reads too) to a FAKE Music Assistant and a FAKE bridge, and the result
or refusal. It was made by running ``tests/actions_scenarios.py`` against an export of that commit, never by this branch's own
code. The same scenarios run here and must give the same record. It is also the "before the door" record the kernel-door tests
compare flag-off behaviour with.

Covered. MUSIC: the screen's control (pause, next, volume, an unknown action), seek, transfer, play, play a chosen result, the
queue routes (clear, remove, play an index, move, save as a playlist), favourite and unfavourite (the removal itself: the uri
resolves to its library row), add to a playlist, "don't stop", group and ungroup; the spoken play / pause / move and "play my
discovery playlist"; the intent executor's pause, volume down, volume set, play, a delayed request and "I like this song"; music
through a house media player. HOME: the panel's
control route (on with brightness, off, toggle, a scene, open a cover, unlock, pause a media player, no entity, the bridge
refusing, the bridge offline); the spoken on / off / brightness / scene / no match; the brain's tool (on, unlock, offline).

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
    ("routers/ha_control.py", "_bridge_post", "send"): ("home_screen_turn_on", "home_screen_scene", "home_screen_bridge_offline"),
    ("smart_home_service.py", "_ha_post", "send"): ("home_spoken_turn_on", "home_spoken_scene"),
    ("zoe_agent.py", "_ha_control", "send"): ("home_tool_turn_on", "home_tool_bridge_offline"),
    (_ROUTER, "_post_music_ha_control", "send"): ("music_house_pause", "music_house_volume_set"),
}
#: MUSIC: every function of the service that names a function of ``music_service`` that reaches Music Assistant -> its scenarios
MUSIC_RECORDED = {
    (_SCREEN, "music_control", _MUSIC): ("music_screen_pause", "music_screen_next", "music_screen_volume_set", "music_screen_unknown_action"),
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
    (_ROUTER, "_execute_music_intent_asked", _MUSIC): ("music_intent_favorite",),     # the body the kernel door runs (this branch)
    ("music_discovery.py", "play_discovery", _MUSIC): ("music_spoken_discovery",),
    (_SCREEN, "music_playlist_add", _MUSIC): ("music_screen_playlist_add",),
    (_SCREEN, "music_dont_stop", _MUSIC): ("music_screen_dont_stop",),
    (_SCREEN, "music_ungroup", _MUSIC): ("music_screen_ungroup",),
    ("skybridge_service.py", "resolve_skybridge_request", _MUSIC): ("music_spoken_play", "music_spoken_pause", "music_spoken_transfer"),
    (_ROUTER, "_music_assistant_control", _MUSIC): ("music_intent_pause", "music_intent_volume_down", "music_intent_volume_set"),
    (_ROUTER, "_music_assistant_play", _MUSIC): ("music_intent_play", "music_intent_delayed"),
    (_ROUTER, "_music_assistant_active_player", _MUSIC): ("music_intent_pause",),
}
_READ = "a read: it asks Music Assistant what is there or playing and sends nothing that changes it"
_SETUP = "provider sign-in / set-up: outside the door, as the record says (a config flow, not a command to a speaker)"
_AUTO = "automatic (the discovery playlist, the listening history): nobody is acting"
MUSIC_NOT_RECORDED = {
    **{(_SCREEN, fn, _MUSIC): _READ for fn in ("_queue_item_art", "music_groups", "music_now_playing", "music_playlist_tracks",
                                               "music_playlists", "music_recently_played", "music_recommendations", "music_search",
                                               "set_preferred_player")},
    (_ROUTER, "_music_assistant_now_playing", _MUSIC): _READ, (_ROUTER, "_execute_music_setup", _MUSIC): _READ,
    ("routers/panel_config.py", "_known_player_ids", _MUSIC): _READ, ("routers/voice_tts.py", "_media_playing", _MUSIC): _READ,
    ("routers/music_setup.py", "setup_catalogue", _MUSIC): _READ, ("routers/music_setup.py", "setup_form", _MUSIC): _READ,
    ("routers/music_setup.py", "setup_save", _MUSIC): _SETUP, ("routers/music_setup.py", "setup_start", _MUSIC): _SETUP,
    ("routers/handoff.py", "handoff_start", _MUSIC): _SETUP,
    ("music_oauth.py", "_run_flow", _MUSIC): _SETUP, ("music_oauth.py", "_run_setup_flow", _MUSIC): _SETUP,
    ("ytmusic_signin.py", "_finish_connect", _MUSIC): _SETUP, ("ytmusic_signin.py", "refresh_now", _MUSIC): _SETUP,
    **{("music_discovery.py", fn, _MUSIC): _AUTO for fn in ("get_discovery_playlist", "replace_discovery_playlist",
                                                           "resolve_recommendation_tracks", "taste_seed")},
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


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_music_and_home_entry_point_sends_exactly_what_main_sent(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_the_calls_not_only_the_replies(here):
    """The comparison has something to bite on: a command's record holds the call with its body and timeout; a refusal has its
    words; music through the house goes to the bridge, not to Music Assistant's command list."""
    sent = [e for e in here["home_screen_turn_on"] if e[0] == "http" and e[1] == "POST"]
    assert sent == [["http", "POST", "/devices/control", {"entity_id": "light.kitchen_lamp", "action": "turn_on", "data": {"brightness_pct": 80}}, sent[0][4]]]
    scene = [e for e in here["home_spoken_scene"] if e[0] == "http" and e[1] == "POST"]
    assert [e[2:4] for e in scene] == [["/scenes/activate", {"scene_id": "scene.movie_time"}]]
    seek = [e[3] for e in here["music_screen_seek"] if e[0] == "http" and (e[3] or {}).get("command") == "player_queues/seek"]
    assert seek == [{"command": "player_queues/seek", "args": {"queue_id": "ply0001", "position": 42}}]
    assert here["home_screen_bridge_offline"][-1] == ["raised", 503, "HA bridge offline"]
    assert [e[2] for e in here["music_house_pause"] if e[0] == "http" and e[1] == "POST" and e[2] != "/api"] == ["/devices/control"]


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
