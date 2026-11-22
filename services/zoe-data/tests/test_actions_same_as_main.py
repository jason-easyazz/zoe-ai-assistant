"""Music and home control each have ONE way out of zoe-data, and nothing any entry point SENDS has changed against ``main``.

Neither area stores anything in Zoe: what an entry point does is the calls it makes to Music Assistant and to the Home
Assistant bridge. ``tests/fixtures/actions_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and
action, every outbound call (method, path, body, timeout; reads too) to a FAKE Music Assistant and a FAKE bridge, and the result
or refusal. It was made by running ``tests/actions_scenarios.py`` against an export of that commit, never by this branch's own
code. The same scenarios run here and must give the same record. It is also the "before the door" record the kernel-door tests
compare flag-off behaviour with.

Covered. MUSIC: the screen's control (pause, next, volume, an unknown action), seek, transfer, play, play a chosen result, the
queue routes, favourite and unfavourite, add to a playlist, "don't stop", ungroup; the spoken play / pause / move; the intent
executor's pause, volume down, volume set, play and a delayed request; music through a house media player. HOME: the panel's
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
_GAP = "A COMMAND WITH NO SCENARIO YET (found in review by reading the code; listed so it is seen, to be given one)"
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
    (_SCREEN, "music_group", _MUSIC): _GAP, (_SCREEN, "music_queue_move", _MUSIC): _GAP, (_SCREEN, "music_queue_save", _MUSIC): _GAP,
    (_ROUTER, "_execute_music_intent", _MUSIC): _GAP,                    # the spoken "I like this song" (favorite_now_playing)
    ("music_discovery.py", "play_discovery", _MUSIC): _GAP,
}


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    """The entry points are READ FROM THE CODE (``tests/door_callers.py``), not counted by name prefix: every function of the
    service that names ``home_control.send``, and every function that names a function of ``music_service`` that reaches Music
    Assistant (``_ma_response`` or anything built on it). Each is tied to named scenarios, or listed with its reason: a read,
    the provider set-up, automatic work, or (five, marked ``_GAP``) a command that has no scenario yet. A new caller of either
    door fails here until it is given one or the other.
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
    assert sum(why == _GAP for why in MUSIC_NOT_RECORDED.values()) == 5              # the known gaps cannot quietly grow


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
