"""
Comprehensive MemPalace integration tests.

Tests: user isolation, upsert behaviour, recency ordering, pattern coverage,
all-agent read paths, Bonsai memory capture, nightly digest, dedup, timeout safety.

Run: cd services/zoe-data && python -m pytest tests/test_mempalace_integration.py -v
"""
import asyncio
import hashlib
import importlib
import os
import sys
import time
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a lightweight in-memory ChromaDB stub so tests run without
# a real Chroma installation and without touching disk.
# ---------------------------------------------------------------------------

class _FakeCollection:
    """Minimal Chroma collection stub backed by a plain dict."""

    def __init__(self):
        self._store: dict[str, dict] = {}  # id → {document, metadata}

    def upsert(self, ids, documents, metadatas):
        for id_, doc, meta in zip(ids, documents, metadatas):
            self._store[id_] = {"document": doc, "metadata": dict(meta)}

    def add(self, ids, documents, metadatas):
        for id_, doc, meta in zip(ids, documents, metadatas):
            if id_ not in self._store:
                self._store[id_] = {"document": doc, "metadata": dict(meta)}

    @staticmethod
    def _match_where(meta: dict, where: dict | None) -> bool:
        """Minimal Chroma where-clause evaluator supporting $or / $and."""
        if not where:
            return True
        for k, v in where.items():
            if k == "$or":
                if not any(_FakeCollection._match_where(meta, clause) for clause in v):
                    return False
            elif k == "$and":
                if not all(_FakeCollection._match_where(meta, clause) for clause in v):
                    return False
            else:
                if meta.get(k) != v:
                    return False
        return True

    def get(self, where=None, include=None):
        results = {"ids": [], "documents": [], "metadatas": []}
        for id_, record in self._store.items():
            meta = record["metadata"]
            if not self._match_where(meta, where):
                continue
            results["ids"].append(id_)
            results["documents"].append(record["document"])
            results["metadatas"].append(meta)
        return results

    def query(self, query_texts, n_results=5, where=None, include=None):
        # Very naive: returns first n_results matching documents (no real vector search)
        docs = self.get(where=where)
        ids = docs["ids"][:n_results]
        docs_out = docs["documents"][:n_results]
        metas_out = docs["metadatas"][:n_results]
        distances = [0.0] * len(ids)
        return {
            "ids": [ids],
            "documents": [docs_out],
            "metadatas": [metas_out],
            "distances": [distances],
        }

    def delete(self, ids=None, where=None):
        if ids is not None:
            for id_ in ids:
                self._store.pop(id_, None)
        elif where:
            to_del = [i for i, rec in self._store.items()
                      if self._match_where(rec["metadata"], where)]
            for i in to_del:
                self._store.pop(i, None)


_GLOBAL_COLLECTION = _FakeCollection()


def _reset_collection():
    _GLOBAL_COLLECTION._store.clear()


def _fake_get_collection(_path):
    return _GLOBAL_COLLECTION


# ---------------------------------------------------------------------------
# Patch MemPalace imports before importing pi_agent
# ---------------------------------------------------------------------------

def _install_mempalace_stubs():
    """Install mempalace module stubs so pi_agent doesn't require the real package."""
    palace_mod = types.ModuleType("mempalace")
    palace_sub = types.ModuleType("mempalace.palace")
    palace_sub.get_collection = _fake_get_collection
    searcher_sub = types.ModuleType("mempalace.searcher")

    def _fake_search(query, path, wing=None, n_results=5):
        col = _fake_get_collection(path)
        hits = col.get(where={"wing": wing} if wing else None)
        docs = hits["documents"][:n_results]
        return {"results": [{"text": d} for d in docs]}

    searcher_sub.search_memories = _fake_search
    sys.modules.setdefault("mempalace", palace_mod)
    sys.modules["mempalace.palace"] = palace_sub
    sys.modules["mempalace.searcher"] = searcher_sub


_install_mempalace_stubs()

