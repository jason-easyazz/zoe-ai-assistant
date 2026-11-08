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
    "yes, what time is it at 2:45?",        # a question carrying a time is not a slot
    "yeah when does it start 2:45",
])
def test_unrelated_or_incomplete_replies_are_not_hijacked(reply):
    assert bind_reminder_offer_reply(reply, OFFER, FRESH) is None


def test_no_pending_offer_never_binds():
    assert bind_reminder_offer_reply("yes please, 2:45", "It's sunny today.", FRESH) is None
    assert bind_reminder_offer_reply("yes please, 2:45", None, FRESH) is None
    # an offer that is not a reminder offer is not this binding's business
    assert bind_reminder_offer_reply("yes 2:45", "Want me to read you the headlines?", FRESH) is None


def test_only_the_final_offer_counts():
    mixed = "I set a reminder for pickup. Want me to play some music?"
    assert bind_reminder_offer_reply("yes, 2:45", mixed, FRESH) is None


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_k):
        return _Cur(self._rows)


def _patch_db(monkeypatch, rows):
    import contextlib

    import database

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Db(rows)

    monkeypatch.setattr(database, "get_db_ctx", ctx)


def _row(role, content):
    return {"role": role, "content": content, "created_at": "2099-01-01 00:00:00+00"}


def test_an_intervening_user_turn_unbinds_the_offer(monkeypatch):
    # newest first: [user "ok thanks", assistant offer] -> the user spoke after the offer -> no binding
    _patch_db(monkeypatch, [_row("user", "ok thanks"), _row("assistant", OFFER)])
    prev, age = asyncio.run(intent_router._previous_assistant_turn("u", "yes 2:45"))
    assert prev is None and age is None


def test_the_current_turn_saved_already_is_skipped_once(monkeypatch):
    _patch_db(monkeypatch, [_row("user", "yes 2:45"), _row("assistant", OFFER)])
    prev, _age = asyncio.run(intent_router._previous_assistant_turn("u", "yes 2:45"))
    assert prev == OFFER


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


# -- review round (Codex #2309) ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply", [
    "sure, tomorrow at 3pm", "yes every Monday at 9am", "yes 3pm on friday", "yeah next week 3pm", "yes in 20 minutes at 3pm",
    "sure 3pm on the 14th",
])
def test_scheduling_qualifiers_the_rewrite_would_drop_are_not_bound(reply):
    """The rewritten sentence cannot carry a day or recurrence; the extractor would put the reminder on TODAY. Decline instead."""
    assert bind_reminder_offer_reply(reply, OFFER, FRESH) is None
    assert bind_reminder_offer_reply("sure, 3pm", OFFER, FRESH) is not None          # negative control: no qualifier binds


def test_the_title_comes_from_the_final_offer_sentence_only():
    prev = "I set a reminder for the dentist. Want me to set a reminder for the pickup?"
    assert bind_reminder_offer_reply("sure, 3pm", prev, FRESH) == "remind me about pickup at 3 pm"


def test_the_transcript_lookup_is_scoped_to_the_current_session(monkeypatch):
    seen = {}

    class _Cur:
        async def fetchall(self):
            return [_row("assistant", OFFER)]

    class _Db:
        async def execute(self, sql, params=()):
            seen["sql"], seen["params"] = sql, params
            return _Cur()

    import contextlib

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Db()

    import database
    monkeypatch.setattr(database, "get_db_ctx", ctx)
    prev, _age = asyncio.run(intent_router._previous_assistant_turn("u", "yes 2:45", "sess-B"))
    assert "m.session_id = ?" in seen["sql"] and seen["params"] == ("u", "sess-B") and prev == OFFER
    asyncio.run(intent_router._previous_assistant_turn("u", "yes 2:45"))
    assert "m.session_id" not in seen["sql"].split("WHERE", 1)[1] and seen["params"] == ("u",)   # control: no session id = as before


def test_the_shared_fast_tier_dispatch_defers_an_offer_reply(monkeypatch):
    """Both channels call fast_tiers.resolve; it must hand an offer reply back (None) instead of dispatching it to the time expert."""
    import fast_tiers

    async def bound(text, uid, previous=None, age_s=None, session_id=None):
        return "remind me about pickup at 2:45 pm" if text.startswith("yes please") else None

    monkeypatch.setattr(intent_router, "reminder_offer_reply_text", bound)
    assert asyncio.run(fast_tiers.resolve("yes please, 2:45 so i'm not late", "u", "s1", channel="chat")) is None


def test_a_remind_you_offer_binds_with_its_task_and_an_unrecoverable_task_declines():
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to remind you to call the dentist?", FRESH) == \
        "remind me to call the dentist at 3 pm"
    # the offer is a reminder offer but names no task: never a reminder about "that"
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to set a reminder?", FRESH) is None
    assert bind_reminder_offer_reply("sure, 3pm", OFFER, FRESH) == "remind me about pickup at 3 pm"      # control


