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
        # a command that LASTS is a command for now (it used to play; the "for" clause is not a delay)
        ("turn on the fan for twenty minutes", ("smart_home", "turn_on")),
        ("play white noise for eight hours", ("music", "play")),
        # a day word or condition OUTSIDE the command's clause, a room name, a politeness tail
        ("turn off the lights, talk to you later", ("smart_home", "turn_off")),
        ("turn on the lights in the morning room", ("smart_home", "turn_on")),
        ("turn on the lights if you can", ("smart_home", "turn_on")),
        ("play relaxing music for the next hour", ("music", "play")),  # "next hour" is not a track skip
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


# ── review round: more shapes of the same classes ───────────────────────────


@pytest.mark.parametrize(
    "text,action,seconds,start",
    [
        ("take off a minute from the timer", "decrease", 60, 0),
        ("add another minute to the timer", "increase", 60, 0),
        ("another 5 minutes on the timer", "increase", 300, 0),
        ("make the timer 5 minutes longer", "increase", 300, 0),
        ("make the timer longer", "increase", 0, 0),  # no amount: asked, never guessed
        ("add some time to the timer", "increase", 0, 0),
        ("remove the 5 minute timer", "cancel", 0, 300),  # used to START a 5-minute timer
    ],
)
def test_more_timer_change_shapes_never_create(text, action, seconds, start):
    i = sky.classify_skybridge_intent(text)
    assert (i.domain, i.action, i.duration_seconds, i.start_seconds) == ("timer", action, seconds, start), text
    hit = intent_router.detect_intent(text, log_miss=False)
    assert hit is None or not hit.name.startswith(("list_", "timer_create")), (text, hit)


def test_a_change_with_no_amount_asks(owner):
    sky._timers.create(owner, "Timer", 300)
    out = _adjust(owner, "increase", 0)
    assert out["spoken_summary"] == "How much time should I add to your timer?"


def test_name_and_length_must_both_match(owner):
    sky._timers.create(owner, "Pasta", 300)
    eggs = sky._timers.create(owner, "Eggs", 600)
    i = sky.classify_skybridge_intent("add 2 minutes to my 10 minute pasta timer")
    out = sky._resolve_timer(i, owner)
    assert out["spoken_summary"] == "I couldn't find a pasta timer."
    assert sky._timers.list(owner)[-1]["expires_at"] == eggs["expires_at"]


def test_a_named_cancel_never_cancels_another_timer(owner):
    sky._timers.create(owner, "Pasta", 300)
    sky._timers.create(owner, "Eggs", 600)
    out = sky._resolve_timer(sky.classify_skybridge_intent("cancel the rice timer"), owner)
    assert out["spoken_summary"] == "I couldn't find a rice timer." and len(sky._timers.list(owner)) == 2
    sky._resolve_timer(sky.classify_skybridge_intent("cancel the eggs timer"), owner)
    assert [t["label"] for t in sky._timers.list(owner)] == ["Pasta"]


def test_a_described_cancel_of_the_only_unnamed_timer_works(owner):
    sky._timers.create(owner, "Timer", 300)
    out = sky._resolve_timer(sky.classify_skybridge_intent("stop the kitchen timer"), owner)
    assert out["spoken_summary"] == "Cancelled your timer." and sky._timers.list(owner) == []


@pytest.mark.parametrize(
    "text,action",
    [
        ("turn on the lights and turn off the tv", "turn_on"),
        ("turn the lights on and the fan off", "turn_on"),
        ("turn off the lights on the first floor", "turn_off"),
        ("turn the overhead light on the first floor off", "turn_off"),
    ],
)
def test_intent_router_light_direction_reads_the_first_clause(text, action):
    assert intent_router.detect_intent(text, log_miss=False).slots["action"] == action


@pytest.mark.parametrize("text", ["why are the lights on", "i left the lights on", "should i turn off the lights"])
def test_intent_router_light_questions_are_not_commands(text):
    hit = intent_router.detect_intent(text, log_miss=False)
    assert hit is None or hit.name != "smart_home", (text, hit)


@pytest.mark.parametrize(
    "text",
    ["i need to put the kids to bed", "i want to remove my name from the list", "can you play dead", "can you play fair"],
)
def test_statements_are_not_stripped_into_writes(text):
    hit = intent_router.detect_intent(text, log_miss=False)
    assert hit is None or hit.name not in ("list_add", "list_remove", "music_play"), (text, hit)


def test_intent_router_delayed_playback_is_not_started():
    hit = intent_router.detect_intent("play the news at 6am", log_miss=False)
    assert hit.name == "music_play" and hit.slots.get("delayed") is True
    assert asyncio.run(intent_router._execute_music_intent(hit, "p0-user")) == command_shapes.DELAYED_COMMAND_REPLY
    assert not intent_router.detect_intent("play white noise for 8 hours", log_miss=False).slots.get("delayed")