# Now we can import pi_agent functions
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi_agent import (
    _mempalace_add,
    _mempalace_search,
    _mempalace_load_user_facts,
    _fast_memory_extract,
    _fire_memory_capture,
    _pi_soul,
    migrate_mempalace_legacy_records,
)
from routers.chat import _persist_memory_candidates


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_collection():
    """Reset the in-memory Chroma collection before each test.

    Also resets the MemoryService singleton so per-instance state
    (idempotency `_seen_keys`, per-user locks, access-tick queue) does
    not leak between tests — the fake collection is cleared, but the
    service remembers what it wrote.
    """
    _reset_collection()
    try:
        import memory_service as _ms
        _ms._service_singleton = None
    except Exception:
        pass
    # Also drop pi_agent's per-turn facts cache.
    try:
        import pi_agent as _pa
        _pa._USER_FACTS_CACHE.clear()
    except Exception:
        pass
    yield


# ---------------------------------------------------------------------------
# 1. User isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_isolation_write_jason_read_family():
    """Writing for 'jason' must not appear when loading facts for 'family-admin'."""
    await _mempalace_add("User was born on 24 March 1982", user_id="jason")
    facts_family = await _mempalace_load_user_facts("family-admin")
    assert "1982" not in facts_family, "family-admin should not see jason's memories"


@pytest.mark.asyncio
async def test_user_isolation_write_family_read_jason():
    """Writing for 'family-admin' must not appear when loading facts for 'jason'."""
    await _mempalace_add("User prefers tea over coffee", user_id="family-admin")
    facts_jason = await _mempalace_load_user_facts("jason")
    assert "tea" not in facts_jason, "jason should not see family-admin's memories"


@pytest.mark.asyncio
async def test_user_isolation_independent_counts():
    """Each user's facts are independent — count is per-user."""
    await _mempalace_add("User lives in Perth", user_id="jason")
    await _mempalace_add("User lives in Melbourne", user_id="family-admin")
    await _mempalace_add("User is 44 years old", user_id="jason")

    jason_facts = await _mempalace_load_user_facts("jason")
    family_facts = await _mempalace_load_user_facts("family-admin")

    assert "Perth" in jason_facts
    assert "44" in jason_facts
    assert "Melbourne" in family_facts
    assert "Melbourne" not in jason_facts


# ---------------------------------------------------------------------------
# 2. Upsert behaviour (not add — must overwrite on same fact)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_same_fact_stored_once():
    """Writing the same fact twice should result in only one record."""
    fact = "User's name is Jason"
    await _mempalace_add(fact, user_id="jason")
    await _mempalace_add(fact, user_id="jason")

    col = _GLOBAL_COLLECTION
    jason_records = [r for r in col._store.values() if r["metadata"].get("wing") == "jason"]
    assert len(jason_records) == 1, f"Expected 1 record, got {len(jason_records)}"


@pytest.mark.asyncio
async def test_upsert_updates_added_at():
    """Second upsert of same fact should update added_at, not create a new record."""
    fact = "User's name is Jason"
    await _mempalace_add(fact, user_id="jason")
    time.sleep(0.01)
    await _mempalace_add(fact, user_id="jason")

    col = _GLOBAL_COLLECTION
    records = [r for r in col._store.values() if r["metadata"].get("wing") == "jason"]
    assert len(records) == 1
    assert "added_at" in records[0]["metadata"]


# ---------------------------------------------------------------------------
# 3. Recency ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recency_ordering():
    """Facts should be returned most-recent-first."""
    import datetime
    facts = [
        ("User prefers morning coffee", "2024-01-01T08:00:00"),
        ("User has a cat named Biscuit", "2024-06-15T12:00:00"),
        ("User was born on 24 March 1982", "2024-12-25T09:00:00"),
    ]
    col = _GLOBAL_COLLECTION
    for doc, ts in facts:
        id_ = f"jason_{hashlib.md5(doc.encode()).hexdigest()[:16]}"
        col.upsert(ids=[id_], documents=[doc], metadatas=[{"wing": "jason", "added_at": ts}])

    result = await _mempalace_load_user_facts("jason")
    # Most recent = 2024-12 (born on 24 March 1982) should appear before the 2024-01 one
    born_pos = result.find("born")
    coffee_pos = result.find("coffee")
    assert born_pos < coffee_pos, "Most recent fact should appear first"


