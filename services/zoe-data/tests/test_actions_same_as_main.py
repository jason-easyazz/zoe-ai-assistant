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


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    assert set(ON_MAIN) == set(scenarios.scenarios())
    for prefix in ("music_screen_", "music_spoken_", "music_intent_", "music_house_", "home_screen_", "home_spoken_", "home_tool_"):
        assert sum(name.startswith(prefix) for name in ON_MAIN) >= 2, prefix


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
