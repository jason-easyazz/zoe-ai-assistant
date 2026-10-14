"""Everyday commands that misfired (research record docs/research/common-questions-ha-intents-2026-10.md, P0 classes).

Measured on Home Assistant's labelled sentences (``test_ha_phrasing_corpus.py`` is the ratchet); this file pins each
class with INVENTED phrasings - positives, hard negatives, and break-the-fix controls that switch the guard off and
show the old misfire come back:

1. A change to a running timer ("add 5 minutes to the timer", "take a minute off the pasta timer", "pause the timer")
   never starts a NEW timer; adjust works on the ONE timer meant, pause/resume says honestly it can't. Halves parse.
2. A device / playback command for LATER ("turn the lights off in 10 minutes", "at 10pm") is never run now.
3. Whole-word keywords: "listening" is not a list, a device named "play room" is not music, a fan's speed is not a dim.
4. List complete/remove word orders write (never a list READ); "is milk on the list?" answers yes/no.
5. Politeness / wake words ("could you please ... for me") are retried without, like HA's skip words.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.ci_safe  # pure classification + the in-memory timer store; no DB, no network

import command_shapes
import conversation_cues
import intent_router
import skybridge_service as sky
import write_receipts
from skybridge_service import SkybridgeIntent


def _claim(text: str) -> tuple[str, str] | None:
    i = sky.classify_skybridge_intent(text)
    return (i.domain, i.action) if i else None


# ── 1. timers ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("set a timer for half an hour", 1800),
        ("an hour and a half", 5400),
        ("two and a half minutes", 150),
        ("1.5 hours", 5400),
        ("half a minute", 30),
        ("a quarter of an hour", 900),
        ("twenty-five minutes", 1500),
        ("1 hour 5 minutes 30 seconds", 3930),
    ],
)
def test_durations_including_halves(text, seconds):
    assert command_shapes.parse_duration_seconds(text) == seconds
    assert sky._parse_timer_duration(text) == seconds


@pytest.mark.parametrize(
    "text,action,seconds,label,start",
    [
        ("add five more minutes to the pasta timer", "increase", 300, "Pasta", 0),
        ("knock two minutes off the egg timer", "decrease", 120, "Egg", 0),
        ("extend the timer by half an hour", "increase", 1800, "", 0),
        ("can you take thirty seconds off the timer", "decrease", 30, "", 0),
        ("give the 10 minute timer another minute", None, 0, "", 0),  # no to/on: not an adjust shape
        ("shorten the 20 minute timer by 5 minutes", "decrease", 300, "", 1200),
        ("pause the bread timer", "pause", 0, "Bread", 0),
        ("unpause the timer", "resume", 0, "", 0),
    ],
)
def test_timer_change_shapes(text, action, seconds, label, start):
    i = sky.classify_skybridge_intent(text)
    if action is None:
        assert i is None or i.action not in ("increase", "decrease")
        return
    assert (i.domain, i.action) == ("timer", action), text
    assert i.duration_seconds == seconds and i.title == label and i.start_seconds == start


@pytest.mark.parametrize(
    "text,action,seconds",
    [
        ("set a timer for 5 minutes", "create", 300),
        ("put the kettle on and set a timer for 3 minutes", "create", 180),
        ("start a twenty minute timer for the roast", "create", 1200),
        ("how long is left on the timer", "status", 0),
        ("cancel the pasta timer", "cancel", 0),
    ],
)
def test_timer_hard_negatives_keep_their_meaning(text, action, seconds):
    i = sky.classify_skybridge_intent(text)
    assert (i.domain, i.action) == ("timer", action), text
    if seconds:
        assert i.duration_seconds == seconds


def test_break_the_fix_adjust_falls_back_to_creating_a_timer(monkeypatch):
    """Control: with the adjust shape switched off, the old false WRITE comes back."""
    monkeypatch.setattr(command_shapes, "timer_adjust", lambda _t: None)
    assert _claim("add 5 minutes to the timer") == ("timer", "create")


@pytest.fixture
def owner():
    name = f"p0-test-{time.monotonic_ns()}"
    yield name
    sky._timers._by_owner.pop(name, None)
    write_receipts.reset(name)


def _adjust(owner, action, seconds=0, label="", start=0):
    return sky._resolve_timer(SkybridgeIntent(domain="timer", action=action, duration_seconds=seconds, title=label,
                                              start_seconds=start), owner)


def test_adjust_moves_the_one_timer_and_never_creates(owner):
    t = sky._timers.create(owner, "Pasta", 600)
    end = t["expires_at"]
    out = _adjust(owner, "increase", 300)
    timers = sky._timers.list(owner)
    assert len(timers) == 1 and timers[0]["timer_id"] == t["timer_id"]
    assert timers[0]["expires_at"] == pytest.approx(end + 300)
    assert out["spoken_summary"].startswith("Added 5 minutes to your Pasta timer.")
    assert write_receipts.has(owner, 0.0, "timer")
    out = _adjust(owner, "decrease", 60, label="pasta")
    assert sky._timers.list(owner)[0]["expires_at"] == pytest.approx(end + 240)
    assert out["spoken_summary"].startswith("Took 1 minute off your Pasta timer.")
    # "the 10 minute timer" still means it after the change: matched on the length it was SET for.
    _adjust(owner, "increase", 60, start=600)
    assert sky._timers.list(owner)[0]["expires_at"] == pytest.approx(end + 300)


def test_adjust_with_no_timer_creates_nothing(owner):
    out = _adjust(owner, "increase", 300)
    assert out["spoken_summary"] == "You have no timers running."
    assert sky._timers.list(owner) == []


def test_adjust_never_guesses_between_timers(owner):
    a = sky._timers.create(owner, "Pasta", 600)
    b = sky._timers.create(owner, "Eggs", 300)
    before = {t["timer_id"]: t["expires_at"] for t in sky._timers.list(owner)}
    out = _adjust(owner, "increase", 60)
    assert "Which one" in out["spoken_summary"]
    out = _adjust(owner, "increase", 60, label="Rice")
    assert out["spoken_summary"] == "I couldn't find a rice timer."
    assert {t["timer_id"]: t["expires_at"] for t in sky._timers.list(owner)} == before
    _adjust(owner, "increase", 60, label="Egg")  # "the egg timer" for a timer called Eggs
    after = {t["timer_id"]: t["expires_at"] for t in sky._timers.list(owner)}
    assert after[b["timer_id"]] == pytest.approx(before[b["timer_id"]] + 60)
    assert after[a["timer_id"]] == before[a["timer_id"]]


def test_decrease_past_zero_leaves_the_timer_alone(owner):
    t = sky._timers.create(owner, "Timer", 120)
    out = _adjust(owner, "decrease", 600)
    assert "so I left it alone" in out["spoken_summary"]
    assert sky._timers.list(owner)[0]["expires_at"] == t["expires_at"]


def test_pause_and_resume_are_honest_and_change_nothing(owner):
    t = sky._timers.create(owner, "Timer", 300)
    for action in ("pause", "resume"):
        out = _adjust(owner, action)
        assert out["spoken_summary"].startswith(f"I can't {action} timers yet.")
    assert sky._timers.list(owner) == [t]


def test_intent_router_never_reads_a_timer_change_as_a_list_write():
    for text in ("add 10 minutes to the roast timer", "remove 2 minutes from the timer", "pause the timer"):
        hit = intent_router.detect_intent(text, log_miss=False)
        assert hit is None or not hit.name.startswith(("list_", "timer_create")), (text, hit)


# ── 2. delayed commands ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,domain",
    [
        ("turn off the kitchen lights in ten minutes", "smart_home"),
        ("at 10pm switch the porch light on", "smart_home"),
        ("turn on the fan for twenty minutes", "smart_home"),
        ("when I go to bed switch off the hallway light", "smart_home"),
        ("stop the music in half an hour", "music"),
    ],
)
def test_a_command_for_later_is_not_run_now(text, domain):
    assert _claim(text) == (domain, "delayed"), text


@pytest.mark.parametrize(
    "text,claim",
    [
        ("turn off the lights in the kitchen", ("smart_home", "turn_off")),
        ("dim the lamp to 30%", ("smart_home", "set_brightness")),
        ("play tonight tonight", ("music", "play")),  # a title, not a time
        ("turn the lights on", ("smart_home", "turn_on")),
    ],
)
def test_now_commands_still_run_now(text, claim):
    assert _claim(text) == claim, text


def test_break_the_fix_delayed_command_runs_now_without_the_guard(monkeypatch):
    monkeypatch.setattr(command_shapes, "delayed_command", lambda *_a, **_k: False)
    assert _claim("turn off the kitchen lights in ten minutes") == ("smart_home", "turn_off")


def test_delayed_command_never_reaches_a_device(monkeypatch):
    import smart_home_service

    async def boom(*_a, **_k):
        raise AssertionError("a delayed command reached the device resolver")

    monkeypatch.setattr(smart_home_service, "resolve_smart_home", boom)
    out = asyncio.run(sky.resolve_skybridge_request("turn off the kitchen lights in ten minutes", "p0-user"))
    assert out["handled"] and out["spoken_summary"] == command_shapes.DELAYED_COMMAND_REPLY
    hit = intent_router.detect_intent("turn off the lights in 5 minutes", log_miss=False)
    assert hit.name == "smart_home" and hit.slots["action"] == "delayed"
    assert asyncio.run(intent_router._execute_smart_home_intent(hit, "p0-user")) == command_shapes.DELAYED_COMMAND_REPLY


def test_intent_router_reads_light_direction_and_questions():
    assert intent_router.detect_intent("turn off the lights on the back porch", log_miss=False).slots["action"] == "turn_off"
    assert intent_router.detect_intent("turn on the lights in the office", log_miss=False).slots["action"] == "turn_on"
    hit = intent_router.detect_intent("are the lights on in the garage", log_miss=False)
    assert hit is None or hit.name != "smart_home"


# ── 3. whole words, not substrings ───────────────────────────────────────────


@pytest.mark.parametrize(
    "text,not_domain",
    [
        ("are you always listening", "lists"),
        ("I was listening to a podcast", "lists"),
        ("turn on the play room lights", "music"),
        ("set the bedroom fan speed to 40%", "smart_home"),  # a speed is not a brightness
    ],
)
def test_keyword_inside_another_word_does_not_claim(text, not_domain):
    claim = _claim(text)
    if not_domain == "smart_home":
        assert claim != ("smart_home", "set_brightness"), text
    else:
        assert claim is None or claim[0] != not_domain, (text, claim)


@pytest.mark.parametrize(
    "text",
    [
        "remind me to bring the washing in if it rains",
        "help me wind down",
        "what is the temperature in the living room",
        "how warm is the nursery temperature",
        "set the bedroom lamp colour temperature to 2700 K",
        "set the temperature to 21",
        "remind me to check the weather",
        "I am feeling under the weather",
    ],
)
def test_weather_words_about_something_else_are_not_weather(text):
    claim = _claim(text)
    assert claim is None or claim[0] != "weather", (text, claim)


@pytest.mark.parametrize(
    "text",
    ["what is the temperature outside", "will it rain tomorrow", "is it windy", "what's the weather like",
     "what is the outside temperature", "how hot is it going to get today", "any rain this afternoon"],
)
def test_weather_questions_still_claim_weather(text):
    assert _claim(text)[0] == "weather", text


def test_break_the_fix_temperature_substring_steals_again(monkeypatch):
    import re

    import weather_speech

    monkeypatch.setattr(weather_speech, "_NOT_WEATHER_RE", re.compile(r"(?!)"))
    assert _claim("what is the temperature in the living room")[0] == "weather"


def test_break_the_fix_substring_keywords_steal_again(monkeypatch):
    import re

    monkeypatch.setattr(sky, "_LIST_KEYWORD_RE", re.compile(r" list"))
    assert _claim("are you always listening") == ("lists", "show")
    monkeypatch.setattr(sky, "_command_shaped", lambda message, verbs: re.search(verbs, message))
    assert _claim("turn on the play room lights") == ("music", "play")


@pytest.mark.parametrize(
    "text,claim",
    [
        ("what's on my shopping list", ("lists", "show")),
        ("show my lists", ("lists", "overview")),
        ("what's on my schedule tomorrow", ("calendar", "show")),
        ("play some jazz in the kitchen", ("music", "play")),
        ("can you play the beatles", ("music", "play")),
    ],
)
def test_whole_word_keywords_still_claim(text, claim):
    assert _claim(text) == claim, text


# ── 4. lists ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,action,item,list_type,list_name",
    [
        ("complete buy stamps on my errands list", "complete_item", "buy stamps", "", "errands"),
        ("remove eggs on my shopping list", "remove_item", "eggs", "shopping", ""),
        ("delete the batteries in my hardware list", "remove_item", "batteries", "", "hardware"),
        ("check bread off from my list", "complete_item", "bread", "shopping", ""),
        ("tick off milk", "complete_item", "milk", "shopping", ""),
        ("is milk on the shopping list", "contains", "milk", "shopping", ""),
        ("do we have eggs on the list", "contains", "eggs", "", ""),
        ("hey zoe is there any butter on my grocery list?", "contains", "butter", "shopping", ""),
    ],
)
def test_list_word_orders(text, action, item, list_type, list_name):
    i = sky.classify_skybridge_intent(text)
    assert (i.domain, i.action, i.item_text, i.list_type, i.list_name) == ("lists", action, item, list_type, list_name)


@pytest.mark.parametrize("text", ["what's on the list", "is the list long", "complete the form for school"])
def test_list_hard_negatives(text):
    i = sky.classify_skybridge_intent(text)
    assert i is None or i.action not in ("contains", "complete_item", "remove_item"), (text, i)


def _lists():
    shop = {"id": "s", "name": "Shopping", "list_type": "shopping"}
    work = {"id": "w", "name": "Work", "list_type": "work"}
    items = {"s": [{"text": "Milk", "completed": 0}, {"text": "bread", "completed": 1}], "w": [{"text": "report"}]}
    return [shop, work], items


@pytest.mark.parametrize(
    "item,list_type,expected",
    [
        ("milk", "shopping", "Yes, Milk is on your Shopping list."),
        ("bread", "", "Bread is on your Shopping list, but it's already ticked off."),
        ("eggs", "shopping", "No, eggs isn't on your Shopping list."),
        ("eggs", "", "No, eggs isn't on any of your lists."),
        ("report", "", "Yes, report is on your Work list."),
    ],
)
def test_membership_answers_yes_or_no_first(item, list_type, expected):
    lists, items = _lists()
    spoken, _ = sky._list_contains_answer(
        SkybridgeIntent(domain="lists", action="contains", item_text=item, list_type=list_type), lists, items)
    assert spoken == expected


def test_a_named_list_that_does_not_exist_is_never_the_default_list():
    class _DB:
        async def fetch(self, sql, *args):
            assert "lower(name)" in sql, "a named list must be looked up by name, never by the default type"
            return []

    intent = SkybridgeIntent(domain="lists", action="remove_item", item_text="hammer", list_name="hardware")
    assert asyncio.run(sky._find_list_for_remove(intent, "u1", _DB(), None)) is None


# ── 5. politeness / wake words ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,name",
    [
        ("can you remind me to call the plumber tomorrow", "reminder_create"),
        ("could you tell me the time", "time_query"),
        ("would you be able to show my reminders please", "reminder_list"),
    ],
)
def test_lead_ins_are_retried_without(text, name):
    hit = intent_router.detect_intent(text, log_miss=False)
    assert hit is not None and hit.name == name, (text, hit)


@pytest.mark.parametrize("text", ["would you like to hear a joke", "can you hear me", "can you remember what I said"])
def test_questions_and_offers_are_not_stripped(text):
    assert conversation_cues.strip_command_filler(text) == text


def test_break_the_fix_lead_in_defeats_the_anchor_without_the_strip(monkeypatch):
    monkeypatch.setattr(conversation_cues, "strip_command_filler", lambda t: t)
    assert intent_router.detect_intent("can you remind me to call the plumber tomorrow", log_miss=False) is None


def test_a_phrase_that_already_matches_is_untouched():
    """The retry only ever ADDS a claim: every corpus-style phrase with a lead-in that matched before still does."""
    for text in ("please turn off the lights", "hey zoe what's the weather", "can you add milk to the shopping list"):
        assert sky.classify_skybridge_intent(text) == sky.classify_skybridge_intent(text, _filler_retry=False)
