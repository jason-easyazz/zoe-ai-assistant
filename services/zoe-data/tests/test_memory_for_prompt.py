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

pytestmark = pytest.mark.ci_safe


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


# ── conflict-aware recency presentation (live bug 2026-07-07) ─────────────
#
# The packet listed stale facts ("sister … Katie", twice) ABOVE the newer
# correction ("sister named Kate"), so the brain answered with the superseded
# value. Relevance stays the selector; conflicting bullets present newest-first.

def test_packet_conflicting_facts_present_newest_first():
    # Verbatim phrasings from the live packet that defeated the correction.
    facts = [
        _ref("old00001", "My sister's name is Katie", added_at="2026-06-01T10:00:00Z"),
        _ref("old00002", "User's sister is named Katie", added_at="2026-06-02T10:00:00Z"),
        _ref("new00001", "User has a sister named Kate", added_at="2026-07-06T10:00:00Z"),
    ]
    out = _build_memory_prompt_packet(facts, [])
    assert out["count"] == 3  # contradiction is reordered, never collapsed
    packet = out["packet"]
    assert packet.index("Kate ") < packet.index("Katie") or \
        packet.index("[mem:new00001]") < packet.index("[mem:old00002]")
    # Newest leads the group; both stale siblings follow, themselves newest-first.
    assert [r["id"] for r in out["refs"]] == ["new00001", "old00002", "old00001"]


def test_packet_conflict_order_is_timestamp_driven():
    # Same rows, timestamps swapped: the OTHER value must lead — proves the
    # reorder keys on added_at, not on list position.
    facts = [
        _ref("a1", "User lives in Geraldton", added_at="2026-07-06T10:00:00Z"),
        _ref("b1", "User lives in Perth", added_at="2026-06-01T10:00:00Z"),
    ]
    out = _build_memory_prompt_packet(facts, [])
    assert [r["id"] for r in out["refs"]] == ["a1", "b1"]  # already newest-first: unchanged
    facts_swapped = [
        _ref("a1", "User lives in Geraldton", added_at="2026-06-01T10:00:00Z"),
        _ref("b1", "User lives in Perth", added_at="2026-07-06T10:00:00Z"),
    ]
    out = _build_memory_prompt_packet(facts_swapped, [])
    assert [r["id"] for r in out["refs"]] == ["b1", "a1"]  # correction floated up


def test_packet_no_conflicts_locks_current_order():
    # Golden fixture: distinct facts (with timestamps that would invert the
    # order if a global newest-first sort ever crept in) keep selection order
    # and content byte-for-byte.
    facts = [
        _ref("f1", "Jason prefers concise answers", added_at="2026-01-01T00:00:00Z"),
        _ref("f2", "Lives in Geraldton", added_at="2026-07-01T00:00:00Z"),
    ]
    hits = [_ref("h1", "My dad's name is Neil", added_at="2026-03-01T00:00:00Z")]
    out = _build_memory_prompt_packet(facts, hits)
    assert out["packet"] == (
        "## What I know about you\n"
        "(These stored memories are authoritative and current. If anything "
        "said earlier in this conversation conflicts with them — including "
        "your own earlier replies that information was unknown or not on "
        "file — trust these memories and answer from them.)\n"
        "- My dad's name is Neil [mem:h1]\n"
        "- Jason prefers concise answers [mem:f1]\n"
        "- Lives in Geraldton [mem:f2]"
    )
    assert [r["id"] for r in out["refs"]] == ["h1", "f1", "f2"]


def test_packet_undated_conflicts_keep_selection_order():
    # No added_at anywhere → nothing to rank by; stable sort keeps the
    # relevance-selected order (also the legacy-metadata safety net).
    facts = [_ref("g1", "User lives in Geraldton"), _ref("p1", "User lives in Perth")]
    out = _build_memory_prompt_packet(facts, [])
    assert [r["id"] for r in out["refs"]] == ["g1", "p1"]


def test_packet_newer_fact_outranks_conflicting_stale_hit():
    # Cross-section conflict: a stale SEARCH HIT vs a newer general-fact
    # correction. Recency wins the group — the correction takes the hit's slot
    # (this was the live failure mode: relevance-ranked stale value shadowing
    # the correction). from_search flags stay truthful per ref.
    hits = [_ref("stale001", "User's sister is named Katie",
                 added_at="2026-06-01T10:00:00Z")]
    facts = [_ref("fresh001", "User has a sister named Kate",
                  added_at="2026-07-06T10:00:00Z")]
    out = _build_memory_prompt_packet(facts, hits)
    assert [r["id"] for r in out["refs"]] == ["fresh001", "stale001"]
    assert out["refs"][0]["from_search"] is False
    assert out["refs"][1]["from_search"] is True


def test_packet_richer_superset_is_not_a_conflict():
    # A strict token-subset pair is enrichment, not a contradiction — the newer
    # richer line must NOT jump above the older short one.
    facts = [
        _ref("s1", "My dad's name is Neil", added_at="2026-01-01T00:00:00Z"),
        _ref("s2", "My dad's name is Neil and he lives in Geraldton",
             added_at="2026-07-01T00:00:00Z"),
    ]
    out = _build_memory_prompt_packet(facts, [])
    assert [r["id"] for r in out["refs"]] == ["s1", "s2"]


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


def test_packet_carries_denial_echo_authority_rule():
    """The authority rule must travel WITH the facts: in a long-lived session the
    model's own earlier denials outvote the packet on retries (live 2026-07-12)
    unless the packet explicitly outranks prior conversation. Header stays the
    first line (consumers pin startswith)."""
    out = _build_memory_prompt_packet([_ref("m1", "My dad's name is Neil")], [])
    packet = out["packet"]
    assert packet.startswith("## What I know about you\n")
    assert "authoritative and current" in packet
    assert "trust these memories" in packet
    assert "Neil" in packet  # facts still present after the rule line
    # No facts → no packet, and therefore no free-floating authority rule.
    empty = _build_memory_prompt_packet([], [])
    assert empty["packet"] == ""
