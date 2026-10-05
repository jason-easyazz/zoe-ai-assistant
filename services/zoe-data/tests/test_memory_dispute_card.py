"""A `memory_dispute` offer ("earlier you told me X, I've just heard Y - which is right?") must
offer BOTH answers on the panel. Review of #1868: it rendered as the generic card with a single
`Save` action, i.e. only "yes, the new one" - the person had no way to keep the old fact.

Now `ui_components_for_suggestions` builds a dispute card: "Keep what I told you" on the existing
dismiss handler (rejects the held-back candidate, the old fact stands) and "Use the new one" on the
existing accept handler (approves it as the person's own). Both wired through the REAL handlers
with the dispute resolver stubbed. Synthetic data (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

import memory_disputes
import pending_suggestions
from pending_suggestions import ui_components_for_suggestions

pytestmark = pytest.mark.ci_safe

QUESTION = 'Earlier you told me "User is 40 years old" and I\'ve just heard "User is 41 years old" - which is right?'
SLOTS = {"candidate_id": "cand-1", "old_id": "old-1"}


def _card():
    (card,) = ui_components_for_suggestions([{
        "id": "sug-d", "action_type": "memory_dispute", "offer_phrase": QUESTION,
        "pre_filled_slots": SLOTS,
    }])
    return card


def test_the_dispute_card_offers_both_answers():
    card = _card()
    assert card["type"] == "action_card"
    assert "40 years old" in card["title"] and "41 years old" in card["title"] and "which is right" in card["title"]
    assert [(a["label"], a["action"], a["suggestion_id"]) for a in card["actions"]] == [
        ("Keep what I told you", "pending_suggestion_dismiss", "sug-d"),
        ("Use the new one", "pending_suggestion_accept", "sug-d"),
    ]


def test_other_offers_keep_the_generic_save_card():
    (card,) = ui_components_for_suggestions([{
        "id": "sug-x", "action_type": "list_add", "offer_phrase": "Add milk?", "pre_filled_slots": {}}])
    assert [a["action"] for a in card["actions"]] == ["pending_suggestion_accept"]


def test_an_empty_question_still_gets_a_titled_two_action_card():
    (card,) = ui_components_for_suggestions([{"id": "s", "action_type": "memory_dispute"}])
    assert card["title"] and len(card["actions"]) == 2


@pytest.fixture
def resolved(monkeypatch):
    calls = []

    async def resolve(user_id, candidate_id, *, accept, svc=None):
        calls.append((user_id, candidate_id, accept))
        return True

    monkeypatch.setattr(memory_disputes, "resolve", resolve)
    return calls


def test_keep_old_runs_the_dismiss_handler_which_rejects_the_candidate(monkeypatch, resolved):
    class _Db:
        async def fetch(self, sql, *a):
            return [{"id": "sug-d", "action_type": "memory_dispute", "pre_filled_slots": json.dumps(SLOTS)}]

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Db()

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", ctx)
    keep = _card()["actions"][0]
    assert keep["action"] == "pending_suggestion_dismiss"
    assert asyncio.run(pending_suggestions.mark_resolved(keep["suggestion_id"], "member-a")) is True
    assert resolved == [("member-a", "cand-1", False)]          # the old fact stands


def test_use_new_runs_the_accept_handler_which_approves_the_candidate(resolved):
    use_new = _card()["actions"][1]
    assert use_new["action"] == "pending_suggestion_accept"
    out = asyncio.run(pending_suggestions._execute_action(None, "memory_dispute", SLOTS, "member-a"))
    assert out == {"candidate_id": "cand-1", "applied": True}
    assert resolved == [("member-a", "cand-1", True)]
