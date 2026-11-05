"""QA round 2: a short affirmative + clock time answering Zoe's own pending REMINDER offer is bound to that action
(not the time fast tier), flag-gated, TTL-bound; plus the AI-honesty prompt content and drift probe.

Pure logic: no DB, no pool. Neutral fixtures only.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

import intent_router
import persona_drift as pd
import persona_layer as pl
from intent_router import bind_reminder_offer_reply, reminder_offer_reply_text

pytestmark = pytest.mark.ci_safe

OFFER = "School finishes mid-afternoon. Want me to set a reminder for the pickup?"
FRESH = 30.0


@pytest.mark.parametrize("reply,expected", [
    ("yes please, 2:45 so i'm not late", "remind me about pickup at 2:45 pm"),
    ("Yes 2:45", "remind me about pickup at 2:45 pm"),
    ("sure, 3pm", "remind me about pickup at 3 pm"),
    ("yeah 8:15 am please", "remind me about pickup at 8:15 am"),
])
def test_affirmative_plus_slot_binds_to_the_offered_reminder(reply, expected):
    assert bind_reminder_offer_reply(reply, OFFER, FRESH) == expected


def test_rewrite_reaches_the_reminder_intent_with_the_slot():
    text = bind_reminder_offer_reply("yes please, 2:45 so i'm not late", OFFER, FRESH)
    intent = intent_router.detect_intent(text, log_miss=False)
    assert intent is not None and intent.name == "reminder_create"


@pytest.mark.parametrize("reply", [
    "what time is it",                      # negative control: no offer shape -> the time fast tier still owns it
    "what's the time at 2:45",
    "2:45",                                 # no affirmative opener
    "yes",                                  # no slot
    "yes please, 7",                        # bare number is not a clock time
    "no thanks, 2:45",                      # refusal
    "yes what's the weather like tomorrow in the city this week",  # unrelated + too long / no time
])
def test_unrelated_or_incomplete_replies_are_not_hijacked(reply):
    assert bind_reminder_offer_reply(reply, OFFER, FRESH) is None


def test_no_pending_offer_never_binds():
    assert bind_reminder_offer_reply("yes please, 2:45", "It's sunny today.", FRESH) is None
    assert bind_reminder_offer_reply("yes please, 2:45", None, FRESH) is None
    # an offer that is not a reminder offer is not this binding's business
    assert bind_reminder_offer_reply("yes 2:45", "Want me to read you the headlines?", FRESH) is None


def test_offer_expires_and_unknown_age_fails_closed():
    assert bind_reminder_offer_reply("yes 2:45", OFFER, 601.0) is None
    assert bind_reminder_offer_reply("yes 2:45", OFFER, None) is None
    assert bind_reminder_offer_reply("yes 2:45", OFFER, 599.0) is not None


def test_flag_dark_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_OFFER_FOLLOWUP_BINDING", raising=False)
    assert asyncio.run(reminder_offer_reply_text("yes 2:45", "u", previous=OFFER, age_s=FRESH)) is None
    monkeypatch.setenv("ZOE_OFFER_FOLLOWUP_BINDING", "1")
    assert asyncio.run(reminder_offer_reply_text("yes 2:45", "u", previous=OFFER, age_s=FRESH)) is not None
    # flag on, no offer pending: the time question is untouched
    assert asyncio.run(reminder_offer_reply_text("what time is it", "u", previous=OFFER, age_s=FRESH)) is None


# ── AI honesty ─────────────────────────────────────────────────────────────────────────
def test_doctrine_source_states_ai_honesty():
    src = (pathlib.Path(__file__).resolve().parents[3] / "labs/flue-zoe-brain-2x/src/doctrines.ts").read_text()
    assert "you are an AI assistant, a computer program, not a person" in src
    assert "never dodge" in src
    # negative control: the prompt must not script a denial
    assert "I\\'m not a robot" not in src and "I'm not a robot" not in src


@pytest.mark.parametrize("reply,ok", [
    ("Yes, I'm an AI assistant. I'm Zoe.", True),
    ("I'm Zoe, a computer helper you can talk to - not a person, and I'm happy to be your friend.", True),
    ("As an AI language model, I can't.", False),     # the corporate register stays flagged
    ("I am Gemma, a model trained by Google.", False),
])
def test_drift_probe_honest_replies_keep_the_persona_register(reply, ok):
    assert pd.style_checks(reply, pl.default_persona())["no_selfid"] is ok
