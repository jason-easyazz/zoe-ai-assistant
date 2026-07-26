"""Tests for the real Skybridge timer: parsing, classify, resolve, store, contract."""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from card_contract import validate_component  # noqa: E402
from skybridge_service import (  # noqa: E402
    _parse_timer_duration,
    _parse_timer_label,
    _timers,
    _TimerStore,
    active_timers_for,
    cancel_timer_by_id,
    classify_skybridge_intent,
    create_timer_direct,
    resolve_skybridge_request,
)

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _isolate_timer_store():
    # The store is a process-global singleton; reset it around every test.
    _timers._by_owner.clear()
    yield
    _timers._by_owner.clear()


def _t(text: str) -> str:
    return f" {text.lower()} "


# ── duration parsing ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("set a 5 minute timer", 300),
    ("10 min timer", 600),
    ("set a timer for 90 seconds", 90),
    ("two minute timer", 120),
    ("set a 1 hour timer", 3600),
    ("set a timer for 1 minute 30 seconds", 90),
    ("set a 3 minute timer called pasta", 180),
    ("timer for the eggs", 0),          # no duration → caller defaults
    ("set a timer", 0),
])
def test_parse_duration(text, expected):
    assert _parse_timer_duration(_t(text)) == expected


@pytest.mark.parametrize("text,expected", [
    ("set a 3 minute timer called pasta", "Pasta"),
    ("timer for the eggs", "Eggs"),
    ("set a 5 minute timer", ""),           # plain duration, no label
    ("set a timer for 10 minutes", ""),     # 'for <duration>' is not a label
])
def test_parse_label(text, expected):
    assert _parse_timer_label(_t(text)) == expected


# ── classify ──────────────────────────────────────────────────────────────────
def test_classify_create_cancel_status():
    create = classify_skybridge_intent("set a 5 minute timer")
    assert create.domain == "timer" and create.action == "create" and create.duration_seconds == 300
    assert classify_skybridge_intent("cancel the timer").action == "cancel"
    assert classify_skybridge_intent("stop my timer").action == "cancel"
    assert classify_skybridge_intent("how long left on my timer").action == "status"
    # A passing mention of "timer" must NOT create one (the phantom-timer bug:
    # a mis-transcribed "It's not the timer." was spinning up a 5-min timer).
    assert classify_skybridge_intent("It's not the timer.") is None
    assert classify_skybridge_intent("the timer went off") is None
    assert classify_skybridge_intent("I don't need the timer") is None
    assert classify_skybridge_intent("10 minute timer").action == "create"


def test_timer_not_misread_as_calendar():
    intent = classify_skybridge_intent("set a 5 minute timer")
    assert intent.domain == "timer"  # not 'calendar'


# ── store ─────────────────────────────────────────────────────────────────────
def test_store_create_list_cancel_and_prune():
    store = _TimerStore()
    a = store.create("u", "Eggs", 60)
    b = store.create("u", "Pasta", 30)
    assert {t["label"] for t in store.list("u")} == {"Eggs", "Pasta"}
    # cancel by name
    assert store.cancel("u", "eggs")["label"] == "Eggs"
    assert [t["label"] for t in store.list("u")] == ["Pasta"]
    # cancel with no name → soonest-expiring
    assert store.cancel("u") is not None
    assert store.list("u") == []
    # prune drops expired timers
    store.create("u", "Past", 60)
    store._by_owner["u"][0]["expires_at"] = time.time() - 1
    assert store.list("u") == []


# ── resolve ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolve_create_returns_conforming_running_card():
    result = await resolve_skybridge_request("set a 5 minute timer for pasta", "guest")
    assert result["handled"] is True
    card = result["cards"][0]
    assert card["component"] == "timer"
    props = card["props"]
    assert props["label"] == "Pasta" and props["status"] == "running"
    assert props["duration_seconds"] == 300
    # absolute expiry ~5 min out so the panel can tick accurately
    assert 290 <= (props["expires_at_ms"] / 1000) - time.time() <= 301
    validate_component(card)  # passes the convergence gate
    assert "pasta timer is set for 5 minutes" in result["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_resolve_bare_timer_defaults_to_five_minutes():
    result = await resolve_skybridge_request("set a timer", "guest")
    assert result["cards"][0]["props"]["duration_seconds"] == 300


@pytest.mark.asyncio
async def test_multiple_timers_all_returned():
    user = "kitchen"
    first = await resolve_skybridge_request("set a 10 minute timer for pasta", user)
    assert len(first["cards"]) == 1
    second = await resolve_skybridge_request("set a 3 minute timer for eggs", user)
    # creating a second timer returns BOTH, soonest first
    labels = [c["props"]["label"] for c in second["cards"]]
    assert labels == ["Eggs", "Pasta"]
    assert "2 timers running" in second["spoken_summary"]
    assert len(active_timers_for(user)) == 2


@pytest.mark.asyncio
async def test_cancel_one_keeps_the_others():
    user = "kitchen2"
    await resolve_skybridge_request("set a 10 minute timer for pasta", user)
    await resolve_skybridge_request("set a 3 minute timer for eggs", user)
    # bare cancel removes the soonest (eggs); pasta keeps running and is shown
    result = await resolve_skybridge_request("cancel the timer", user)
    assert result["timer_cancelled_id"]
    remaining = [c["props"]["label"] for c in result["cards"]]
    assert remaining == ["Pasta"]
    assert "eggs" in result["spoken_summary"].lower()


def test_cancel_by_id_helper():
    user = "byid"
    t = _timers.create(user, "Oven", 600)
    assert cancel_timer_by_id(user, "nope") is None
    assert cancel_timer_by_id(user, t["timer_id"])["label"] == "Oven"
    assert active_timers_for(user) == []


@pytest.mark.asyncio
async def test_resolve_status_and_cancel_roundtrip():
    user = "timer-rt-user"
    await resolve_skybridge_request("set a 9 minute timer for soup", user)
    status = await resolve_skybridge_request("how long left on my timer", user)
    assert status["cards"][0]["component"] == "timer"
    assert "soup timer has" in status["spoken_summary"].lower()

    cancel = await resolve_skybridge_request("cancel the timer", user)
    assert cancel["timer_cancelled_id"]  # panel reconciles on this
    assert "cancelled" in cancel["spoken_summary"].lower()

    after = await resolve_skybridge_request("how long left on my timer", user)
    assert "no timers" in after["spoken_summary"].lower()


# ── create_timer_direct (the non-voice intent-dispatch path) ──────────────────
def test_create_timer_direct_registers_real_timer():
    """The intent-dispatch path (no Skybridge classifier) must register a real,
    poll-able timer in the shared store — not just speak a confirmation."""
    user = "dispatch-user"
    resolved = create_timer_direct(user, minutes=3, label="Pasta")
    assert resolved["spoken_summary"]
    timers = active_timers_for(user)
    assert len(timers) == 1
    assert timers[0]["label"] == "Pasta"
    assert timers[0]["duration_seconds"] == 180


def test_create_timer_direct_defaults_bad_minutes_to_five():
    user = "dispatch-user"
    resolved = create_timer_direct(user, minutes="soon", label="")
    assert resolved["spoken_summary"]
    timers = active_timers_for(user)
    assert len(timers) == 1
    assert timers[0]["duration_seconds"] == 300
