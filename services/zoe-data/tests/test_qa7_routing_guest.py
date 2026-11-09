"""QA round 7 routing + guest fixes. Synthetic phrasings only (ci_safe). Each fix has a NEGATIVE CONTROL: the neighbouring phrase that must NOT change.

Pinned (docs/knowledge/qa7-routing-guest.md):
  1. a person's routine / a named kind of event is not the clock and not today's agenda (memory_gate + expert_dispatch);
  2. a weather question naming a place is the weather expert's; "and in albany" after a weather answer is the same question; a second,
     non-weather question on the same turn is the brain's (weather_speech / weather_followup / fast_tiers);
  3. "dont forget to add X" is the add command (asks which list for a task), and a decision question is not a complaint (intent_router);
  4. a spelled-short goodnight is a goodbye (clean_goodbye + the fast tier);
  5. a feeling's turn never gets a question three turns running and never a stock "I'm here to listen" (reply_polish);
  6. a stranger's "remember me" / "forget me" / "who am I" is answered honestly in code, a member's is untouched (guest_asks).
"""
from __future__ import annotations

import asyncio

import pytest

import clean_goodbye as cg
import expert_dispatch as xd
import guest_asks as ga
import intent_router as ir
import memory_gate as mg
import reply_polish as rp
import weather_followup as wf
import weather_speech as ws

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_REPLY_POLISH", raising=False)
    rp.reset_state()
    wf.reset_state()


# ── 1. routine and named-event questions ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "which day is max at kindy", "when is max at kindy", "which day is mum at work", "what time does zara finish at uni",
    "what exam do i have and when", "what meeting do i have", "what appointments do we have",
])
def test_routine_and_named_event_questions_are_the_persons_own_life(text):
    assert mg.is_event_time_question(text)
    assert xd._plan("time", text) is None          # never the clock


@pytest.mark.parametrize("text", [
    "what time is it", "what time is it in perth", "when is it at school", "what time does the game start", "what do i have on today",
])
def test_negative_control_clock_and_agenda_questions_stay_theirs(text):
    assert not mg.is_event_time_question(text)


def test_clock_question_still_reaches_the_clock():
    assert xd._plan("time", "what time is it")[0] == "time_query"


def test_the_brain_gets_the_stored_rows_for_these_without_a_flag(monkeypatch):
    """The planners hand them to the brain, so the recall floor must claim them (positive routing: memory + calendar tool)."""
    import zoe_flue_client as zfc

    monkeypatch.delenv("ZOE_RECALL_PRESENT_STATE_SHAPES", raising=False)
    assert zfc._recall_question_shape("which day is max at kindy") == "event_time"
    assert zfc._recall_question_shape("what exam do i have and when") == "event_time"
    for t in ("what time is it", "what is the capital of france", "what time does the game start"):
        assert zfc._recall_question_shape(t) == "", t          # negative control: nothing else gained a shape


def test_a_named_event_with_no_day_is_not_todays_agenda():
    assert xd._plan("calendar", "what exam do i have and when") is None


def test_negative_control_a_generic_agenda_question_is_still_read():
    assert xd._plan("calendar", "what appointments do I have")[0] == "calendar_show"


def test_negative_control_a_named_event_WITH_a_day_is_still_read():
    assert xd._plan("calendar", "what exam do i have tomorrow") == ("calendar_show", {"qualifier": "tomorrow"}, "read")
    assert xd._plan("calendar", "do i have anything on today")[0] == "calendar_show"


# ── 2. weather ───────────────────────────────────────────────────────────────────────────────────────

def test_compound_weather_plus_cooking_is_the_brains():
    for t in ("whats the weather in mandurah and whats the temp for roast potatoes",
              "whats the weather in bunbury and how hot for a pork roast",
              "weather in bunbury and cooking temperature for pork roast", "weather in bunbury plus cooking temp for chicken",
              "weather in bunbury and safe internal temp for pork"):
        assert ws.has_other_question(t)
        assert xd._plan("weather", t) is None


def test_negative_control_weather_only_compounds_are_still_answered():
    for t in ("weather in perth and will it rain tomorrow", "weather in fremantle today? and mandurah", "weather in perth and what about albany",
              "weather in perth and how hot tomorrow", "weather in perth and outside temp"):
        assert not ws.has_other_question(t)
    assert xd._plan("weather", "weather in perth and will it rain tomorrow")[0] == "weather"


