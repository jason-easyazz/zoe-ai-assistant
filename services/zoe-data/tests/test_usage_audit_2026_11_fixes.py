"""Fixes from the 2026-11-02 usage audit (docs/knowledge/usage-audit-2026-11-02.md). Synthetic phrases only.

* A nameless "what's on the list" reads the populated list instead of answering a bare count.
* A smart-home command that swallowed the sentences spoken after it, or whose device noun was misheard, still resolves.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import skybridge_service  # noqa: E402
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request  # noqa: E402
from test_skybridge_service import FakeDb  # noqa: E402

pytestmark = pytest.mark.ci_safe


def _lists_db(*, populated: tuple[str, ...] = ("shopping",)):
    rows, items = [], {}
    for kind in ("shopping", "personal", "work"):
        rows.append({"id": f"{kind}-1", "user_id": "member-a", "name": kind.title(), "list_type": kind,
                     "description": "", "visibility": "family"})
        items[f"{kind}-1"] = (
            [{"id": f"{kind}-i{n}", "list_id": f"{kind}-1", "text": word, "completed": False}
             for n, word in enumerate(("apples", "rice", "tea") if kind != "work" else ("stapler", "paper", "pens"))]
            if kind in populated else []
        )
    return FakeDb(lists=rows, items_by_list=items)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["What's on the list?", "Tell me what's on the list.", "What's on my list now?"])
async def test_nameless_list_read_names_the_items(phrase):
    result = await resolve_skybridge_request(phrase, "member-a", db=_lists_db())
    spoken = result["spoken_summary"]
    assert "apples" in spoken and "rice" in spoken and "tea" in spoken
    assert "has 3 items" not in spoken


@pytest.mark.asyncio
async def test_nameless_list_read_with_two_populated_lists_prefers_shopping():
    result = await resolve_skybridge_request("What's on the list?", "member-a", db=_lists_db(populated=("shopping", "work")))
    assert "apples" in result["spoken_summary"] and "stapler" not in result["spoken_summary"]


@pytest.mark.asyncio
async def test_nameless_list_read_negative_controls():
    # Overview ("my lists") is still the count summary, and two populated non-shopping lists stay a count (no guess).
    overview = await resolve_skybridge_request("Show my lists", "member-a", db=_lists_db())
    assert "apples" not in overview["spoken_summary"]
    ambiguous = await resolve_skybridge_request("What's on the list?", "member-a", db=_lists_db(populated=("personal", "work")))
    assert "apples" not in ambiguous["spoken_summary"]


def test_only_populated_list_helper():
    assert skybridge_service._only_populated_list([{"item_count": 0}, {"item_count": 2, "name": "a"}])["name"] == "a"
    assert skybridge_service._only_populated_list([{"item_count": 0}]) is None


@pytest.mark.parametrize("phrase,action", [
    ("Turn on the light. Some unrelated sentence the room said. Another one here.", "turn_on"),
    ("Turn off the lights! The narrator kept talking about ships.", "turn_off"),
    ("Turn on the light.Some unrelated sentence the room said.", "turn_on"),
])
def test_home_command_ignores_sentences_spoken_after_it(phrase, action):
    intent = classify_skybridge_intent(phrase)
    assert intent is not None and intent.domain == "smart_home" and intent.action == action
    assert intent.query in ("light", "lights")


def test_home_runoff_negative_control(monkeypatch):
    # With the sentence repair bypassed the device query swallows the trailing talk - the failure that was live.
    monkeypatch.setattr(skybridge_service, "_classify_home", lambda raw, text: skybridge_service._classify_home_parse(raw, text))
    intent = classify_skybridge_intent("Turn on the light. Some unrelated sentence the room said.")
    assert intent is not None and "." in (intent.query or "")


@pytest.mark.parametrize("phrase,action,noun", [
    ("Turn on the watch.", "turn_on", "light"),
    ("Turned off the lies.", "turn_off", "lights"),
    ("Switch on the lite", "turn_on", "light"),
    ("Turn off the lice", "turn_off", "lights"),
    ("Flip on the lye", "turn_on", "light"),
])
def test_misheard_light_noun_in_a_switch_frame(phrase, action, noun):
    intent = classify_skybridge_intent(phrase)
    assert intent is not None and intent.domain == "smart_home" and intent.action == action and intent.query == noun


def test_misheard_noun_repair_is_frame_bound():
    # Not a switch frame: the words are left alone and nothing becomes a smart-home command.
    for phrase in ("I lost my watch yesterday", "Tell me the lies they told", "What time does the watch say"):
        intent = classify_skybridge_intent(phrase)
        assert intent is None or intent.domain != "smart_home"


def test_garbled_question_is_not_a_list_item():
    from intent_router import detect_intent
    from list_service import is_plausible_item

    assert not is_plausible_item("all the what")
    assert not is_plausible_item("the which")
    assert not is_plausible_item("what")
    assert is_plausible_item("whole wheat bread")      # negative control: ordinary items still pass
    got = detect_intent("Get all the what?")
    assert got is None or got.name != "list_add"
    bought = detect_intent("Get some milk")
    assert bought is not None and bought.name == "list_add"


@pytest.mark.parametrize("phrase", [
    "I already turned off the lies.",
    "Don't turn off the watch.",
    "He said to turn on the watch.",
])
def test_mishear_repair_never_turns_a_statement_or_negation_into_a_command(phrase):
    intent = classify_skybridge_intent(phrase)
    assert intent is None or intent.domain != "smart_home"


@pytest.mark.parametrize("phrase", ["Can you turn on the watch", "Hey Zoe, turned off the lies", "Please switch on the lite"])
def test_mishear_repair_still_covers_polite_affirmative_frames(phrase):
    intent = classify_skybridge_intent(phrase)
    assert intent is not None and intent.domain == "smart_home"


def test_later_sentence_that_names_the_device_keeps_the_qualifier():
    intent = classify_skybridge_intent("Turn on the light. The bedroom light.")
    assert intent is not None and intent.action == "turn_on" and "bedroom" in (intent.query or "")


def test_later_sentence_that_retracts_the_command_is_not_acted_on():
    for phrase in ("Turn off the lights. Actually, don't.", "Turn on the light. Never mind."):
        intent = classify_skybridge_intent(phrase)
        assert intent is None or intent.domain != "smart_home" or intent.action not in ("turn_on", "turn_off")
