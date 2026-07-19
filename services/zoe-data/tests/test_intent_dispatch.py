"""Tests for /api/system/intent-dispatch (Brick 4b — zoe-core ability dispatch)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import intent_router
from routers.system import router as system_router

pytestmark = pytest.mark.ci_safe


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(system_router)
    return app


def test_requires_internal_token(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch", json={"user_id": "u", "intent": "list_show", "slots": {}}
    )
    assert resp.status_code == 403


def test_rejects_non_allowlisted_intent(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "u", "intent": "rm_rf", "slots": {}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400
    assert "not dispatchable" in resp.json()["detail"]


def test_requires_user_id(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "", "intent": "list_show", "slots": {}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400


def test_happy_path_runs_allowlisted_intent(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    captured = {}

    async def fake_execute_intent(intent, user_id="family-admin"):
        captured["intent"] = intent.name
        captured["slots"] = dict(intent.slots)
        captured["user_id"] = user_id
        return "Here's your shopping list: milk, eggs."

    monkeypatch.setattr(intent_router, "execute_intent", fake_execute_intent)
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "family-admin", "intent": "list_show", "slots": {"list_type": "shopping"}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "shopping list" in body["result"]
    assert captured == {
        "intent": "list_show",
        "slots": {"list_type": "shopping"},
        "user_id": "family-admin",
    }


def test_memory_store_is_dispatchable(monkeypatch):
    """Wave 3 (cut-list record §3): memory_store must be on the allowlist and
    reach execute_intent — the endpoint no longer rejects it as non-dispatchable."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    captured = {}

    async def fake_execute_intent(intent, user_id="family-admin"):
        captured["intent"] = intent.name
        captured["slots"] = dict(intent.slots)
        captured["user_id"] = user_id
        return "Got it — I'll remember that."

    monkeypatch.setattr(intent_router, "execute_intent", fake_execute_intent)
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "family-admin", "intent": "memory_store",
              "slots": {"text": "my anniversary is June 3rd"}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert captured == {
        "intent": "memory_store",
        "slots": {"text": "my anniversary is June 3rd"},
        "user_id": "family-admin",
    }


@pytest.mark.asyncio
async def test_memory_store_intent_ingests_fact(monkeypatch):
    """execute_intent(memory_store) routes through MemoryService.ingest with a
    fact memory_type and a stable idempotency key, returning the confirmation."""
    import memory_service

    calls = {}

    class _FakeRef:
        text = "my anniversary is June 3rd"

    class _FakeSvc:
        async def ingest(self, text, **kwargs):
            calls["text"] = text
            calls["kwargs"] = kwargs
            return _FakeRef()

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    out = await intent_router.execute_intent(
        intent_router.Intent("memory_store", {"text": "my anniversary is June 3rd"}),
        user_id="family-admin",
    )
    assert out == "Got it — I'll remember that."
    assert calls["text"] == "my anniversary is June 3rd"
    assert calls["kwargs"]["memory_type"] == "fact"
    assert calls["kwargs"]["confidence"] == 0.85
    assert calls["kwargs"]["source"] == "brain_tool"
    assert calls["kwargs"]["user_turn_id"].startswith("fact-")


@pytest.mark.asyncio
async def test_memory_store_empty_text_is_a_noop(monkeypatch):
    """Empty text never reaches the store and never claims a write."""
    import memory_service

    def _boom():
        raise AssertionError("get_memory_service must not be called for empty text")

    monkeypatch.setattr(memory_service, "get_memory_service", _boom)
    out = await intent_router.execute_intent(
        intent_router.Intent("memory_store", {"text": "   "}),
        user_id="family-admin",
    )
    assert "nothing to remember" in out.lower()


@pytest.mark.asyncio
async def test_memory_store_dropped_write_does_not_claim_success(monkeypatch):
    """ingest returning None (PII reject / dedup / opt-out) must not be reported
    as a successful 'I'll remember that'."""
    import memory_service

    class _FakeSvc:
        async def ingest(self, text, **kwargs):
            return None

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    out = await intent_router.execute_intent(
        intent_router.Intent("memory_store", {"text": "something"}),
        user_id="family-admin",
    )
    assert "couldn't save" in out.lower()


