"""Increment 2c — point the voice recall packet at the SAME composed memory
source chat uses (zoe_memory_compose), behind ZOE_MEMORY_COMPOSE_ENABLED
(default OFF).

Everything is synthetic: MemoryService.search is faked (mock embeddings) and the
relational store is a seeded in-memory fake reached through db_pool.get_db_ctx —
no DB, no model loads.

Key invariants proven here:
  * flag OFF ⇒ _voice_recall_packet output identical to today (golden snapshot);
  * flag ON + a person/relationship/date query ⇒ voice packet includes the cited
    relational facts (people/relationship/date/portrait);
  * flag ON + a non-relational query ⇒ router gate keeps relational OUT (vector-only);
  * no cross-user leakage; approved-only + soft-deleted excluded; guest fails closed.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

import pytest

import db_pool
import memory_service
import routers.voice_tts as v
import zoe_memory_compose as compose_mod

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Ref:
    text: str
    id: str = "id"
    metadata: dict = field(default_factory=dict)
    score: float = 0.0


# ── Fake MemoryService (mock embeddings) ─────────────────────────────────────


def _patch_search(monkeypatch, refs):
    class _FakeSvc:
        async def search(self, query, *, user_id, limit=10, timeout_s=2.0):
            return list(refs)

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: u == "guest")


# ── Seeded relational store (same shape as test_memory_compose_packet) ───────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, *, people, relationships, dates, portraits):
        self.people = people
        self.relationships = relationships
        self.dates = dates
        self.portraits = portraits

    def execute(self, sql, params):
        uid = params[0]
        s = sql.lower()
        if "from people" in s and "person_relationships" not in s:
            rows = [
                p for p in self.people
                if p.get("deleted", 0) == 0
                and (p.get("visibility") == "family" or p.get("user_id") == uid)
                and (p.get("is_partial", 0) in (0, None))
            ]
            return _FakeCursor([{k: v for k, v in p.items()} for p in rows])
        if "person_relationships" in s:
            rows = [r for r in self.relationships if r.get("user_id") == uid]
            return _FakeCursor(rows)
        if "person_important_dates" in s:
            rows = [d for d in self.dates if d.get("user_id") == uid]
            return _FakeCursor(rows)
        if "user_portraits" in s:
            txt = self.portraits.get(uid)
            return _FakeCursor([[txt]] if txt else [])
        raise AssertionError(f"unexpected SQL: {sql}")


def _seed_db() -> _FakeDB:
    return _FakeDB(
        people=[
            {"id": "p1", "user_id": "jason", "name": "Neil", "relationship": "father",
             "circle": "inner", "context": "personal", "notes": "loves fishing",
             "visibility": "personal", "deleted": 0, "is_partial": 0,
             "last_contacted_at": None},
            {"id": "p2", "user_id": "jason", "name": "Sara", "relationship": "sister",
             "circle": "inner", "context": "personal", "notes": "",
             "visibility": "personal", "deleted": 0, "is_partial": 0,
             "last_contacted_at": None},
            # Cross-user row — must NEVER surface for jason.
            {"id": "p9", "user_id": "eve", "name": "SecretEve", "relationship": "partner",
             "circle": "inner", "context": "personal", "notes": "private",
             "visibility": "personal", "deleted": 0, "is_partial": 0,
             "last_contacted_at": None},
            # Soft-deleted — excluded.
            {"id": "p3", "user_id": "jason", "name": "Ghost", "relationship": "friend",
             "notes": "", "visibility": "personal", "deleted": 1, "is_partial": 0,
             "last_contacted_at": None},
        ],
        relationships=[
            {"user_id": "jason", "label": "Father", "name_a": "Jason", "name_b": "Neil",
             "notes": ""},
            {"user_id": "eve", "label": "Partner", "name_a": "Eve", "name_b": "SecretEve",
             "notes": "private"},
        ],
        dates=[
            {"user_id": "jason", "label": "Birthday", "date_type": "birthday",
             "month": 5, "day": 12, "year": None, "name": "Neil"},
            {"user_id": "eve", "label": "Birthday", "date_type": "birthday",
             "month": 1, "day": 1, "year": None, "name": "SecretEve"},
        ],
        portraits={"jason": "Jason is a builder who values direct, concise answers."},
    )


@pytest.fixture
def _relational(monkeypatch):
    """Wire db_pool.get_db_ctx to the seeded fake store."""
    db = _seed_db()

    @contextlib.asynccontextmanager
    async def _fake_ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _fake_ctx())
    return db


# ── Flag OFF is a true no-op (golden snapshot) ──────────────────────────────


def test_flag_off_identical_to_today(monkeypatch, _relational):
    """Flag OFF ⇒ voice packet == the pure vector-recall packet (no relational)."""
    monkeypatch.delenv("ZOE_MEMORY_COMPOSE_ENABLED", raising=False)
    _patch_search(monkeypatch, [_Ref("My dad's name is Neil")])
    block = _run(v._voice_recall_packet("who is my dad", "jason"))
    # Byte-for-byte the pre-2c output: header + the single searched fact, nothing
    # from the relational store.
    assert block == "[What you remember]\n- My dad's name is Neil"
    for tag in (compose_mod.CITE_PEOPLE, compose_mod.CITE_DATE,
                compose_mod.CITE_PORTRAIT, compose_mod.CITE_RELATIONSHIP):
        assert tag not in block
    assert "Sara" not in block
    assert "builder" not in block


def test_flag_off_zero_value_is_no_op(monkeypatch, _relational):
    """A falsy flag value ('0') is treated as OFF — same no-op."""
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "0")
    _patch_search(monkeypatch, [_Ref("My dad's name is Neil")])
    block = _run(v._voice_recall_packet("who is my dad", "jason"))
    assert block == "[What you remember]\n- My dad's name is Neil"


# ── Flag ON + relational query ⇒ cited relational facts included ─────────────


def test_flag_on_relational_query_includes_cited_facts(monkeypatch, _relational):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    _patch_search(monkeypatch, [_Ref("My dad's name is Neil")])
    block = _run(v._voice_recall_packet("who is my dad and when is his birthday", "jason"))
    assert block is not None
    assert block.startswith("[What you remember]")
    # Vector recall still present.
    assert "My dad's name is Neil" in block
    # Cited relational facts folded in (people/date/portrait), each keeping its tag.
    assert compose_mod.CITE_PEOPLE in block
    assert compose_mod.CITE_DATE in block
    assert compose_mod.CITE_PORTRAIT in block
    assert "Neil (father)" in block
    assert "builder" in block  # portrait
    # Every folded line renders under the uniform "- " bullet.
    body_lines = block.split("\n")[1:]
    for ln in body_lines:
        assert ln.startswith("- ")
    # Combined budget: the whole block (vector recall + relational) stays compact.
    assert len(body_lines) <= v._VOICE_RECALL_MAX_LINES


def test_relational_lines_respect_combined_budget(monkeypatch, _relational):
    """A fully-populated relational query must NOT balloon the packet past the
    combined line budget (compose can return ~25 relational lines)."""
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    # Six vector hits (fills _VOICE_RECALL_MAX_FACTS) + a relational query so the
    # relational lines pile on top — the combined cap must hold the total down.
    _patch_search(monkeypatch, [_Ref(f"Vector fact {i}") for i in range(6)])
    block = _run(v._voice_recall_packet(
        "who is my dad and my sister and when is their birthday", "jason"))
    assert block is not None
    body_lines = block.split("\n")[1:]
    assert len(body_lines) <= v._VOICE_RECALL_MAX_LINES
    assert len(block) < 1264  # still smaller than the dump it replaces


def test_flag_on_works_even_when_vector_search_empty(monkeypatch, _relational):
    """Flag ON + relational query but no semantic hits ⇒ relational still lands
    (does NOT fall through to the metadata dump)."""
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    _patch_search(monkeypatch, [])  # search finds nothing

    import zoe_agent

    async def _dump(user_id, limit=20):
        return "## SHOULD NOT BE USED"

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _dump)
    block = _run(v._voice_recall_packet("tell me about my sister Sara", "jason"))
    assert block is not None
    assert "SHOULD NOT BE USED" not in block
    assert compose_mod.CITE_PEOPLE in block
    assert "Sara" in block


# ── Flag ON + non-relational query ⇒ gate keeps relational OUT ───────────────


def test_flag_on_non_relational_query_stays_vector_only(monkeypatch, _relational):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    _patch_search(monkeypatch, [_Ref("It is currently sunny")])
    block = _run(v._voice_recall_packet("what's the weather", "jason"))
    assert block == "[What you remember]\n- It is currently sunny"
    for tag in (compose_mod.CITE_PEOPLE, compose_mod.CITE_DATE,
                compose_mod.CITE_PORTRAIT, compose_mod.CITE_RELATIONSHIP):
        assert tag not in block
    assert "Neil" not in block
    assert "builder" not in block


# ── Scoping: no cross-user leakage, approved-only, guest fails closed ────────


def test_no_cross_user_leakage(monkeypatch, _relational):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    _patch_search(monkeypatch, [])
    block = _run(v._voice_recall_packet("who is my partner", "jason")) or ""
    assert "SecretEve" not in block
    assert "Eve" not in block


def test_soft_deleted_person_excluded(monkeypatch, _relational):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    _patch_search(monkeypatch, [])
    block = _run(v._voice_recall_packet("tell me about my friend", "jason")) or ""
    assert "Ghost" not in block


def test_guest_fails_closed_even_with_flag_on(monkeypatch, _relational):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    _patch_search(monkeypatch, [_Ref("should not be read")])
    # is_guest_memory_user guard runs before search AND before the relational read.
    assert _run(v._voice_recall_packet("who is my dad", "guest")) is None


def test_relational_lines_helper_empty_when_flag_off(monkeypatch, _relational):
    """_voice_relational_lines is a true no-op with the flag OFF."""
    monkeypatch.delenv("ZOE_MEMORY_COMPOSE_ENABLED", raising=False)
    assert _run(v._voice_relational_lines("who is my dad", "jason")) == []
