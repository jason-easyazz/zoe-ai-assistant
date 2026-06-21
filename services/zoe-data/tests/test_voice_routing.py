"""Unit tests for the Tier-1.5 voice routing / fact logic.

These pin the regressions fixed in the router PR — all pure (no DB, no GPU, no
saved audio), so they run in CI unlike the live replay harness:

- _plan must not turn a SHOW query ("what's on my calendar") into a CREATE write.
- A bare add ("milk on the shopping list") must still default to CREATE.
- A misheard/question fragment must defer to the brain, not invent an event.
- store_fact's _QUESTION_RE must treat questions as recall, not store them.
- _echo_fact must read a stored fact back in second person.
- _calendar_qualifier must parse the date scope so it isn't lost.
"""
import os

import pytest

# expert_dispatch imports lightly (re/os/time + lazy intent_router); skip cleanly
# if the service package can't be imported in this environment.
xd = pytest.importorskip("expert_dispatch")


class TestPlanShowVsCreate:
    def test_calendar_show_is_read_not_write(self):
        intent, _slots, kind = xd._plan("calendar", "what's on my calendar today")
        assert intent == "calendar_show"
        assert kind == "read"

    def test_calendar_show_this_week_is_read(self):
        intent, _slots, kind = xd._plan("calendar", "what's on my calendar this week")
        assert intent == "calendar_show" and kind == "read"

    def test_explicit_add_is_write(self):
        intent, _slots, kind = xd._plan("lists", "add milk to the shopping list")
        assert intent == "list_add" and kind == "write"

    def test_bare_item_defaults_to_create(self):
        intent, _slots, kind = xd._plan("lists", "milk on the shopping list")
        assert intent == "list_add" and kind == "write"

    def test_misheard_question_fragment_defers_to_brain(self):
        # No create verb + a question word → must NOT become a bogus create.
        assert xd._plan("calendar", "what? calendar items are on my calendar today") is None
        assert xd._plan("calendar", "i was asking about this week for my calendar") is None


class TestQuestionDetection:
    @pytest.mark.parametrize("q", [
        "what's my mum's name",
        "what are my sisters names",
        "who are my neighbours",
        "how many sisters do I have",
        "where are my keys",
        "when is mum's birthday",
    ])
    def test_questions_match(self, q):
        assert xd._QUESTION_RE.search(q), f"{q!r} should be detected as a question (recall)"

    @pytest.mark.parametrize("s", [
        "my mum's name is janice",
        "my dad's birthday is the 17th of the 11th 1947",
        "remember i parked on level three",
    ])
    def test_statements_do_not_match(self, s):
        assert not xd._QUESTION_RE.search(s), f"{s!r} is a statement, must not be a question"


class TestEchoFact:
    def test_second_person_swap(self):
        assert xd._echo_fact("My mum's name is Janice.") == "your mum's name is Janice"

    def test_strips_leading_remember(self):
        assert xd._echo_fact("remember that my wifi password is bluebird") == \
            "your wifi password is bluebird"

    def test_i_am_becomes_you_are(self):
        assert xd._echo_fact("I'm allergic to nuts") == "you're allergic to nuts"


class TestCalendarQualifier:
    @pytest.mark.parametrize("text,expected", [
        ("what's on my calendar today", "today"),
        ("do I have anything on tomorrow", "tomorrow"),
        ("what's on this week", "this week"),
        ("anything on this month", "this month"),
        ("am I free", ""),
    ])
    def test_qualifier_parsed(self, text, expected):
        assert xd._calendar_qualifier(text) == expected


class TestCalendarSpeechHelpers:
    def test_say_clock(self):
        ir = pytest.importorskip("intent_router")
        assert ir._say_clock("15:00") == "3 PM"
        assert ir._say_clock("09:30") == "9:30 AM"
        assert ir._say_clock("") == ""

    def test_spoken_day_today(self):
        import datetime
        ir = pytest.importorskip("intent_router")
        assert ir._spoken_day(datetime.date.today().isoformat()) == "today"
