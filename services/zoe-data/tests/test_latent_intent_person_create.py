"""Phase 2a of contacts-from-known-people (ADR-contacts-from-known-people.md).

`latent_intent_detector.detect` gains a `person_create` suggestion: when a
message names a person who isn't a contact, offer to add them. This exercises the
DETERMINISTIC layer (parse + filter) by mocking the detector LLM (`_complete`);
the LLM itself is never called. Proves: emitted when the flag is on, dropped
(byte-for-byte no-op) when off, and dropped for a pronoun name even if the LLM
hands one back. Slim-dep → GitHub `-m ci_safe` lane.
"""
import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe

import latent_intent_detector as lid

USER = "demo_mention_user"  # a DEMO user — never a real person
SESSION = "sess-1"

# A synthetic LLM response naming a real person (offer_phrase left empty on
# purpose — the detector must supply the natural confirm prompt itself).
_PERSON_JSON = (
    '[{"action_type": "person_create", "description": "met Sarah",'
    ' "offer_phrase": "", "pre_filled_slots": {"name": "Sarah", "relationship": "friend"}}]'
)


def _mock_llm(monkeypatch, raw):
    async def _fake(prompt):
        return raw
    monkeypatch.setattr(lid, "_complete", _fake)


def _stub_intent_router(monkeypatch):
    """detect() consults intent_router first; stub it so nothing short-circuits."""
    mod = types.ModuleType("intent_router")
    mod.detect_intent = lambda text: None
    monkeypatch.setitem(sys.modules, "intent_router", mod)


def _no_dedup(monkeypatch):
    async def _fake(name, user_id):
        return False
    monkeypatch.setattr(lid, "_already_a_contact", _fake)


@pytest.mark.asyncio
async def test_emitted_when_flag_on(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    _stub_intent_router(monkeypatch)
    _mock_llm(monkeypatch, _PERSON_JSON)
    _no_dedup(monkeypatch)

    out = await lid.detect("I had lunch with Sarah today", user_id=USER, session_id=SESSION)

    assert len(out) == 1
    s = out[0]
    assert s["action_type"] == "person_create"
    assert s["pre_filled_slots"] == {"name": "Sarah", "relationship": "friend"}
    # Deterministic confirm prompt so the UI renders a confirm card.
    assert s["offer_phrase"] == "Add Sarah to your contacts?"


@pytest.mark.asyncio
async def test_dropped_when_flag_off(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_SUGGEST_ENABLED", raising=False)
    _stub_intent_router(monkeypatch)
    _mock_llm(monkeypatch, _PERSON_JSON)  # LLM offers one anyway…
    _no_dedup(monkeypatch)

    out = await lid.detect("I had lunch with Sarah today", user_id=USER, session_id=SESSION)

    assert out == []  # …but the bridge is dark — no-op when the flag is off


@pytest.mark.asyncio
async def test_dropped_for_pronoun_name(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    _stub_intent_router(monkeypatch)
    _mock_llm(
        monkeypatch,
        '[{"action_type": "person_create", "offer_phrase": "Add She?",'
        ' "pre_filled_slots": {"name": "She"}}]',
    )
    _no_dedup(monkeypatch)

    out = await lid.detect("I saw She at the park yesterday", user_id=USER, session_id=SESSION)

    assert out == []  # precision guard rejects the pronoun the LLM handed back


@pytest.mark.asyncio
async def test_existing_actions_unaffected_when_flag_on(monkeypatch):
    """A normal list_add still flows through unchanged with the flag on."""
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    _stub_intent_router(monkeypatch)
    _mock_llm(
        monkeypatch,
        '[{"action_type": "list_add", "offer_phrase": "Add milk to shopping?",'
        ' "list_type": "shopping", "pre_filled_slots": {"item": "milk"}}]',
    )

    out = await lid.detect("we are out of milk again", user_id=USER, session_id=SESSION)

    assert len(out) == 1
    assert out[0]["action_type"] == "list_add"
    assert out[0]["pre_filled_slots"]["item"] == "milk"