def test_explicit_place_weather_text():
    assert wf.explicit_text("temp in albany") == "temp in albany"
    assert wf.explicit_text("what's the weather in broome today")
    for t in ("what's the temp in the oven", "temperature for roast chicken", "whats the weather in bunbury and how hot for pork",
              "what time is it in albany", "weather"):
        assert wf.explicit_text(t) is None, t


def test_place_follow_up_needs_a_recent_weather_answer():
    assert wf.recent("u", "s") is None                       # nothing asked yet: "and broome" is not a weather question
    wf.note("u", "s", "weather in bunbury tomorrow")
    assert wf.recent("u", "s") == "tomorrow"
    assert wf.followup_text("and broome", "tomorrow") == "what's the weather in broome tomorrow"
    assert wf.followup_text("and in albany", "") == "what's the weather in albany"
    assert wf.followup_text("what about mt barker?", "") == "what's the weather in mt barker"


def test_negative_control_follow_up_shapes_that_are_not_places():
    for t in ("and tomorrow", "and you", "and then", "ok and in the kitchen", "and thanks", "and what about the oven"):
        assert wf.followup_text(t, "today") is None, t


def test_follow_up_window_expires():
    wf.note("u", "s", "weather in bunbury", now=1000.0)
    assert wf.recent("u", "s", now=1000.0 + wf.WINDOW_S - 1) == ""
    assert wf.recent("u", "s", now=1000.0 + wf.WINDOW_S + 1) is None
    assert wf.recent("u", "other", now=1001.0) is None        # another conversation never inherits it


def test_weather_place_tier_answers_a_place_follow_up(monkeypatch):
    import fast_tiers

    seen = []

    async def fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen.append((domain, text))
        return xd.DispatchResult(domain="weather", reply="It's 14 degrees in Albany.", intent="weather")

    monkeypatch.setattr(xd, "dispatch", fake_dispatch)
    # no earlier weather answer: a bare "and in albany" is not touched
    assert asyncio.run(fast_tiers._weather_place_tier("and in albany", "u", "s", {})) is None
    wf.note("u", "s", "weather in bunbury")
    res = asyncio.run(fast_tiers._weather_place_tier("and in albany", "u", "s", {}))
    assert res is not None and res.reply.startswith("It's 14")
    assert seen == [("weather", "what's the weather in albany")]
    seen.clear()
    res = asyncio.run(fast_tiers._weather_place_tier("temp in albany", "u2", "s2", {}))
    assert res is not None and seen == [("weather", "temp in albany")]


# ── 3. planning talk and decisions ───────────────────────────────────────────────────────────────────

def test_dont_forget_to_add_is_the_add_command():
    d = ir.detect_intent("dont forget to add screws", log_miss=False)
    assert d.name == "list_add" and d.slots["item"] == "screws"                  # not "to add screws"
    assert d.slots.get("ask_list") is True                                       # hardware: ask which list


def test_dont_forget_to_add_a_task_asks_which_list():
    d = ir.detect_intent("dont forget to add print my study notes", log_miss=False)
    assert d.name == "list_add" and d.slots.get("ask_list") is True and d.slots["item"] == "print my study notes"


def test_negative_control_a_grocery_still_goes_straight_on():
    d = ir.detect_intent("dont forget to add milk", log_miss=False)
    assert d.name == "list_add" and d.slots["item"] == "milk" and not d.slots.get("ask_list")
    d = ir.detect_intent("dont forget milk", log_miss=False)
    assert d.name == "list_add" and d.slots["item"] == "milk"


def test_the_add_command_is_not_vetoed_by_the_head(monkeypatch):
    import fast_tiers
    import semantic_router

    def boom(_text):
        raise AssertionError("the head must not be consulted for an explicit add command")

    monkeypatch.setattr(semantic_router, "head_verdict", boom)
    assert fast_tiers.intent_gate("list_add", "dont forget to add screws", lane="chat") is True
    assert fast_tiers._FORGET_TO_ADD_RE.match("remember to put bins on the list")
    assert not fast_tiers._FORGET_TO_ADD_RE.match("add screws")          # a plain add still goes to the head (unchanged)


def test_a_decision_question_is_not_a_complaint():
    assert ir.detect_intent("should i get a new ute or fix the old one", log_miss=False) is None
    assert ir.detect_intent("should we fix the fence or replace it", log_miss=False) is None


def test_negative_control_a_real_complaint_is_still_a_report():
    for t in ("fix the lights", "you need to fix the bug", "you got that wrong"):
        d = ir.detect_intent(t, log_miss=False)
        assert d is not None and d.name == "user_issue_report", t