@pytest.mark.parametrize(
    "text,action,list_type,list_name",
    [
        ("remove milk from the grocery shopping list", "remove_item", "shopping", ""),
        ("remove milk from this list", "remove_item", "shopping", ""),
    ],
)
def test_typed_and_demonstrative_lists_are_not_names(text, action, list_type, list_name):
    i = sky.classify_skybridge_intent(text)
    assert (i.action, i.list_type, i.list_name) == (action, list_type, list_name)


@pytest.mark.parametrize("text", ["complete it on the list", "delete it in the list", "remove that from the list"])
def test_a_pronoun_is_never_a_list_item(text):
    i = sky.classify_skybridge_intent(text)
    assert i is None or i.action not in ("complete_item", "remove_item"), (text, i)


def test_membership_in_a_missing_named_list_never_answers_about_another_list():
    lists, items = _lists()
    spoken, found = sky._list_contains_answer(
        SkybridgeIntent(domain="lists", action="contains", item_text="milk", list_name="hardware"), lists, items)
    assert spoken == "I couldn't find a list called hardware." and found is None


def test_delay_reader_is_linear_on_a_looping_transcript():
    started = time.monotonic()
    command_shapes.delayed_command("turn off the lights in " + "twenty five " * 40 + "zebra")
    assert time.monotonic() - started < 0.5


# ── cross-review round (codex): ambiguity, questions, transport delays, destructive list questions ──


def test_a_bare_cancel_with_several_timers_asks(owner):
    sky._timers.create(owner, "Eggs", 180)
    sky._timers.create(owner, "Pasta", 600)
    out = sky._resolve_timer(sky.classify_skybridge_intent("cancel the timer"), owner)
    assert "Which one?" in out["spoken_summary"] and len(sky._timers.list(owner)) == 2
    out = sky._resolve_timer(sky.classify_skybridge_intent("cancel all timers"), owner)
    assert out["spoken_summary"] == "Cancelled all 2 timers." and sky._timers.list(owner) == []


@pytest.mark.parametrize(
    "text",
    [
        "should I add 5 minutes to the timer?",
        "should I cancel the timer?",
        "why did you cancel the timer?",
        "can I take a minute off the timer",
        "did you set a timer for 10 minutes",
        "I was going to set a timer for the rice",
    ],
)
def test_a_question_about_a_timer_never_writes_one(owner, text):
    sky._timers.create(owner, "Pasta", 600)
    before = [(t["timer_id"], t["expires_at"]) for t in sky._timers.list(owner)]
    i = sky.classify_skybridge_intent(text)
    assert (i.domain, i.action) == ("timer", "status"), (text, i)
    sky._resolve_timer(i, owner)  # resolver level: nothing created, moved or cancelled
    assert [(t["timer_id"], t["expires_at"]) for t in sky._timers.list(owner)] == before


@pytest.mark.parametrize(
    "text",
    ["stop the music tomorrow", "pause the music later", "turn off the lights and do it tomorrow"],
)
def test_transport_and_back_referenced_delays_are_not_run_now(text):
    assert _claim(text)[1] == "delayed", text


@pytest.mark.parametrize(
    "text",
    [
        "can I remove milk from my chores list",
        "I was going to remove milk from my chores list",
        "did you check milk off my chores list",
        "would you like to complete milk on my chores list",
    ],
)
def test_a_question_about_a_list_change_never_writes(text):
    i = sky.classify_skybridge_intent(text)
    assert i is None or i.action not in ("remove_item", "complete_item"), (text, i)


def test_an_adjust_moves_the_card_fields_it_should(owner):
    t = sky._timers.create(owner, "Soup", 600)
    out = _adjust(owner, "increase", 120)
    props = out["cards"][0]["props"]
    assert props["expires_at_ms"] == int((t["expires_at"]) * 1000)  # the store row itself moved
    assert props["duration_seconds"] == 720
    assert sky._timers.list(owner)[0]["set_seconds"] == 600  # still "the 10 minute timer"


# ── PR review threads (codex) ────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["cancel all but the pasta timer", "cancel every timer except the eggs"])
def test_an_exclusion_never_cancels_everything(owner, text):
    sky._timers.create(owner, "Pasta", 600)
    sky._timers.create(owner, "Eggs", 300)
    i = sky.classify_skybridge_intent(text)
    assert (i.action, i.query) != ("cancel", "all"), text
    sky._resolve_timer(i, owner)
    assert len(sky._timers.list(owner)) == 2


