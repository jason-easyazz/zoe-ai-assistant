"""Unit tests for the memory_service.py P2/P3 correctness fixes.

Each test maps to a finding fixed in the same PR:

  FIX 1  review(decision='edit') must not lose the old memory if the new write fails
  FIX 2  counter-bump tick paths must NOT re-embed (col.update, not col.upsert)
  FIX 3  archive_by_entity's Chroma scan must be offloaded off the event loop
  FIX 4  _seen_keys and the per-row _query_hashes blob must stay bounded

The tests inject a fake Chroma collection so they need neither a real MemPalace
store nor fastembed.
"""

import threading

import pytest

import memory_service
from memory_service import (
    MemoryService,
    _BoundedKeySet,
    _MAX_QUERY_HASHES,
    _SEEN_KEYS_MAX,
)


def _match_where(where, meta):
    """Minimal Chroma where-clause matcher: $and / $or / equality."""
    if where is None:
        return True
    if "$and" in where:
        return all(_match_where(c, meta) for c in where["$and"])
    if "$or" in where:
        return any(_match_where(c, meta) for c in where["$or"])
    for key, val in where.items():
        if meta.get(key) != val:
            return False
    return True


class FakeCollection:
    """In-memory stand-in for the MemPalace Chroma collection wrapper.

    Records every write so tests can assert update-vs-upsert and capture the
    thread each read ran on (to prove event-loop offload).
    """

    def __init__(self):
        self.rows = {}  # id -> {"document": str, "metadata": dict}
        self.write_calls = []  # list of {"method", "has_documents", "ids"}
        self.get_threads = []  # list of (thread_ident, where)
        self.fail_predicate = None  # callable(id, meta) -> bool; True => raise

    # ---- writes -----------------------------------------------------------
    def upsert(self, *, ids, documents=None, metadatas=None, embeddings=None):
        self.write_calls.append(
            {"method": "upsert", "has_documents": documents is not None, "ids": list(ids)}
        )
        for i, _id in enumerate(ids):
            meta = dict(metadatas[i]) if metadatas else self.rows.get(_id, {}).get("metadata", {})
            if self.fail_predicate and self.fail_predicate(_id, meta):
                raise RuntimeError(f"simulated write failure for {_id}")
            doc = documents[i] if documents else self.rows.get(_id, {}).get("document", "")
            self.rows[_id] = {"document": doc, "metadata": meta}

    def update(self, *, ids, documents=None, metadatas=None, embeddings=None):
        if documents is None and metadatas is None and embeddings is None:
            raise ValueError("update requires at least one field")
        self.write_calls.append(
            {"method": "update", "has_documents": documents is not None, "ids": list(ids)}
        )
        for i, _id in enumerate(ids):
            if _id not in self.rows:
                continue
            if documents is not None:
                self.rows[_id]["document"] = documents[i]
            if metadatas is not None:
                self.rows[_id]["metadata"] = dict(metadatas[i])

    # ---- reads ------------------------------------------------------------
    def get(self, *, ids=None, where=None, include=None):
        self.get_threads.append((threading.get_ident(), where))
        items = []
        for _id, row in self.rows.items():
            if ids is not None and _id not in ids:
                continue
            if where is not None and not _match_where(where, row["metadata"]):
                continue
            items.append((_id, row))
        if ids is not None:
            order = {i: n for n, i in enumerate(ids)}
            items.sort(key=lambda kv: order.get(kv[0], 1 << 30))
        return {
            "ids": [i for i, _ in items],
            "documents": [r["document"] for _, r in items],
            "metadatas": [dict(r["metadata"]) for _, r in items],
        }


@pytest.fixture
def svc(monkeypatch):
    fake = FakeCollection()
    audit = FakeCollection()
    service = MemoryService(data_dir="/tmp/does-not-exist-mempalace")
    monkeypatch.setattr(service, "_collection", lambda: fake)
    monkeypatch.setattr(service, "_audit_collection", lambda: audit)
    service._fake = fake
    service._audit_fake = audit
    return service


async def _seed(service, text="my name is jason", user_id="u1", **kw):
    ref = await service.ingest(text, user_id=user_id, source="test", **kw)
    assert ref is not None
    return ref


# ── FIX 1: edit must not lose the old memory on a failed new write ────────────

async def test_edit_failed_new_write_keeps_old_memory(svc):
    ref = await _seed(svc, "favourite colour is blue")
    old_id = ref.id

    # Arm the fake so writing the NEW (status="approved") row throws. With the safe
    # ordering the new row is written first, so this aborts before the old row is
    # ever marked superseded.
    svc._fake.fail_predicate = lambda _id, meta: (
        meta.get("status") == "approved" and _id != old_id
    )

    with pytest.raises(Exception):
        await svc.review(old_id, decision="edit", edits="favourite colour is green",
                         actor="tester")

    # The old fact must still be readable (NOT superseded/hidden) and unchanged.
    survived = await svc.get(old_id)
    assert survived is not None
    assert survived.metadata.get("status") == "approved"
    assert survived.text == "favourite colour is blue"
    # No orphan/new row was persisted.
    assert list(svc._fake.rows.keys()) == [old_id]