# ── 4. goodnight ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["gnite", "Gnite!", "g'night zoe", "nite nite", "gn", "nini", "night night", "nighty"])
def test_spelled_short_goodnights_are_goodbyes(text):
    assert cg.is_goodnight_short(text) and cg.is_farewell(text)


@pytest.mark.parametrize("text", ["gnome", "gn the light", "is it night?", "turn off the light gnite", "night mode on"])
def test_negative_control_not_a_goodnight(text):
    assert not cg.is_goodnight_short(text)


def test_goodnight_tier_replies_in_code_and_leaves_other_turns():
    import fast_tiers

    res = fast_tiers._qa7_conversation_tier("gnite", "demo_user")
    assert res is not None and res.reply == "Night. Sleep well." and "light" not in res.reply
    assert fast_tiers._qa7_conversation_tier("turn on the light", "demo_user") is None
    assert fast_tiers._qa7_conversation_tier("good night", "demo_user") is None     # the ordinary spelling keeps its path


# ── 5. a feeling's turn ──────────────────────────────────────────────────────────────────────────────

OVERWHELMED = "im overwhelmed"
TWO_Q = "That sounds like a lot to hold, with work and the kids. What feels heaviest right now?"


def test_a_feeling_turn_after_a_question_ends_on_a_statement():
    strict, stock = rp.feeling_turn_policy(OVERWHELMED, "u", "s")
    assert (strict, stock) == (False, True)                        # the last reply did not end on a question
    rp.note_reply("u", "s", "How are you doing?")
    strict, _ = rp.feeling_turn_policy("i dont know how people do it", "u", "s")
    assert strict is True
    out = rp.polish(TWO_Q, "i dont know how people do it", True, strict_cap=True, stock=True)
    assert out == "That sounds like a lot to hold, with work and the kids."


def test_never_a_question_three_turns_running():
    msg = "i dont know how people do it"
    replies = []
    for _ in range(4):
        strict, stock = rp.feeling_turn_policy(msg, "u", "s")
        out = rp.polish(TWO_Q, msg, rp.ended_on_question("u", "s"), strict_cap=strict, stock=stock)
        rp.note_reply("u", "s", out)
        replies.append(out.rstrip().endswith("?"))
    assert replies == [True, False, True, False]


def test_a_short_reply_right_after_a_feeling_is_still_that_moment():
    # the guest's real shape: feeling, feeling, then "yeah" - three turns, a question on every one without the breaker
    rp.feeling_turn_policy("i snapped at my youngest today and i feel horrible", "u", "s")
    rp.note_reply("u", "s", "Have you had a chance to talk to your youngest?")
    assert rp.feeling_turn_policy("yeah", "u", "s") == (True, True)
    rp.note_reply("u", "s", "That sounds like a good step.")
    assert rp.feeling_turn_policy("yeah", "u", "s")[0] is False       # the last reply was a statement: a question is allowed again


def test_negative_control_the_moment_ends():
    rp.feeling_turn_policy("i snapped at my youngest today and i feel horrible", "u", "s")
    rp.note_reply("u", "s", "How did it go?")
    for _ in range(2):
        rp.feeling_turn_policy("ok", "u", "s")
    assert rp.feeling_turn_policy("ok", "u", "s") == (False, False)    # three turns on: ordinary conversation again
    rp.note_reply("u", "s", "How did it go?")
    assert rp.feeling_turn_policy("what is the capital of france", "u", "s") == (False, False)


def test_negative_controls_the_breaker_does_not_touch_these():
    rp.note_reply("u", "s", "How are you doing?")
    # the person asked something: a question back is fine
    assert rp.feeling_turn_policy("im overwhelmed, what should i do?", "u", "s")[0] is False
    # not a feeling: the strict breaker is off
    assert rp.feeling_turn_policy("what is the capital of france", "u", "s") == (False, False)
    # a reply that is only a question is kept whole, even under the breaker
    assert rp.polish("What feels heaviest?", "x", True, strict_cap=True, stock=True) == "What feels heaviest?"
    # a first reply (previous did not end on a question) keeps its one question
    assert rp.polish(TWO_Q, "x", False, strict_cap=False, stock=True) == TWO_Q


def test_stock_line_alone_is_replaced_and_with_substance_is_dropped():
    assert rp.polish("I'm here to listen to whatever is on your mind.", "im overwhelmed", False, stock=True) == rp.STOCK_REPLACEMENT
    out = rp.polish("I'm here to listen. That sounds like a heavy week.", "im overwhelmed", False, stock=True)
    assert out == "That sounds like a heavy week."
    out = rp.polish("That sounds like a heavy week. Feel free to share more.", "im overwhelmed", False, stock=True)
    assert out == "That sounds like a heavy week."


