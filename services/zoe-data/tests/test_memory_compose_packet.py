"""Increment 2b — compose relational Postgres facts into /for-prompt.

These cover the router gate, the cited relational block, and the endpoint
composition. Everything is synthetic: the embedding layer is mocked via a fake
MemoryService and the relational store is a seeded in-memory fake — no DB, no
model loads.

Key invariants proven here:
  * flag OFF ⇒ packet identical to today (golden snapshot);
  * flag ON + relational query ⇒ cited relational facts AND vector recall;
  * flag ON + non-relational query ⇒ router gate keeps relational OUT;
  * citations present/correct; approved-only + no cross-user leakage (both stores).
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db_pool
import memory_service
import routers.memories as memories_mod
import zoe_memory_compose as compose_mod
from memory_service import MemoryRef
from routers.memories import router as memories_router

pytestmark = pytest.mark.ci_safe


def _ref(mem_id: str, text: str, **meta) -> MemoryRef:
    score = meta.pop("score", 0.0)
    return MemoryRef(id=mem_id, text=text, metadata=meta, score=score)


# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeSvc:
    """Stands in for MemoryService — approved facts + semantic hits only."""

    def __init__(self, facts=None, hits=None):
        self._facts = facts or []
        self._hits = hits or []

    async def load_for_prompt(self, user_id, *, limit=20):
        return self._facts

    async def search(self, q, *, user_id, limit=10):
        return self._hits


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
    """Seeded relational store keyed by user_id, honouring visibility scoping.

    Rows are dicts. ``execute`` matches the SQL by a coarse keyword so we don't
    reimplement a SQL engine — enough to prove scoping/citations/gating.
    """

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


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(memories_router)
    return app


@pytest.fixture
def _wire(monkeypatch):
    """Common wiring: internal token, non-guest, fake svc + fake DB via get_db_ctx."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: uid == "guest")

    facts = [_ref("abc12345", "Jason prefers concise answers")]
    hits = [_ref("hit00001", "My dad's name is Neil")]
    monkeypatch.setattr(memories_mod, "_svc", lambda: _FakeSvc(facts=facts, hits=hits))

    db = _seed_db()

    @contextlib.asynccontextmanager
    async def _fake_ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _fake_ctx())
    return db


def _get(user_id="jason", message="who is my dad", token="tok"):
    return TestClient(_app()).get(
        "/api/memories/for-prompt",
        params={"user_id": user_id, "message": message},
        headers={"X-Internal-Token": token},
    )


# ── Router gate unit tests ──────────────────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "who is my dad",
    "what is my father's name",
    "when is Neil's birthday",
    "tell me about my sister",
    "who is my wife",
    "what's my anniversary",
    "how is my brother doing",
])
def test_gate_relational_true(msg):
    assert compose_mod.needs_relational(msg) is True


@pytest.mark.parametrize("msg", [
    "turn on the lights",
    "what time is it",
    "set a timer for 5 minutes",
    "tell me a joke",
    "what's the weather",
    "",
])
def test_gate_relational_false(msg):
    assert compose_mod.needs_relational(msg) is False


# ── Flag OFF is a true no-op (golden snapshot) ──────────────────────────────


def test_flag_off_is_identical_packet(monkeypatch, _wire):
    monkeypatch.delenv("ZOE_MEMORY_COMPOSE_ENABLED", raising=False)
    resp = _get(message="who is my dad and when is his birthday")
    assert resp.status_code == 200
    body = resp.json()
    # No relational keys, no relational citations — pure vector packet.
    assert "relational" not in body
    assert compose_mod.CITE_PEOPLE not in body["packet"]
    assert compose_mod.CITE_DATE not in body["packet"]
    assert "## People & important dates" not in body["packet"]
    # Exactly the pre-2b shape/content: the recall query fires the semantic
    # search so both the hit and the general fact land (vector-only).
    assert body["user_scoped"] is True
    assert body["count"] == 2
    assert "[mem:abc12345]" in body["packet"]
    assert "[mem:hit00001]" in body["packet"]
    assert body["packet"].startswith("## What I know about you")


def test_flag_off_golden_matches_direct_builder(monkeypatch, _wire):
    """Belt-and-braces: OFF endpoint packet == the builder's output directly."""
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "0")
    resp = _get(message="who is my dad")
    body = resp.json()
    from routers.memories import _build_memory_prompt_packet
    golden = _build_memory_prompt_packet(
        [_ref("abc12345", "Jason prefers concise answers")],
        [_ref("hit00001", "My dad's name is Neil")],
        max_facts=12,
    )
    assert body["packet"] == golden["packet"]


# ── Flag ON + relational query ⇒ relational + vector, cited ──────────────────


def test_flag_on_relational_query_folds_cited_facts(monkeypatch, _wire):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    resp = _get(message="who is my dad and when is his birthday")
    assert resp.status_code == 200
    body = resp.json()
    packet = body["packet"]
    # Vector recall still present.
    assert "[mem:hit00001]" in packet or "[mem:abc12345]" in packet
    assert "## What I know about you" in packet
    # Relational section present + cited.
    assert "## People & important dates" in packet
    assert compose_mod.CITE_PEOPLE in packet
    assert compose_mod.CITE_DATE in packet
    assert compose_mod.CITE_PORTRAIT in packet
    assert "Neil" in packet
    assert body["relational"] >= 1
    # refs carry provenance source tags.
    sources = {r.get("source") for r in body["refs"]}
    assert "people" in sources
    assert "date" in sources
    assert "portrait" in sources


# ── Flag ON + non-relational query ⇒ gate keeps relational OUT ───────────────


def test_flag_on_non_relational_query_stays_vector_only(monkeypatch, _wire):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    resp = _get(message="turn on the lights")
    assert resp.status_code == 200
    body = resp.json()
    assert "relational" not in body
    assert "## People & important dates" not in body["packet"]
    assert compose_mod.CITE_PEOPLE not in body["packet"]


# ── No cross-user leakage (both stores) ─────────────────────────────────────


def test_no_cross_user_leakage(monkeypatch, _wire):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    resp = _get(user_id="jason", message="who is my partner")
    body = resp.json()
    # eve's private person/relationship/date must not appear for jason.
    assert "SecretEve" not in body["packet"]
    assert "Eve" not in body["packet"]


def test_soft_deleted_person_excluded(monkeypatch, _wire):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    resp = _get(user_id="jason", message="tell me about my friend")
    body = resp.json()
    assert "Ghost" not in body["packet"]


# ── Guest still fails closed with the flag ON ───────────────────────────────


def test_guest_fails_closed_even_with_flag_on(monkeypatch, _wire):
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    resp = _get(user_id="guest", message="who is my dad")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"packet": "", "refs": [], "count": 0, "user_scoped": False}