async def test_edit_success_writes_new_and_supersedes_old(svc):
    ref = await _seed(svc, "favourite colour is blue")
    old_id = ref.id

    new_ref = await svc.review(old_id, decision="edit", edits="favourite colour is green",
                               actor="tester")

    assert new_ref.id != old_id
    assert new_ref.text == "favourite colour is green"
    assert new_ref.metadata.get("status") == "approved"
    assert new_ref.metadata.get("supersedes_id") == old_id
    # Old row retained by id but retired so reads skip it.
    old_after = await svc.get(old_id)
    assert old_after.metadata.get("status") == "superseded"
    assert old_after.metadata.get("superseded_by_id") == new_ref.id


# ── FIX 2: counter-bump paths must not re-embed (update, not upsert) ──────────

async def test_tick_access_uses_update_not_upsert(svc):
    ref = await _seed(svc, "likes espresso")
    svc._fake.write_calls.clear()

    await svc.tick_access("u1", [ref.id], query="what does jason drink")

    methods = [c["method"] for c in svc._fake.write_calls]
    assert "update" in methods, "tick_access must write via col.update"
    assert "upsert" not in methods, "tick_access must NOT re-embed via col.upsert"
    for c in svc._fake.write_calls:
        if c["method"] == "update":
            assert c["has_documents"] is False, "no documents => no embedding recompute"

    # Metadata values are still written as before.
    meta = svc._fake.rows[ref.id]["metadata"]
    assert meta["access_count"] == 1
    assert meta["unique_query_count"] == 1
    assert meta["last_accessed"]


async def test_tick_consolidation_uses_update_not_upsert(svc):
    ref = await _seed(svc, "has a dog named teddy")
    svc._fake.write_calls.clear()

    await svc.tick_consolidation("u1", [ref.id])

    methods = [c["method"] for c in svc._fake.write_calls]
    assert methods == ["update"], f"expected a single update, got {methods}"
    assert svc._fake.write_calls[0]["has_documents"] is False
    assert svc._fake.rows[ref.id]["metadata"]["consolidation_count"] == 1


# ── FIX 3: archive_by_entity must offload the blocking Chroma scan ────────────

async def test_archive_by_entity_offloads_scan(svc):
    main_ident = threading.get_ident()
    await _seed(svc, "works at acme", entity_type="person", entity_id="ent1")
    svc._fake.get_threads.clear()

    archived = await svc.archive_by_entity(entity_id="ent1", user_id="u1")
    assert archived == 1

    # The entity scan ($and where-clause) must have run in an executor thread,
    # never on the event-loop thread.
    scan_calls = [t for (t, where) in svc._fake.get_threads if where and "$and" in where]
    assert scan_calls, "expected an $and entity scan"
    for ident in scan_calls:
        assert ident != main_ident, "archive_by_entity scan ran on the event loop"


# ── FIX 4: bounded in-memory growth ──────────────────────────────────────────

def test_bounded_key_set_evicts_oldest():
    s = _BoundedKeySet(maxlen=3)
    for k in ["a", "b", "c"]:
        s.add(k)
    assert len(s) == 3
    s.add("d")  # evicts "a"
    assert len(s) == 3
    assert "a" not in s
    assert "d" in s and "b" in s and "c" in s
    # Re-adding an existing key is a no-op and does not grow the set.
    s.add("d")
    assert len(s) == 3


async def test_seen_keys_is_bounded(svc):
    assert isinstance(svc._seen_keys, _BoundedKeySet)
    # Drive more distinct ingests than the cap would allow if unbounded.
    cap = svc._seen_keys._maxlen
    svc._seen_keys._maxlen = 5  # shrink so the test is fast
    for i in range(20):
        await svc.ingest(f"fact number {i}", user_id="u1", source="test",
                         user_turn_id=f"turn-{i}")
    assert len(svc._seen_keys) <= 5
    svc._seen_keys._maxlen = cap  # restore


async def test_query_hashes_blob_is_bounded(svc):
    ref = await _seed(svc, "enjoys hiking")
    # Surface this memory under many distinct queries.
    for i in range(_MAX_QUERY_HASHES + 50):
        await svc.tick_access("u1", [ref.id], query=f"distinct query {i}")

    meta = svc._fake.rows[ref.id]["metadata"]
    stored = [h for h in (meta.get("_query_hashes") or "").split(",") if h]
    assert len(stored) <= _MAX_QUERY_HASHES, "stored hash blob must stay capped"
    # unique_query_count is the durable monotonic signal and keeps counting.
    assert meta["unique_query_count"] >= _MAX_QUERY_HASHES
