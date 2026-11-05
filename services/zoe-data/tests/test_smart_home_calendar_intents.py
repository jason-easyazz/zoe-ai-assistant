"""Capability-gap fixes 2026-11-05: simple smart-home on/off commands and calendar READ questions that matched no intent.

Every wording here is a neutral paraphrase (public repo). Each positive group has a negative control: the near-miss phrasings
("turn the page", "what's on tv", a complaint about the calendar) must NOT be claimed.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import command_shapes
import expert_dispatch
import intent_router
import skybridge_service as sky
import smart_home_service as shs

pytestmark = pytest.mark.ci_safe


# ── calendar reads ────────────────────────────────────────────────────────────────────────────────────────────────────
CALENDAR_READS = [
    ("what's on my calendar today", "today"),
    ("do I have anything on my calendar", ""),
    ("is there anything on my calendar", ""),
    ("have I got anything on today", "today"),
    ("what do I have on tomorrow", "tomorrow"),
    ("anything on tomorrow", "tomorrow"),
    ("what's my week look like", "this week"),
    ("what am I doing on friday", "friday"),
    ("are we free on saturday", "saturday"),
    ("am I free this weekend", "this weekend"),
    ("what's on the calendar for next week", "next week"),
    ("can you see my calendar", ""),
    ("could you check my schedule please", ""),
    ("what appointments do I have", ""),
    ("do I have any appointments today", "today"),
    ("what's coming up this week", "this week"),
]

NOT_CALENDAR_READS = [
    "what's on tv",                                    # media, not the calendar
    "what's on tv tomorrow",
    "what's on netflix tonight",
    "what's on the radio today",
    "what's on tonight",                               # as likely the TV as the calendar: left to the brain
    "what's on",                                       # no date, no noun
    "that's not my calendar",                          # complaints / remarks
    "why couldn't you see my calendar earlier",
    "how come you couldn't see my calendar before",
    "I can't see my calendar",
    "what is a calendar",                              # a definition
    "I have a meeting tomorrow",                       # a statement
    "add a dentist visit to my calendar tomorrow",     # a WRITE
    "turn the page",
    "do I have anything in the fridge",
    "what's the weather tomorrow",
    "what's on my mind",
    "what time is my dentist appointment on friday",   # an event-TIME question: brain + recall floor, not a calendar dump
]


@pytest.mark.parametrize("text,qualifier", CALENDAR_READS)
def test_calendar_read_ask_positive(text, qualifier):
    assert command_shapes.calendar_read_ask(text) == qualifier


@pytest.mark.parametrize("text", NOT_CALENDAR_READS)
def test_calendar_read_ask_negative_control(text):
    assert command_shapes.calendar_read_ask(text) is None


@pytest.mark.parametrize("text,qualifier", CALENDAR_READS)
def test_intent_router_reads_the_calendar(text, qualifier):
    got = intent_router.detect_intent(text, log_miss=False)
    assert got is not None and got.name == "calendar_show", (text, got)
    if qualifier:
        assert (got.slots.get("qualifier") or "") in (qualifier, "") or qualifier in got.slots.get("qualifier", "")


@pytest.mark.parametrize("text", NOT_CALENDAR_READS)
def test_intent_router_does_not_read_the_calendar_for_near_misses(text):
    got = intent_router.detect_intent(text, log_miss=False)
    assert got is None or got.name != "calendar_show", (text, got)


@pytest.mark.parametrize("text", [t for t in NOT_CALENDAR_READS if "calendar" in t or "tv" in t or "netflix" in t or "radio" in t])
def test_skybridge_loose_keyword_no_longer_reads_complaints_or_media(text):
    got = sky.classify_skybridge_intent(text)
    assert got is None or got.domain != "calendar" or text.startswith("add "), (text, got)


@pytest.mark.parametrize("text", [t for t, _ in CALENDAR_READS])
def test_skybridge_reads_the_calendar_questions_the_router_reads(text):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and (got.domain, got.action) == ("calendar", "show"), (text, got)


def test_skybridge_weekend_is_saturday_and_sunday():
    got = sky._calendar_weekend_range(" what's on this weekend ", date(2026, 11, 5))   # a Thursday
    assert got == ("this weekend", date(2026, 11, 7), date(2026, 11, 8))
    assert sky._calendar_weekend_range(" what's on this weekend ", date(2026, 11, 8))[1:] == (date(2026, 11, 8), date(2026, 11, 8))
    assert sky._calendar_weekend_range(" what's on tomorrow ", date(2026, 11, 5)) is None     # negative control


def test_calendar_show_scope_covers_the_qualifiers_the_asks_produce():
    thu = date(2026, 11, 5)
    assert intent_router._calendar_show_scope("", thu)[2] == "today"
    assert intent_router._calendar_show_scope("tomorrow", thu) == (date(2026, 11, 6), date(2026, 11, 6), "tomorrow")
    assert intent_router._calendar_show_scope("next week", thu)[:2] == (date(2026, 11, 9), date(2026, 11, 15))
    assert intent_router._calendar_show_scope("this weekend", thu)[:2] == (date(2026, 11, 7), date(2026, 11, 8))
    assert intent_router._calendar_show_scope("friday", thu)[:2] == (date(2026, 11, 6), date(2026, 11, 6))
    assert intent_router._calendar_show_scope("thursday", thu)[:2] == (date(2026, 11, 12), date(2026, 11, 12))   # never today
    # negative control: an unknown qualifier keeps the old behaviour (the next seven days)
    assert intent_router._calendar_show_scope("whenever", thu)[2] == "in the next week"


def test_expert_calendar_plan_reads_what_it_used_to_miss():
    for text, qualifier in CALENDAR_READS:
        plan = expert_dispatch._plan("calendar", text)
        assert plan is not None and plan[0] == "calendar_show" and plan[2] == "read", (text, plan)
    # negative control: a complaint about the calendar still has no read plan (and no bogus create)
    assert expert_dispatch._plan("calendar", "that's not my calendar") is None


def test_expert_calendar_write_with_an_unusual_create_verb_gets_a_write_plan():
    plan = expert_dispatch._plan("calendar", "make an appointment at the dentist on friday at 3")
    assert plan is not None and plan[0] == "calendar_create" and plan[2] == "write"
    # the write plan still defers on the chat lane (write_ok=False) and still needs the opt-in flag: kind is "write", not "read"
    assert plan[2] != "read"


# ── smart-home on/off ─────────────────────────────────────────────────────────────────────────────────────────────────
KNOWN_NOUN_COMMANDS = [
    ("turn the heater on", "turn_on", "heater"),
    ("turn the kettle off", "turn_off", "kettle"),
    ("switch the heater off", "turn_off", "heater"),
    ("turn on the heater", "turn_on", "heater"),
    ("turn the lamp on", "turn_on", "lamp"),
    ("can you turn the diffuser on", "turn_on", "diffuser"),
    ("switch off the humidifier", "turn_off", "humidifier"),
]


@pytest.mark.parametrize("text,action,query", KNOWN_NOUN_COMMANDS)
def test_more_household_device_nouns_are_switchable(text, action, query):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and got.domain == "smart_home" and got.action == action, (text, got)
    assert got.query == query and not got.device_unverified
    ir = intent_router.detect_intent(text, log_miss=False)
    assert ir is not None and ir.name == "smart_home" and ir.slots["action"] == action and ir.slots["query"] == query


@pytest.mark.parametrize("text", ["turn the white on", "turn on the white.", "can you turn the lies off", "turn the lite on", "turn the watch off"])
def test_misheard_light_noun_in_either_word_order(text):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and got.domain == "smart_home" and "light" in got.query, (text, got)


@pytest.mark.parametrize("text", [
    "turn the white on the shelf",                     # a trailing clause: a different request
    "I turned the white off yesterday",                # a report, not a command
    "don't turn the watch on",
])
def test_misheard_light_repair_negative_control(text):
    got = sky.classify_skybridge_intent(text)
    assert got is None or got.domain != "smart_home", (text, got)


@pytest.mark.parametrize("text,action,query", [
    ("turn the porch off", "turn_off", "porch"),
    ("turn on the pantry", "turn_on", "pantry"),
    ("turn my sunroom on", "turn_on", "sunroom"),
])
def test_unknown_noun_is_claimed_only_as_an_unverified_device(text, action, query):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and (got.domain, got.action, got.query) == ("smart_home", action, query)
    assert got.device_unverified is True
    # the intent router leaves it to Skybridge, which can check the house's device list
    ir = intent_router.detect_intent(text, log_miss=False)
    assert ir is None or ir.name != "smart_home"


@pytest.mark.parametrize("text", [
    "turn the page",
    "turn the page over",
    "turn the tv on",
    "turn on the tv",
    "turn on the music",
    "turn the volume up",
    "turn it on",
    "turn it back on",
    "turn off everything now",
    "turn the corner",
    "what's on tv",
    "turn the speaker on",
    "turn on the wifi",
    "turn the channel over",
])
def test_near_misses_are_not_device_commands(text):
    got = sky.classify_skybridge_intent(text)
    assert got is None or got.domain != "smart_home" or got.action in ("clarify_state", "status"), (text, got)
    assert not (got and got.domain == "smart_home" and got.action in ("turn_on", "turn_off") and got.device_unverified), (text, got)


# ── the resolver: an unverified device must be REAL, and the auth rules are unchanged ───────────────────────────────
DEVICES = [
    {"entity_id": "switch.hall_heater", "name": "Hall Heater", "domain": "switch", "on": False, "available": True, "dimmable": False},
    {"entity_id": "switch.washer_lamp", "name": "Washer Lamp", "domain": "light", "on": False, "available": True, "dimmable": True},
]


class _I:
    domain = "smart_home"
    entity_id = ""
    duration_seconds = 0

    def __init__(self, action, query, unverified):
        self.action, self.query, self.device_unverified = action, query, unverified


@pytest.fixture
def home(monkeypatch):
    calls = []

    async def _devices():
        return [dict(d) for d in DEVICES]

    async def _scenes():
        return []

    async def _set(entity_id, on, *, brightness_pct=None):
        calls.append((entity_id, on))
        return True

    monkeypatch.setattr(shs, "list_devices", _devices)
    monkeypatch.setattr(shs, "list_scenes", _scenes)
    monkeypatch.setattr(shs, "set_device", _set)
    monkeypatch.setattr(shs, "_turn_refused", lambda what: False)
    return calls


def _run(coro):
    return asyncio.run(coro)


def test_unverified_device_that_exists_is_switched(home):
    out = _run(shs.resolve_smart_home(_I("turn_on", "heater", True)))
    assert out["handled"] is True and home == [("switch.hall_heater", True)]
    assert "Hall Heater" in out["spoken_summary"]


def test_unverified_device_the_house_does_not_have_is_not_hers(home):
    out = _run(shs.resolve_smart_home(_I("turn_on", "page", True)))
    assert out["handled"] is False and out["spoken_summary"] == "" and home == []


def test_unverified_match_is_whole_word_not_substring(home):
    # "wash" is a substring of "Washer Lamp" but not a word of it
    out = _run(shs.resolve_smart_home(_I("turn_on", "wash", True)))
    assert out["handled"] is False and home == []


def test_known_noun_with_no_such_device_still_answers_honestly(home):
    # negative control: the existing behaviour for a KNOWN device noun is unchanged
    out = _run(shs.resolve_smart_home(_I("turn_on", "kettle", False)))
    assert out["handled"] is True and "couldn't find" in out["spoken_summary"].lower() and home == []


def test_refused_turn_never_controls_and_an_unverified_guess_is_not_answered_with_a_refusal(home, monkeypatch):
    monkeypatch.setattr(shs, "_turn_refused", lambda what: True)
    unverified = _run(shs.resolve_smart_home(_I("turn_on", "heater", True)))
    assert unverified["handled"] is False and home == []
    known = _run(shs.resolve_smart_home(_I("turn_on", "heater", False)))       # negative control: the spoken refusal is unchanged
    assert known["handled"] is True and known.get("auth_required") is True and home == []


def test_hub_unreachable_is_the_brains_for_an_unverified_guess(home, monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(shs, "list_devices", _none)
    assert _run(shs.resolve_smart_home(_I("turn_on", "heater", True)))["handled"] is False
    assert _run(shs.resolve_smart_home(_I("turn_on", "heater", False)))["handled"] is True      # a known noun still says so


def test_two_devices_with_the_same_word_ask_which(home, monkeypatch):
    async def _two():
        return [dict(DEVICES[0]), dict(DEVICES[0], entity_id="switch.den_heater", name="Den Heater")]

    monkeypatch.setattr(shs, "list_devices", _two)
    out = _run(shs.resolve_smart_home(_I("turn_on", "heater", True)))
    assert out["handled"] is True and home == []      # asks "which one", switches nothing