# -- review round 3 (Codex #2309) ---------------------------------------------------------------------------------------------
def test_both_offer_cues_are_judged_on_the_final_sentence():
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to play music? I set a reminder for pickup.", FRESH) is None
    assert bind_reminder_offer_reply("sure, 3pm", "I set a reminder for the dentist. Want me to set a reminder for the pickup?",
                                     FRESH) == "remind me about pickup at 3 pm"                    # control


def test_a_task_containing_a_plain_at_keeps_its_full_text():
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to remind you to look at the invoice?", FRESH) == \
        "remind me to look at the invoice at 3 pm"
    assert bind_reminder_offer_reply("sure, 4pm", "Want me to set a reminder to call the dentist?", FRESH) == \
        "remind me to call the dentist at 4 pm"                                                    # control


def test_an_offer_that_carries_its_own_day_or_time_is_not_rewritten():
    for offer in ("Want me to set a reminder for pickup at 3pm tomorrow?", "Want me to set a reminder for pickup tomorrow?",
                  "Want me to set a reminder for pickup at 3pm?"):
        assert bind_reminder_offer_reply("sure, 4pm", offer, FRESH) is None, offer


def test_competing_clock_times_in_the_reply_decline():
    assert bind_reminder_offer_reply("sure 3pm is too early, 4pm please", OFFER, FRESH) is None
    assert bind_reminder_offer_reply("sure, 4pm please", OFFER, FRESH) == "remind me about pickup at 4 pm"      # control: one slot


@pytest.mark.parametrize("reply", [
    "add a reminder to call the dentist at 3pm",     # Codex on #2309: a NEW command that opens with an affirmative word
    "please add a reminder to call the dentist at 3pm",
    "yes add a reminder to buy milk at 5pm",
    "sure, not 3pm",                                 # the named time is the one being refused
    "ok but not at 3pm",
    "yes please, never before 9am",
])
def test_standalone_commands_and_negated_times_are_not_bound(reply):
    assert bind_reminder_offer_reply(reply, OFFER, FRESH) is None


@pytest.mark.parametrize("reply", ["yes please, 2:45 so i'm not late", "sure, at 3pm", "ok go ahead at 3pm thanks", "yes 3pm works"])
def test_a_plain_acceptance_with_a_time_still_binds(reply):
    # negative control for the two guards above: they must not stop a real acceptance
    assert bind_reminder_offer_reply(reply, OFFER, FRESH) is not None


def test_a_purpose_clause_never_hides_distress_from_the_binding():
    # Codex P1 on #2309: "sure 3pm because ..." binds as a reminder and the crisis handling never runs
    assert bind_reminder_offer_reply("sure 3pm because i want to kill myself", OFFER, FRESH) is None
    # negative control: an ordinary purpose clause still binds
    assert bind_reminder_offer_reply("sure 3pm because i keep forgetting", OFFER, FRESH) is not None


@pytest.mark.asyncio
async def test_the_chat_path_runs_the_distress_tier_before_deferring_an_offer_reply(monkeypatch):
    import fast_tiers

    order: list = []

    async def distress(text, user_id, kwargs):
        order.append("distress")
        return "CRISIS"

    async def deferred(text, user_id, session_id=None):
        order.append("offer")
        return "remind me about pickup at 3 pm"

    monkeypatch.setattr(fast_tiers, "_distress_tier", distress)
    import intent_router

    monkeypatch.setattr(intent_router, "reminder_offer_reply_text", deferred)
    out = await fast_tiers._resolve_tiers("sure 3pm", "jason", "s1")
    assert out == "CRISIS" and order == ["distress"]


@pytest.mark.parametrize("offer", [
    "Want me to move the reminder for pickup?",          # Codex on #2309: not an offer to CREATE a reminder
    "Shall I cancel the reminder for pickup?",
    "Want me to reschedule the pickup reminder?",
    "Should I delete that reminder?",
])
def test_an_offer_to_change_a_reminder_is_not_bound_as_a_new_one(offer):
    assert bind_reminder_offer_reply("sure, 3pm", offer, FRESH) is None


def test_an_offer_to_create_a_reminder_still_binds():
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to set a reminder for the pickup?", FRESH) is not None
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to remind you to collect the parcel?", FRESH) is not None


def test_a_period_inside_the_offered_task_is_kept():
    # Codex on #2309: only a period followed by whitespace / the end closes the sentence
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to remind you to email support@example.com?", FRESH) == \
        "remind me to email support@example.com at 3 pm"
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to remind you to buy 1.5 litres of milk?", FRESH) == \
        "remind me to buy 1.5 litres of milk at 3 pm"
    # negative control: an ordinary sentence end still closes the title
    assert bind_reminder_offer_reply("sure, 3pm", "Want me to remind you to call the dentist.", FRESH) == \
        "remind me to call the dentist at 3 pm"
