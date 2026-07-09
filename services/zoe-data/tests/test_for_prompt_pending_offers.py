"""P1 (ADR-contacts-production-hardening): pending `person_create` offers are
folded into the /api/memories/for-prompt packet so the flue brain can offer to
add them. Flag-gated (ZOE_PERSON_SUGGEST_ENABLED); OFF = byte-for-byte no-op.

Not `ci_safe`: importing routers.memories pulls memory_service — runs on the
self-hosted full-dir lane.
"""
import pytest

import pending_suggestions
import routers.memories as mem


def _base():
    return {"packet": "## What I know about you\n- likes tea [mem]", "refs": [], "count": 0}


@pytest.mark.asyncio
async def test_off_is_noop(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_SUGGEST_ENABLED", raising=False)
    before = _base()
    after = await mem._fold_pending_contact_offers(dict(before), "u1")
    assert after["packet"] == before["packet"]  # unchanged


@pytest.mark.asyncio
async def test_on_folds_offers(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")

    async def fake_list(user_id, *, limit=3):
        return [
            {"id": "s1", "name": "Tanika", "relationship": "niece", "offer_phrase": "add?"},
            {"id": "s2", "name": "Bob", "relationship": None, "offer_phrase": "add?"},
        ]
    monkeypatch.setattr(pending_suggestions, "list_pending_contacts", fake_list)

    out = await mem._fold_pending_contact_offers(_base(), "u1")
    pkt = out["packet"]
    assert "## People mentioned recently (not contacts yet)" in pkt
    assert "- Tanika (niece) [pending-contact]" in pkt
    assert "- Bob [pending-contact]" in pkt          # no "(None)" when relationship missing
    assert "people_create tool" in pkt               # tells the brain how to act on yes
    assert "## What I know about you" in pkt          # original packet preserved


@pytest.mark.asyncio
async def test_on_but_empty_is_noop(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")

    async def fake_empty(user_id, *, limit=3):
        return []
    monkeypatch.setattr(pending_suggestions, "list_pending_contacts", fake_empty)

    before = _base()
    after = await mem._fold_pending_contact_offers(dict(before), "u1")
    assert after["packet"] == before["packet"]