def test_membership_in_a_named_list_searches_for_the_item_not_the_list_name():
    lists = [{"id": "c", "name": "Chores", "list_type": "tasks"}, {"id": "s", "name": "Shopping", "list_type": "shopping"}]
    items = {"c": [{"text": "milk", "completed": 0}], "s": []}
    spoken, found = sky._list_contains_answer(
        SkybridgeIntent(domain="lists", action="contains", item_text="milk", list_name="chores"), lists, items)
    assert spoken == "Yes, milk is on your Chores list." and found["id"] == "c"


def test_an_enumerated_subset_never_cancels_everything(owner):
    for label, secs in (("Pasta", 600), ("Eggs", 300), ("Rice", 900)):
        sky._timers.create(owner, label, secs)
    i = sky.classify_skybridge_intent("cancel both the pasta and eggs timers")
    sky._resolve_timer(i, owner)
    assert len(sky._timers.list(owner)) == 3
    out = sky._resolve_timer(sky.classify_skybridge_intent("cancel both timers"), owner)  # three running: ambiguous
    assert "Which one?" in out["spoken_summary"] and len(sky._timers.list(owner)) == 3
    out = sky._resolve_timer(sky.classify_skybridge_intent("stop all of my timers in the kitchen"), owner)
    assert out["spoken_summary"] == "Cancelled all 3 timers."


@pytest.mark.parametrize(
    "text",
    [
        "cancel the first timer",  # an ordinal names no timer: several running = ask
        "cancel the other timer",
        "stop that timer",
        "cancel the pasta timer and the eggs timer",  # two targets: never half a delete
        "don't cancel the timer",
        "never mind dont stop the timer",
    ],
)
def test_cancel_self_audit_never_deletes_a_timer_nobody_named(owner, text):
    sky._timers.create(owner, "Pasta", 600)
    sky._timers.create(owner, "Eggs", 300)
    sky._resolve_timer(sky.classify_skybridge_intent(text), owner)
    assert len(sky._timers.list(owner)) == 2, text


@pytest.mark.parametrize("text", ["do not remove milk from the list", "don't tick off the bread"])
def test_a_negated_list_change_writes_nothing(text):
    i = sky.classify_skybridge_intent(text)
    assert i is None or i.action not in ("remove_item", "complete_item"), (text, i)


@pytest.mark.parametrize(
    "text,survivors",
    [
        ("cancel every pasta timer", {"Eggs", "Rice"}),
        ("cancel every 5-minute timer", {"Pasta", "Rice"}),  # Eggs was set for 5 minutes
        ("cancel all of the rice timers", {"Pasta", "Eggs"}),
    ],
)
def test_a_qualified_quantifier_cancels_only_what_it_names(owner, text, survivors):
    for label, secs in (("Pasta", 600), ("Eggs", 300), ("Rice", 900)):
        sky._timers.create(owner, label, secs)
    sky._resolve_timer(sky.classify_skybridge_intent(text), owner)
    assert {t["label"] for t in sky._timers.list(owner)} == survivors, text


@pytest.mark.parametrize(
    "text",
    ["turn off the lights in a quarter of an hour", "after three quarters of an hour turn on the fan"],
)
def test_quarter_hour_delays_are_not_run_now(text):
    assert _claim(text) == ("smart_home", "delayed"), text


@pytest.mark.parametrize(
    "text,seconds,label",
    [("increase the timer to 10 minutes", 600, ""), ("reduce the pasta timer to 5 minutes", 300, "Pasta")],
)
def test_a_set_to_change_moves_the_running_timer_and_never_creates(owner, text, seconds, label):
    sky._timers.create(owner, "Pasta", 420)
    i = sky.classify_skybridge_intent(text)
    assert (i.action, i.duration_seconds, i.title) == ("set", seconds, label), text
    out = sky._resolve_timer(i, owner)
    timers = sky._timers.list(owner)
    assert len(timers) == 1 and timers[0]["expires_at"] - time.time() == pytest.approx(seconds, abs=2)
    assert out["spoken_summary"].startswith("Your Pasta timer now has")


@pytest.mark.parametrize(
    "text",
    ["turn off the lights at seven", "turn off the lights at seven thirty", "turn on the porch light at half past six",
     "switch off the fan at ten pm"],
)
def test_spoken_clock_times_are_delays(text):
    assert _claim(text)[1] == "delayed", text


def test_spoken_clock_negatives_still_run_now():
    assert _claim("turn on the lights at one end of the room") == ("smart_home", "turn_on")


@pytest.mark.parametrize("text", ["cancel every other timer", "cancel every second timer"])
def test_ordinal_subsets_never_cancel_everything(owner, text):
    for label, secs in (("Pasta", 600), ("Eggs", 300), ("Rice", 900)):
        sky._timers.create(owner, label, secs)
    sky._resolve_timer(sky.classify_skybridge_intent(text), owner)
    assert len(sky._timers.list(owner)) == 3, text
