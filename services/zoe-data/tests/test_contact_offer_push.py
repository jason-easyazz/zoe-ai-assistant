"""Proactive contact-offer panel push (ADR-contacts-production-hardening P4 follow-up).

When a new person_create offer is stored, the server can (flag-gated) enqueue a
person_confirm "Add {name}?" show_card to the user's foreground panel so it appears
without the user asking. OFF by default → no-op. The emit lives in the post-turn
memory pipeline (not the STT/brain/TTS hot path), so it's not replay-gated; enabling
it still needs live-kiosk verification (it surfaces a card on the panel).
"""
import contextlib

import pytest

pytestmark = pytest.mark.ci_safe

import latent_intent_detector as lid


def _sugg(name="Daniel", rel="brother"):
    return [{"action_type": "person_create", "pre_filled_slots": {"name": name, "relationship": rel}}]


async def _run(monkeypatch, suggestions):
    calls = []

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield object()

    async def fake_enqueue(db, **kw):
        calls.append(kw)
        return {"ok": True}

    import database
    import ui_orchestrator
    monkeypatch.setattr(database, "get_db_ctx", fake_ctx)
    monkeypatch.setattr(ui_orchestrator, "enqueue_ui_action", fake_enqueue)
    await lid._maybe_push_contact_offer_cards("u1", suggestions)
    return calls


@pytest.mark.asyncio
async def test_no_push_when_flag_off(monkeypatch):
    monkeypatch.delenv("ZOE_CONTACT_OFFER_PANEL_PUSH", raising=False)
    assert await _run(monkeypatch, _sugg()) == []


@pytest.mark.asyncio
async def test_pushes_person_confirm_card_when_flag_on(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_PANEL_PUSH", "1")
    calls = await _run(monkeypatch, _sugg("Daniel", "brother"))
    assert len(calls) == 1
    kw = calls[0]
    assert kw["user_id"] == "u1" and kw["action_type"] == "show_card"
    assert kw["idempotency_key"] == "contact_offer_u1_daniel_brother"
    payload = kw["payload"]
    assert payload["source"] == "contact_offer" and payload["name"] == "Daniel"
    assert payload["card"]["component"] == "person_confirm"
    assert payload["card"]["props"]["title"] == "Add Daniel as your brother?"


@pytest.mark.asyncio
async def test_only_person_create_offers_pushed(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_PANEL_PUSH", "1")
    mixed = [{"action_type": "list_add", "pre_filled_slots": {"item": "milk"}}] + _sugg("Fiona", "friend")
    calls = await _run(monkeypatch, mixed)
    assert len(calls) == 1 and calls[0]["payload"]["name"] == "Fiona"


@pytest.mark.asyncio
async def test_same_name_distinct_relationship_gets_distinct_keys(monkeypatch):
    # Two different people with the same name (brother Daniel + coworker Daniel)
    # must not collapse onto one idempotency key, or the ledger dedupes the second.
    monkeypatch.setenv("ZOE_CONTACT_OFFER_PANEL_PUSH", "1")
    two = _sugg("Daniel", "brother") + _sugg("Daniel", "coworker")
    calls = await _run(monkeypatch, two)
    keys = {c["idempotency_key"] for c in calls}
    assert keys == {"contact_offer_u1_daniel_brother", "contact_offer_u1_daniel_coworker"}


@pytest.mark.asyncio
async def test_relationless_offer_key_has_no_trailing_underscore(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_PANEL_PUSH", "1")
    calls = await _run(monkeypatch, [{"action_type": "person_create", "pre_filled_slots": {"name": "Sam"}}])
    assert calls[0]["idempotency_key"] == "contact_offer_u1_sam"


@pytest.mark.asyncio
async def test_nameless_offer_skipped(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_PANEL_PUSH", "1")
    assert await _run(monkeypatch, [{"action_type": "person_create", "pre_filled_slots": {"name": "  "}}]) == []
