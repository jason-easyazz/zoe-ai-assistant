"""QA review F5c: off-panel accept/dismiss of a surfaced contact offer.

After Zoe voices "Would you like me to add Caitlin as a contact?", a Telegram
user answers in plain text — there is no confirm card. The intent router must
map a short affirmative onto `pending_suggestions.execute_suggestion` (the same
sanctioned path the panel card uses) and a short refusal onto `mark_resolved`.

Pure-logic + monkeypatched pending_suggestions: no DB, no pool.
"""
from __future__ import annotations

import pytest

import pending_suggestions
from intent_router import (
    _match_pending_offer_reply,
    _offer_reply_kind,
    Intent,
    execute_intent,
)

pytestmark = pytest.mark.ci_safe

USER = "demo_offer_reply_user"  # a DEMO user — never a real person

_OFFER = {
    "id": "sugg-1",
    "name": "Caitlin",
    "relationship": "friend",
    "offer_phrase": "Add Caitlin to your contacts?",
}


# ── Shape matcher ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "yes", "Yes!", "yeah", "yep", "sure", "ok", "okay", "yes please",
    "yes add her", "sure, add her", "go ahead", "do it", "yes add Caitlin",
    "save her as a contact", "absolutely", "yes please do",
])
def test_affirmatives_accept(text):
    assert _offer_reply_kind(text, "Caitlin") == "accept"


@pytest.mark.parametrize("text", [
    "no", "No.", "nah", "nope", "no thanks", "don't", "don't add her",
    "no don't save her", "not now", "no thank you",
])
def test_negatives_dismiss(text):
    assert _offer_reply_kind(text, "Caitlin") == "dismiss"


@pytest.mark.parametrize("text", [
    "",                                   # empty
    "yes let's book the flight",          # affirmative opener, other content
    "add milk to the shopping list",      # add-opener but non-filler content
    "sure I was thinking about dinner tonight with everyone at the house",  # too long
    "what's the weather",                 # no yes/no opener
    "she is allergic to nuts",            # fact statement
])
def test_non_replies_fall_through(text):
    assert _offer_reply_kind(text, "Caitlin") is None


def test_affirm_opener_with_negation_is_dismiss():
    assert _offer_reply_kind("ok don't", "Caitlin") == "dismiss"
    assert _offer_reply_kind("yes not now", "Caitlin") == "dismiss"


# ── Router matcher (flag + surfaced-offer guards) ─────────────────────────────

@pytest.mark.asyncio
async def test_match_requires_flag(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_SUGGEST_ENABLED", raising=False)

    async def _boom(user_id, *, limit=5):  # pragma: no cover — must not be reached
        raise AssertionError("offer lookup must be skipped when the flag is off")

    monkeypatch.setattr(pending_suggestions, "surfaced_person_offers", _boom)
    assert await _match_pending_offer_reply("yes", USER) is None


@pytest.mark.asyncio
async def test_match_requires_surfaced_offer(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")

    async def _none(user_id, *, limit=5):
        return []

    monkeypatch.setattr(pending_suggestions, "surfaced_person_offers", _none)
    assert await _match_pending_offer_reply("yes", USER) is None


@pytest.mark.asyncio
async def test_match_binds_offer(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")

    async def _offers(user_id, *, limit=5):
        return [dict(_OFFER)]

    monkeypatch.setattr(pending_suggestions, "surfaced_person_offers", _offers)

    accept = await _match_pending_offer_reply("yes add her", USER)
    assert accept is not None and accept.name == "pending_offer_accept"
    assert accept.slots["suggestion_id"] == "sugg-1"
    assert accept.slots["name"] == "Caitlin"

    dismiss = await _match_pending_offer_reply("no thanks", USER)
    assert dismiss is not None and dismiss.name == "pending_offer_dismiss"

    # Even with a live offer, unrelated content must not be hijacked.
    assert await _match_pending_offer_reply("yes let's plan the trip", USER) is None


@pytest.mark.asyncio
async def test_match_multiple_offers_binding(monkeypatch):
    """With several surfaced offers: a bare 'yes' binds to the OLDEST offer
    (the question the fold asked FIRST, never the newest); a reply naming a
    person binds to that offer; a name matching several offers falls through."""
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    offers = [
        {"id": "sugg-old", "name": "Marcus", "relationship": "brother",
         "offer_phrase": "Add Marcus to your contacts?"},
        {"id": "sugg-new", "name": "Caitlin", "relationship": "friend",
         "offer_phrase": "Add Caitlin to your contacts?"},
    ]

    async def _offers(user_id, *, limit=5):
        return [dict(o) for o in offers]

    monkeypatch.setattr(pending_suggestions, "surfaced_person_offers", _offers)

    bare = await _match_pending_offer_reply("yes", USER)
    assert bare is not None and bare.slots["suggestion_id"] == "sugg-old"

    named = await _match_pending_offer_reply("yes add Caitlin", USER)
    assert named is not None and named.slots["suggestion_id"] == "sugg-new"

    # The named token matching MULTIPLE offers is ambiguous — fall through.
    offers[0]["name"] = "Caitlin Rose"
    assert await _match_pending_offer_reply("yes add Caitlin", USER) is None


# ── execute_intent handlers ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_accept_runs_execute_suggestion(monkeypatch):
    calls = []

    async def _exec(suggestion_id, user_id):
        calls.append((suggestion_id, user_id))
        return {"ok": True, "action": "person_create", "result": {"created": True}}

    monkeypatch.setattr(pending_suggestions, "execute_suggestion", _exec)
    intent = Intent("pending_offer_accept", {"suggestion_id": "sugg-1", "name": "Caitlin"})
    reply = await execute_intent(intent, USER)
    assert calls == [("sugg-1", USER)]
    assert "Caitlin" in reply and "added" in reply


@pytest.mark.asyncio
async def test_execute_accept_failure_is_polite(monkeypatch):
    async def _exec(suggestion_id, user_id):
        return {"ok": False, "error": "not_found"}

    monkeypatch.setattr(pending_suggestions, "execute_suggestion", _exec)
    intent = Intent("pending_offer_accept", {"suggestion_id": "gone", "name": "Caitlin"})
    reply = await execute_intent(intent, USER)
    assert "couldn't save Caitlin" in reply


@pytest.mark.asyncio
async def test_execute_dismiss_marks_resolved(monkeypatch):
    calls = []

    async def _resolve(suggestion_id, user_id):
        calls.append((suggestion_id, user_id))
        return True

    monkeypatch.setattr(pending_suggestions, "mark_resolved", _resolve)
    intent = Intent("pending_offer_dismiss", {"suggestion_id": "sugg-1", "name": "Caitlin"})
    reply = await execute_intent(intent, USER)
    assert calls == [("sugg-1", USER)]
    assert "won't save Caitlin" in reply
