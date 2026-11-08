"""write_intent_gate.py (ZOE_WRITE_INTENT_GATE): a write needs an ask. Synthetic phrasings only (ci_safe).

QA 2026-11-08: a planning request ("help me think through the logistics") made the brain call add_calendar_event three times; an unnamed
"add bolts to the list" went onto the household shopping list. Pinned here:
  * the verb detector: explicit write verbs allow, nouns ("my schedule", "the book") and planning talk do not;
  * NEGATIVE CONTROL: "add dinner friday 7pm" still writes (the gate allows it, end to end through /intent-dispatch);
  * a short affirmation / a short follow-up inside a write conversation is allowed; no noted turn => allowed (the gate only refuses on evidence);
  * the list rule: milk goes straight on, bolts ask; a named list is never gated; the router asks instead of defaulting to shopping;
  * the dispatch endpoint returns a 422 with the reason and never reaches execute_intent on a refusal.
"""
from __future__ import annotations

import asyncio

import pytest

import write_intent_gate as wig

pytestmark = pytest.mark.ci_safe

PLANNING = ("ok so thursday week i have a driving test for the eldest and netball on saturday morning, then an appointment the following "
            "tuesday. help me think through the logistics")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_WRITE_INTENT_GATE", raising=False)
    wig.reset_state()
    yield
    wig.reset_state()


@pytest.mark.parametrize("text,want", [
    ("add dinner friday 7pm", True),
    ("please put the dentist on the calendar for tuesday at 3", True),
    ("book the plumber for monday", True),
    ("schedule a catch up with sam on thursday", True),
    ("pencil in lunch on friday", True),
    ("can you create an event for the school concert", True),
    (PLANNING, False),
    ("help me plan my schedule for next week", False),
    ("what's on my schedule today", False),
    ("the schedule is crazy this week", False),
    ("help me put together a plan for saturday", False),
    ("that book is on the table", False),
])
def test_asks_to_write_calendar(text, want):
    assert wig.asks_to_write_calendar(text) is want


def test_planning_talk_never_writes_but_an_explicit_add_still_does():
    wig.note_turn("u1", PLANNING)
    reason = wig.check("u1", "calendar_create", {"title": "driving test", "date": "this Thursday"})
    assert reason and "talk it through" in reason
    # negative control: the same user, an explicit ask, same intent -> allowed
    wig.note_turn("u1", "add dinner friday 7pm")
    assert wig.check("u1", "calendar_create", {"title": "dinner", "date": "friday", "time": "7pm"}) is None


def test_affirmation_and_follow_up_are_allowed_but_only_after_an_ask():
    wig.note_turn("u1", PLANNING)
    assert wig.check("u1", "calendar_create", {"title": "x"})                      # blocked
    wig.note_turn("u1", "yes please")                                                # Zoe offered; the person agrees
    assert wig.check("u1", "calendar_create", {"title": "x"}) is None
    wig.reset_state()
    wig.note_turn("u2", "add dinner on friday")
    wig.note_turn("u2", "7pm")                                                       # the answer to "what time?"
    assert wig.check("u2", "calendar_create", {"title": "dinner"}) is None
    wig.reset_state()
    wig.note_turn("u3", "thursday at 7pm")                                           # a short line with NO earlier ask
    assert wig.check("u3", "calendar_create", {"title": "dinner"})


def test_no_noted_turn_means_allowed_and_off_means_allowed(monkeypatch):
    assert wig.check("nobody", "calendar_create", {"title": "x"}) is None
    wig.note_turn("u1", PLANNING)
    monkeypatch.setenv("ZOE_WRITE_INTENT_GATE", "off")
    assert wig.check("u1", "calendar_create", {"title": "x"}) is None


def test_shadow_logs_and_refuses_nothing(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_WRITE_INTENT_GATE", "shadow")
    wig.note_turn("u1", PLANNING)
    with caplog.at_level("INFO"):
        assert wig.check("u1", "calendar_create", {"title": "x"}) is None
    assert "WRITE_INTENT_GATE mode=shadow" in caplog.text


def test_other_intents_are_never_gated():
    wig.note_turn("u1", PLANNING)
    for intent in ("calendar_show", "reminder_create", "note_create", "list_show", "weather"):
        assert wig.check("u1", intent, {"title": "x"}) is None


@pytest.mark.parametrize("item,want", [
    ("milk", False), ("bread and butter", False), ("vegemite", False), ("nuts", False), ("batteries", False),
    ("bolts", True), ("steel cap boots", True), ("cable ties", True), ("insulation tape", True), ("the quote for the client", True),
])
def test_looks_non_grocery(item, want):
    assert wig.looks_non_grocery(item) is want


def test_unnamed_list_with_a_hardware_item_asks_but_milk_and_named_lists_go_through():
    wig.note_turn("u1", "add bolts to the list")
    assert "Which list should I put bolts on" in (wig.check("u1", "list_add", {"item": "bolts", "list_type": "shopping"}) or "")
    assert wig.check("u1", "list_add", {"item": "milk", "list_type": "shopping"}) is None                  # negative control
    wig.note_turn("u1", "add bolts to my work list")
    assert wig.check("u1", "list_add", {"item": "bolts", "list_type": "work"}) is None
    wig.note_turn("u1", "add bolts to the shopping list")
    assert wig.check("u1", "list_add", {"item": "bolts", "list_type": "shopping"}) is None                  # they said shopping
    wig.note_turn("u1", "we need bolts")
    assert wig.check("u1", "list_add", {"item": "bolts", "list_type": "shopping"}) is None                  # not an add-to-the-list ask


@pytest.mark.parametrize("text,asks", [
    ("add bolts to the list", True), ("add bolts to my list", True), ("add bolts", True), ("put cable ties on the list", True),
    ("add milk to the list", False), ("add milk", False), ("add bolts to the work list", False), ("add bolts to the shopping list", False),
])
def test_router_asks_which_list_instead_of_defaulting_to_shopping(text, asks):
    import intent_router as ir

    intent = ir.detect_intent(text, log_miss=False)
    assert intent is not None and intent.name == "list_add"
    assert bool(intent.slots.get("ask_list")) is asks
    if not asks:
        assert intent.slots.get("list_type")


def test_router_question_is_the_reply_and_nothing_is_written():
    import intent_router as ir

    out = asyncio.run(ir._execute_list_add_direct(ir.Intent("list_add", {"item": "bolts", "list_type": "", "ask_list": True}), "u1"))
    assert out == "Which list should I put bolts on - your shopping list or your personal list?"


def test_dispatch_endpoint_refuses_with_a_422_and_never_executes(monkeypatch):
    from fastapi import HTTPException

    from routers import system

    ran = []

    async def fake_execute(intent, user_id=None, **_):
        ran.append(intent.name)
        return "ok"

    import intent_router

    monkeypatch.setattr(intent_router, "execute_intent", fake_execute)
    wig.note_turn("u1", PLANNING)
    body = system._IntentDispatchBody(user_id="u1", intent="calendar_create", slots={"title": "driving test"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(system.intent_dispatch(body, None))
    assert ei.value.status_code == 422 and "talk it through" in str(ei.value.detail)
    assert ran == []
    # negative control: an explicit add goes through to the executor
    wig.note_turn("u1", "add dinner friday 7pm")
    out = asyncio.run(system.intent_dispatch(system._IntentDispatchBody(user_id="u1", intent="calendar_create", slots={"title": "dinner"}), None))
    assert out["ok"] is True and ran == ["calendar_create"]