# ---------------------------------------------------------------------------
# 4. Pattern coverage — all extraction patterns must fire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg,expected_fragment", [
    ("I was born on the 24th of March 1982", "born"),
    ("my name is Jason", "Jason"),
    ("I'm 44 years old", "44"),
    ("My full name is Jason Smith", "Jason Smith"),
    ("I was born on 1982-03-24", "born"),
    ("call me Jay", "Jay"),
    ("I live in Perth", "Perth"),
    ("I prefer tea over coffee", "tea"),
    ("I don't like broccoli", "broccoli"),
    ("my birthday is March 24", "March"),
    ("I work for Acme Corp", "Acme"),
    ("please remember that I use dark mode", "dark mode"),
])
def test_pattern_extraction(msg, expected_fragment):
    """Each extraction pattern must fire on its canonical example."""
    facts = _fast_memory_extract(msg)
    combined = " ".join(facts).lower()
    assert expected_fragment.lower() in combined, (
        f"Expected '{expected_fragment}' in extracted facts for: {msg!r}\nGot: {facts}"
    )


def test_question_not_extracted():
    """Questions must not be extracted as memory facts."""
    messages = [
        "What is my name?",
        "How old am I?",
        "what time is it?",
        "Tell me a joke",
        "Hello",
        "Thanks",
    ]
    for msg in messages:
        facts = _fast_memory_extract(msg)
        assert not facts, f"Should not extract from question: {msg!r} → {facts}"


# ---------------------------------------------------------------------------
# 5. All-agent read path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_agents_see_same_facts():
    """_mempalace_load_user_facts should return same data regardless of caller."""
    await _mempalace_add("User is 44 years old", user_id="jason")
    await _mempalace_add("User lives in Perth", user_id="jason")

    # Simulate each agent calling _mempalace_load_user_facts
    pi_view = await _mempalace_load_user_facts("jason")
    bonsai_view = await _mempalace_load_user_facts("jason")
    openclaw_view = await _mempalace_load_user_facts("jason")

    assert pi_view == bonsai_view == openclaw_view
    assert "44" in pi_view
    assert "Perth" in pi_view


# ---------------------------------------------------------------------------
# 5b. Capture path lockdown — Pi + Hermes/OpenClaw shared hook
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_capture_paths_pi_hermes_openclaw():
    """All runtime agent paths should persist through shared MemPalace pipeline."""
    from pi_agent import _background_memory_save

    # Pi-agent post-turn capture
    await _background_memory_save(
        "remember that my friend Ava is a designer",
        "Got it.",
        user_id="jason",
    )

    # Hermes/OpenClaw chat router shared persistence hook
    await _persist_memory_candidates(
        "jason",
        "sess-hermes",
        "remember that i met Noah and he is a plumber",
        "Noted.",
    )
    await _persist_memory_candidates(
        "jason",
        "sess-openclaw",
        "remember that i prefer tea over coffee",
        "Saved.",
    )

    facts = await _mempalace_load_user_facts("jason")
    facts_l = facts.lower()
    assert "ava" in facts_l
    assert "noah" in facts_l
    assert "tea" in facts_l


# ---------------------------------------------------------------------------
# 6. _pi_soul includes datetime and user_id
# ---------------------------------------------------------------------------

def test_pi_soul_contains_datetime():
    soul = _pi_soul(username="Jason", user_id="jason")
    assert "jason" in soul.lower()
    assert "Jason" in soul
    # Check year appears (datetime stamp)
    import datetime
    assert str(datetime.datetime.now().year) in soul


def test_pi_soul_empty_user():
    """Should not crash with empty username."""
    soul = _pi_soul()
    assert "You are Zoe" in soul


# ---------------------------------------------------------------------------
# 7. Background memory capture fires and is user-scoped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fire_memory_capture_user_scoped():
    """_fire_memory_capture must write facts to the correct user's wing."""
    msg = "I was born on 24 March 1982"
    reply = "I've noted that you were born on 24 March 1982."
    # Directly call the async save to test (not the fire wrapper)
    from pi_agent import _background_memory_save
    await _background_memory_save(msg, reply, user_id="jason")

    col = _GLOBAL_COLLECTION
    jason_records = [r for r in col._store.values() if r["metadata"].get("wing") == "jason"]
    assert len(jason_records) >= 1, "Should have written at least one fact for jason"
    family_records = [r for r in col._store.values() if r["metadata"].get("wing") == "family-admin"]
    assert len(family_records) == 0, "family-admin should have no records from jason's message"


