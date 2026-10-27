"""Polite / modal lead-ins must never leak into the smart-home device query.

Live 2026-10-27: "you turn the light on" and "can you turn the light on?" were routed
correctly but answered "I couldn't find 'you light' among your devices", because the
bare-toggle matcher kept the filler words. Generic phrases only (public repo).
"""
from __future__ import annotations

import pytest

import skybridge_service as sky
import smart_home_service as shs

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("text,action", [
    ("you turn the light on", "turn_on"),
    ("can you turn the light on?", "turn_on"),
    ("Could you please turn off the lights", "turn_off"),
    ("turn the lamp on for me", "turn_on"),
    ("would you turn the light off please", "turn_off"),
    ("hey zoe, will you turn on the light", "turn_on"),
    ("go ahead and turn the light on", "turn_on"),
    ("just turn the light off", "turn_off"),
])
def test_polite_frame_is_stripped_from_the_query(text, action):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and got.domain == "smart_home" and got.action == action
    q = set(got.query.split())
    assert q <= {"light", "lights", "lamp"}, got.query
    assert q, got.query


@pytest.mark.parametrize("text,pct", [
    ("could you dim the lamp to 40%", 40),
    ("can you please set the lights to 80 percent for me", 80),
])
def test_dim_verb_strips_the_frame(text, pct):
    got = sky.classify_skybridge_intent(text)
    assert got.action == "set_brightness" and got.duration_seconds == pct
    assert not set(got.query.split()) & {"you", "can", "could", "please", "me", "for"}


@pytest.mark.parametrize("text,query", [
    ("turn on the can light", "can light"),
    ("turn on the zoe lamp", "zoe lamp"),
    ("switch on the go lamp", "go lamp"),
    ("turn the just lamp off", "just lamp"),
    ("can you turn on the can light please", "can light"),
])
def test_filler_words_inside_a_device_name_survive(text, query):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and got.query == query, got


def test_guard_a_named_device_still_resolves():
    got = sky.classify_skybridge_intent("can you turn on the kitchen lamp?")
    assert got.query == "kitchen lamp"


def test_guard_non_commands_are_left_alone():
    assert sky.classify_skybridge_intent("can you hear me") is None


def _dev(eid, name):
    return shs._device_from_light({"entity_id": eid, "state": "off", "attributes": {"friendly_name": name}})


A, B, C = _dev("light.one", "Alpha Lamp"), _dev("light.two", "Beta Lamp"), _dev("light.three", "Gamma Lamp")


def test_bare_light_one_room_match_resolves_to_it():
    assert shs._select_targets([A, B, C], "light", "turn_on", {"light.two"}) == ([B], False)


def test_bare_light_two_room_matches_asks_among_those_two():
    targets, amb = shs._select_targets([A, B, C], "light", "turn_on", {"light.one", "light.two"})
    assert amb and {d["entity_id"] for d in targets} == {"light.one", "light.two"}
    q = shs._which_one(targets)
    assert q == "Which one \u2014 Alpha Lamp or Beta Lamp?"


def test_bare_light_no_room_match_asks_which():
    targets, amb = shs._select_targets([A, B], "light", "turn_on", None)
    assert amb and len(targets) == 2
    assert shs._which_one(targets).startswith("Which one")


def test_filler_only_query_never_reports_not_found(monkeypatch):
    import asyncio

    async def ld():
        return [A, B]

    async def ls():
        return []

    monkeypatch.setattr(shs, "list_devices", ld)
    monkeypatch.setattr(shs, "list_scenes", ls)
    monkeypatch.setattr(shs, "_turn_refused", lambda _w: False)
    intent = sky.classify_skybridge_intent("you turn the light on")
    out = asyncio.run(shs.resolve_smart_home(intent, None))
    assert "couldn't find" not in out["spoken_summary"]
    assert out["spoken_summary"].startswith("Which one")