def test_negative_control_stock_lines_are_only_touched_on_a_feeling_turn_and_when_they_are_the_whole_sentence():
    line = "I'm here to listen to whatever is on your mind."
    assert rp.polish(line, "hello there", False, stock=False) == line
    keep = "I'm here to listen, and that sounds really hard."
    assert rp.polish(keep, "im overwhelmed", False, stock=True) == keep


def test_stream_form_equals_batch_form_for_the_new_rules():
    text = "I'm here to listen. That sounds heavy. What feels worst? Is it the kids?"
    batch = rp.polish(text, "im overwhelmed", True, strict_cap=True, stock=True)
    for step in (1, 2, 3, 5, 7, 11):
        ps = rp.PolishStream(rp.closer_lexicon_for("im overwhelmed"), True, strict_cap=True, stock=True)
        out = "".join(ps.feed(text[i:i + step]) for i in range(0, len(text), step)) + ps.finish()
        assert out == batch, step


# ── 6. a stranger's remember / forget / who ──────────────────────────────────────────────────────────

def test_guest_remember_me_is_honest():
    r = ga.reply("hi. im quinn, remember me", "guest-abc")
    assert r == ga.REMEMBER_REPLY and "I'll remember" not in r and "haven't saved" in r and "learn your voice" in r


def test_guest_forget_me_and_who_are_honest():
    assert ga.reply("forget me", "guest") == ga.FORGET_REPLY
    assert ga.reply("do you know whos talking", "guest") == ga.WHO_REPLY
    assert ga.reply("who am i", "guest") == ""          # the identity card's question: answered upstream, not here


def test_negative_controls_members_and_other_asks_are_untouched():
    assert ga.reply("forget me", "demo_member") == ""             # a member's forget is the forget tier's
    assert ga.reply("remember me", "demo_member") == ""
    for t in ("remember to buy milk", "remember that my name is quinn", "remember me and also order milk", "forget what i said about shops"):
        assert ga.reply(t, "guest-abc") == "", t


def test_a_recognised_named_guest_is_not_told_they_are_not_remembered(monkeypatch):
    import named_guests

    monkeypatch.setattr(named_guests, "current", lambda: object())
    assert ga.reply("remember me", "guest-abc") == ""


def test_guest_tier_answers_before_any_router():
    import fast_tiers

    res = fast_tiers._qa7_conversation_tier("hi. im quinn, remember me", "guest-abc")
    assert res is not None and res.tier == "guest_asks" and "I'll remember" not in res.reply
    assert fast_tiers._qa7_conversation_tier("hi. im quinn, remember me", "demo_member") is None


# ── review round (Codex on #2368) ────────────────────────────────────────────────────────────────────

def test_a_run_of_trailing_questions_is_dropped_together_under_the_strict_breaker():
    out = rp.polish("That sounds heavy. What feels worst? Is it the kids?", "x", True, strict_cap=True, stock=True)
    assert out == "That sounds heavy."
    # negative control: a question followed by a statement is not "trailing" - kept; and non-strict behaviour is unchanged
    keep = "That sounds heavy. What feels worst? I'm glad you said it."
    assert rp.polish(keep, "x", True, strict_cap=True, stock=True) == keep
    assert rp.polish("That sounds heavy. What feels worst? Is it the kids?", "x", False) == "That sounds heavy. What feels worst? Is it the kids?"


def test_a_warm_acknowledgement_is_not_a_stock_line():
    ack = "I'm happy to hear you talked to your youngest."
    assert not rp.is_stock_line(ack)
    assert rp.polish(ack, "yeah", False, stock=True) == ack
    assert rp.is_stock_line("I'm here to listen.") and rp.is_stock_line("Feel free to share.")      # the real stock lines still are


def test_the_inherited_day_survives_a_bare_place_follow_up(monkeypatch):
    import fast_tiers

    seen = []

    async def fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen.append(text)
        return xd.DispatchResult(domain="weather", reply="Tomorrow in Albany: 18 degrees.", intent="weather")

    monkeypatch.setattr(xd, "dispatch", fake_dispatch)
    wf.note("u", "s", "weather in perth tomorrow")
    asyncio.run(fast_tiers._weather_place_tier("and albany", "u", "s", {}))
    asyncio.run(fast_tiers._weather_place_tier("and broome", "u", "s", {}))
    assert seen == ["what's the weather in albany tomorrow", "what's the weather in broome tomorrow"]