@pytest.mark.asyncio
async def test_memory_store_emotional_moment_threads_type_and_metadata(monkeypatch):
    """memory_store with memory_type=emotional_moment + valence/intensity threads
    the type through and passes valence/intensity via the ingest metadata dict (the
    store has no columns for them). This is the substrate side of the emotional-thread
    handoff (docs/architecture/zoe-memory-emotional-thread-handoff.md)."""
    import memory_service

    calls = {}

    class _FakeRef:
        text = "Jason has been anxious about the house settlement"

    class _FakeSvc:
        async def ingest(self, text, **kwargs):
            calls["text"] = text
            calls["kwargs"] = kwargs
            return _FakeRef()

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    out = await intent_router.execute_intent(
        intent_router.Intent(
            "memory_store",
            {
                "text": "Jason has been anxious about the house settlement",
                "memory_type": "emotional_moment",
                "valence": "neg",
                "intensity": 0.8,
            },
        ),
        user_id="family-admin",
    )
    assert out == "Got it — I'll remember that."
    assert calls["text"] == "Jason has been anxious about the house settlement"
    assert calls["kwargs"]["memory_type"] == "emotional_moment"
    # emotional_moment stays a touch more conservative than an explicit fact.
    assert calls["kwargs"]["confidence"] == 0.8
    # valence + intensity ride the metadata dict (→ candidate_valence / candidate_intensity).
    assert calls["kwargs"]["metadata"] == {"valence": "neg", "intensity": 0.8}
    # Emotional moments carry the 'emotional' tag and a namespaced idempotency key.
    assert "emotional" in calls["kwargs"]["tags"]
    assert calls["kwargs"]["user_turn_id"].startswith("emo-")


@pytest.mark.asyncio
async def test_memory_store_ignores_junk_valence_and_intensity(monkeypatch):
    """Malformed valence / out-of-range or non-finite intensity is dropped, never
    stored. An emotional_moment with only junk emotional metadata sends metadata=None."""
    import memory_service

    calls = {}

    class _FakeRef:
        text = "Jason is happy"

    class _FakeSvc:
        async def ingest(self, text, **kwargs):
            calls["kwargs"] = kwargs
            return _FakeRef()

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    out = await intent_router.execute_intent(
        intent_router.Intent(
            "memory_store",
            {
                "text": "Jason is happy",
                "memory_type": "emotional_moment",
                "valence": "elated",   # not one of pos|neg|mixed → dropped
                "intensity": 7.5,        # out of 0..1 → dropped
            },
        ),
        user_id="family-admin",
    )
    assert out == "Got it — I'll remember that."
    assert calls["kwargs"]["memory_type"] == "emotional_moment"
    # No valid emotional metadata survived, so metadata is None (not a junk dict).
    assert calls["kwargs"]["metadata"] is None


@pytest.mark.asyncio
async def test_memory_store_default_type_is_fact_with_no_metadata(monkeypatch):
    """remember_fact behaviour is unchanged: no memory_type slot → fact, metadata None."""
    import memory_service

    calls = {}

    class _FakeRef:
        text = "my anniversary is June 3rd"

    class _FakeSvc:
        async def ingest(self, text, **kwargs):
            calls["kwargs"] = kwargs
            return _FakeRef()

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    await intent_router.execute_intent(
        intent_router.Intent("memory_store", {"text": "my anniversary is June 3rd"}),
        user_id="family-admin",
    )
    assert calls["kwargs"]["memory_type"] == "fact"
    assert calls["kwargs"]["confidence"] == 0.85
    assert calls["kwargs"]["metadata"] is None
    assert calls["kwargs"]["user_turn_id"].startswith("fact-")


@pytest.mark.asyncio
async def test_memory_store_unknown_memory_type_falls_back_to_fact(monkeypatch):
    """memory_type is caller-controlled, so an unknown type must NOT reach the store —
    it falls back to the safe 'fact' default (allowlist guard against injected types)."""
    import memory_service

    calls = {}

    class _FakeRef:
        text = "something"

    class _FakeSvc:
        async def ingest(self, text, **kwargs):
            calls["kwargs"] = kwargs
            return _FakeRef()

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    await intent_router.execute_intent(
        intent_router.Intent(
            "memory_store",
            {"text": "something", "memory_type": "note; DROP TABLE"},
        ),
        user_id="family-admin",
    )
    assert calls["kwargs"]["memory_type"] == "fact"
    assert calls["kwargs"]["confidence"] == 0.85
    assert calls["kwargs"]["user_turn_id"].startswith("fact-")