# ---------------------------------------------------------------------------
# 8. Dedup — same fact written twice stays as one record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_dedup():
    """Writing same fact twice via _background_memory_save should not create duplicates."""
    from pi_agent import _background_memory_save
    msg = "My name is Jason"
    await _background_memory_save(msg, "", user_id="jason")
    await _background_memory_save(msg, "", user_id="jason")

    col = _GLOBAL_COLLECTION
    jason_records = [r for r in col._store.values() if r["metadata"].get("wing") == "jason"]
    # At most 1 record for the same name fact
    assert len(jason_records) <= 1


# ---------------------------------------------------------------------------
# 9. Timeout safety — Chroma unavailable → all functions return gracefully
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_user_facts_tolerates_error():
    """If Chroma raises, _mempalace_load_user_facts must return '' not crash."""
    orig_get = sys.modules["mempalace.palace"].get_collection

    def _broken_get(_path):
        raise RuntimeError("Chroma crashed!")

    sys.modules["mempalace.palace"].get_collection = _broken_get
    try:
        result = await _mempalace_load_user_facts("jason")
        assert result == "", f"Expected empty string on error, got: {result!r}"
    finally:
        sys.modules["mempalace.palace"].get_collection = orig_get


@pytest.mark.asyncio
async def test_mempalace_add_tolerates_error():
    """If Chroma raises, _mempalace_add must return False not crash."""
    orig_get = sys.modules["mempalace.palace"].get_collection

    def _broken_get(_path):
        raise RuntimeError("Chroma crashed!")

    sys.modules["mempalace.palace"].get_collection = _broken_get
    try:
        ok = await _mempalace_add("some fact", user_id="jason")
        assert ok is False
    finally:
        sys.modules["mempalace.palace"].get_collection = orig_get


@pytest.mark.asyncio
async def test_mempalace_search_tolerates_error():
    """If Chroma raises, _mempalace_search must return [] not crash."""
    orig_search = sys.modules["mempalace.searcher"].search_memories

    def _broken_search(*args, **kwargs):
        raise RuntimeError("Chroma crashed!")

    sys.modules["mempalace.searcher"].search_memories = _broken_search
    try:
        results = await _mempalace_search("some query", user_id="jason")
        assert results == []
    finally:
        sys.modules["mempalace.searcher"].search_memories = orig_search


# ---------------------------------------------------------------------------
# 10. Migration helper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migration_retags_legacy_records(tmp_path, monkeypatch):
    """migrate_mempalace_legacy_records must retag wing='zoe' → wing='family-admin'."""
    # Insert a legacy record with wing="zoe"
    col = _GLOBAL_COLLECTION
    col.upsert(
        ids=["old_record_1"],
        documents=["User is a loyal Zoe user"],
        metadatas=[{"wing": "zoe", "room": "conversations"}],
    )
    assert col._store["old_record_1"]["metadata"]["wing"] == "zoe"

    # Use a temp dir so the flag file doesn't interfere with real .mempalace
    flag_path = str(tmp_path / ".migration_v1_done")
    monkeypatch.setattr("pi_agent._MIGRATION_DONE_FLAG", flag_path)
    import pi_agent as _pa
    monkeypatch.setattr(_pa, "_MIGRATION_DONE_FLAG", flag_path)

    await asyncio.get_event_loop().run_in_executor(None, migrate_mempalace_legacy_records)

    assert col._store["old_record_1"]["metadata"]["wing"] == "family-admin"
    assert "added_at" in col._store["old_record_1"]["metadata"]


# ---------------------------------------------------------------------------
# 11. _mempalace_load_user_facts returns empty string when no facts stored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_user_facts_empty():
    result = await _mempalace_load_user_facts("unknown_user_xyz")
    assert result == ""


# ---------------------------------------------------------------------------
# 12. Tags are stored correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tags_stored():
    await _mempalace_add("User likes dark mode", user_id="jason", tags=["preference", "ui"])
    col = _GLOBAL_COLLECTION
    records = [r for r in col._store.values() if r["metadata"].get("wing") == "jason"]
    assert len(records) == 1
    tags = records[0]["metadata"].get("tags", "")
    assert "preference" in tags
    assert "ui" in tags


# ---------------------------------------------------------------------------
# 13. added_by is stored correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_added_by_stored():
    await _mempalace_add("User prefers morning coffee", user_id="jason", added_by="memory_digest")
    col = _GLOBAL_COLLECTION
    records = [r for r in col._store.values() if r["metadata"].get("wing") == "jason"]
    assert records[0]["metadata"].get("added_by") == "memory_digest"
