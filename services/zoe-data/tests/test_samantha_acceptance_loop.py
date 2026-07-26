"""Samantha acceptance — the "tell it in the morning, reference it in the afternoon" loop.

This is the §1 acceptance bar for the memory rebuild (see
`docs/architecture/zoe-memory-samantha-buildplan.md`), reduced to a single
self-contained, CI-runnable case against the MERGED increment-1 code:

    live  → a morning conversation is stored verbatim (per-turn user in metadata)
    idle  → the conversation goes quiet; the idle-consolidation sweep runs
    store → durable facts are extracted over the WHOLE exchange, gated, and ingested
    recall→ a LATER (afternoon) session surfaces the durable fact; junk is absent

The companion `services/zoe-core/test/test_samantha_acceptance.py` proves the same
intent against the live Pi + model server, but skips without the Orin. THIS test
needs no live box: the Gemma extractor and Postgres are mocked, but the loop runs
the *real* engine (`memory_idle_consolidation`), the *real* write-quality gate
(`memory_quality.is_storable_fact`), and a faithful in-memory store + recall so a
regression in any of those — owner resolution, idle selection, gating, store, or
recall — turns this red.

Mirrors the mocking style of `tests/test_memory_idle_consolidation.py`.

    python -m pytest services/zoe-data/tests/test_samantha_acceptance_loop.py -v
"""
import pytest
import asyncio

import memory_idle_consolidation as mic
import memory_quality

pytestmark = pytest.mark.ci_safe


def _run(c):
    return asyncio.run(c)


class _Row(dict):
    """Behaves like an asyncpg Record for our subscript access."""


class _FakeConn:
    """Serves the consolidation engine its turns and records watermark writes."""

    def __init__(self, turns):
        self._turns = turns
        self.executed = []

    async def fetch(self, q, *a):
        return self._turns

    async def execute(self, q, *a):
        self.executed.append((q, a))


def _ctx_factory(conn):
    """A get_ctx() replacement that hands `conn` to each short-lived `async with`.

    The pool-decoupling refactor made consolidate_session open its own short-lived
    connections (read transcript, write watermark) instead of receiving one — so
    tests inject the fake conn through this factory rather than positionally.
    """
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    return lambda: _Ctx()


class _FakeMemoryStore:
    """A faithful-enough stand-in for MemoryService: what consolidation ingests is
    exactly what a later recall can return, scoped per user. No Chroma, no DB."""

    def __init__(self):
        self.rows = []  # list of dicts: {id, text, user_id}

    async def search(self, query, *, user_id, limit=3):
        # Used by _ingest_or_supersede's dedup probe; substring match is enough
        # to exercise the supersede path without a vector index.
        q = (query or "").lower()
        hits = [
            type("Ref", (), {"id": r["id"], "text": r["text"]})()
            for r in self.rows
            if r["user_id"] == user_id and any(w in r["text"].lower() for w in q.split())
        ]
        return hits[:limit]

    async def ingest(self, **kw):
        self.rows.append(
            {"id": f"m{len(self.rows)}", "text": kw["text"], "user_id": kw["user_id"]}
        )
        return self.rows[-1]["id"]

    def recall(self, user_id):
        """Afternoon read: the facts this user would see in a later session."""
        return [r["text"] for r in self.rows if r["user_id"] == user_id]


def _wire(monkeypatch, store, gemma_facts):
    """Wire the engine's collaborators: stub Gemma extraction + the store, keep the
    REAL quality gate, and route _ingest_or_supersede at a plain store.ingest()."""
    import memory_digest
    import memory_service
    import expert_dispatch

    async def _fake_extract(_transcript):
        return list(gemma_facts)

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _fake_extract)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: store)

    async def _ingest(svc, text, **kw):
        # The engine calls _ingest_or_supersede; we collapse to a plain store write
        # (dedup behaviour is covered by expert_dispatch's own tests).
        await svc.ingest(text=text, user_id=kw["user_id"], source=kw.get("source"))

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _ingest)


