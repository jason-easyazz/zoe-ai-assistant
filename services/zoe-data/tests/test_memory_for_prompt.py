"""Tests for the /api/memories/for-prompt packet endpoint (Brick 3 memory).

Covers the packet prompt-policy (compact, cited, current-over-superseded,
disputed-as-uncertain, dedup, capped) and the endpoint contract (internal-token
auth, fail-closed for guests, happy path).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import memory_service
import routers.memories as memories_mod
from memory_service import MemoryRef
from routers.memories import _build_memory_prompt_packet, router as memories_router


def _ref(mem_id: str, text: str, **meta) -> MemoryRef:
    score = meta.pop("score", 0.0)
    return MemoryRef(id=mem_id, text=text, metadata=meta, score=score)


# ── packet builder (the prompt-policy) ────────────────────────────────────

def test_packet_is_compact_and_cited():
    facts = [_ref("abc12345", "Jason prefers concise answers"), _ref("def67890", "Lives in Geraldton")]
    out = _build_memory_prompt_packet(facts, [])
    assert out["count"] == 2
    assert out["packet"].startswith("## What I know about you")
    assert "[mem:abc12345]" in out["packet"]
    assert "[mem:def67890]" in out["packet"]


def test_packet_drops_superseded_and_archived():
    facts = [
        _ref("1", "Old job title", status="superseded"),
        _ref("2", "Current job title", status="active"),
        _ref("3", "Old address", status="archived"),
    ]
    out = _build_memory_prompt_packet(facts, [])
    assert out["count"] == 1
    assert "Current job title" in out["packet"]
    assert "Old job title" not in out["packet"]
    assert "Old address" not in out["packet"]


def test_packet_marks_disputed_uncertain():
    out = _build_memory_prompt_packet([_ref("9", "Maybe allergic to nuts", status="disputed")], [])
    assert "(uncertain)" in out["packet"]


def test_packet_dedups_and_caps():
    facts = [_ref(str(i), f"fact {i}") for i in range(20)] + [_ref("0", "fact 0 dup")]
    out = _build_memory_prompt_packet(facts, [], max_facts=5)
    assert out["count"] == 5  # capped
    assert out["packet"].count("[mem:") == 5


def test_packet_search_hits_lead():
    facts = [_ref("f1", "general fact")]
    hits = [_ref("h1", "relevant to the question")]
    out = _build_memory_prompt_packet(facts, hits)
    # the search hit appears before the general fact
    assert out["packet"].index("relevant to the question") < out["packet"].index("general fact")
    assert out["refs"][0]["from_search"] is True


def test_packet_empty_when_no_memories():
    out = _build_memory_prompt_packet([], [])
    assert out == {"packet": "", "refs": [], "count": 0}


# ── endpoint (auth + fail-closed + happy path) ────────────────────────────

class _FakeSvc:
    def __init__(self, facts=None, hits=None):
        self._facts = facts or []
        self._hits = hits or []

    async def load_for_prompt(self, user_id, *, limit=20):
        return self._facts

    async def search(self, q, *, user_id, limit=10):
        return self._hits


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(memories_router)
    return app


def test_endpoint_requires_internal_token(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).get("/api/memories/for-prompt", params={"user_id": "family-admin"})
    assert resp.status_code == 403


def test_endpoint_fails_closed_for_guest(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memories_mod, "_svc", lambda: _FakeSvc(facts=[_ref("x", "should not appear")]))
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: True)
    resp = TestClient(_app()).get(
        "/api/memories/for-prompt",
        params={"user_id": "guest"},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"packet": "", "refs": [], "count": 0, "user_scoped": False}


def test_endpoint_happy_path(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: False)
    monkeypatch.setattr(
        memories_mod,
        "_svc",
        lambda: _FakeSvc(facts=[_ref("abc12345", "Jason prefers concise answers")]),
    )
    resp = TestClient(_app()).get(
        "/api/memories/for-prompt",
        params={"user_id": "family-admin", "message": "what do you know about me"},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_scoped"] is True
    assert body["count"] == 1
    assert "[mem:abc12345]" in body["packet"]