def test_morning_fact_recalled_in_the_afternoon(monkeypatch):
    """The full Samantha loop on the merged engine: a fact told in a morning session
    is consolidated on idle and recalled in a later session; a question is gated out.

    The owner is NOT carried on the session (it stays 'guest', mirroring prod) — it
    must be resolved from the per-turn metadata, which is exactly what 1b added."""
    # ── MORNING: a real authenticated exchange (session stays 'guest', user in meta)
    morning = [
        _Row(role="user", content="My dad's name is Neil",
             metadata='{"user_id": "jason"}', at="2026-06-24T08:00:00+00:00"),
        _Row(role="assistant", content="Got it — I'll remember that.",
             metadata='{"user_id": "jason"}', at="2026-06-24T08:00:04+00:00"),
        _Row(role="user", content="do you remember my mum's name?",
             metadata='{"user_id": "jason"}', at="2026-06-24T08:00:09+00:00"),
    ]
    conn = _FakeConn(morning)
    store = _FakeMemoryStore()

    # Gemma sees the WHOLE exchange and returns a real fact + a question that slipped
    # through extraction (so the REAL gate has something to reject).
    _wire(monkeypatch, store, gemma_facts=[
        {"fact": "Jason's dad's name is Neil"},
        {"fact": "Do you remember my mum's name?"},
    ])

    # ── IDLE → STORE: the caller passes 'guest' (the session fallback); the engine
    # must resolve the owner to jason from per-turn metadata and consolidate.
    stored = _run(mic.consolidate_session("sess-morning", "guest", get_ctx=_ctx_factory(conn)))

    assert stored == 1, "exactly the durable fact should be stored; the question gated out"
    assert conn.executed, "the consolidation watermark must be advanced"

    # ── AFTERNOON: a later session recalls jason's facts.
    recalled = store.recall("jason")
    assert recalled == ["Jason's dad's name is Neil"], recalled
    assert not any("?" in r for r in recalled), "no question/junk leaked into memory"

    # Cross-user isolation: nobody else sees jason's fact.
    assert store.recall("alice") == []


def test_real_quality_gate_rejects_junk_in_the_loop(monkeypatch):
    """Belt-and-braces: the gate wired here is the REAL one, not a stub — confirm it
    rejects a question and a trivial filler line while keeping the real fact."""
    assert memory_quality.is_storable_fact("My dad's name is Neil")[0] is True
    assert memory_quality.is_storable_fact("do you remember my mum's name?")[0] is False

    turns = [
        _Row(role="user", content="I work as a paramedic in Geraldton",
             metadata='{"user_id": "jason"}', at="2026-06-24T08:00:00+00:00"),
        _Row(role="assistant", content="Noted.",
             metadata='{"user_id": "jason"}', at="2026-06-24T08:00:03+00:00"),
    ]
    store = _FakeMemoryStore()
    _wire(monkeypatch, store, gemma_facts=[
        {"fact": "Jason works as a paramedic in Geraldton"},
        {"fact": "ok"},                      # trivial filler
        {"fact": "what should I have for dinner?"},  # a question
    ])

    stored = _run(mic.consolidate_session("sess-2", "guest", get_ctx=_ctx_factory(_FakeConn(turns))))
    recalled = store.recall("jason")
    assert recalled == ["Jason works as a paramedic in Geraldton"], recalled
    assert stored == 1


def test_guest_only_session_stores_nothing(monkeypatch):
    """No resolvable real owner (no metadata, guest session) → the loop writes nothing
    and never even calls Gemma. We must never write a fact under 'guest'."""
    turns = [
        _Row(role="user", content="hello there", metadata=None, at="2026-06-24T08:00:00+00:00"),
        _Row(role="assistant", content="hi", metadata=None, at="2026-06-24T08:00:03+00:00"),
    ]
    store = _FakeMemoryStore()
    called = {"extract": False}

    import memory_digest
    import memory_service

    async def _fake_extract(_t):
        called["extract"] = True
        return [{"fact": "should never be stored"}]

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _fake_extract)
    # Wire the store into the engine so an *accidental* store write (e.g. if the
    # no-owner guard regressed) would actually land in store.rows and be caught by
    # the recall assertion below — otherwise that assertion is vacuously true.
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: store)

    stored = _run(mic.consolidate_session("sess-guest", "guest", get_ctx=_ctx_factory(_FakeConn(turns))))
    assert stored == 0
    assert called["extract"] is False, "must skip before extraction when no real owner"
    assert store.rows == [], "no fact may be written for a guest-only session"
    assert store.recall("jason") == []
