"""Unit tests for the memory_service.py P2/P3 correctness fixes.

Each test maps to a finding fixed in the same PR:

  FIX 1  review(decision='edit') must not lose the old memory if the new write fails
  FIX 2  counter-bump tick paths must NOT re-embed (col.update, not col.upsert)
  FIX 3  archive_by_entity's Chroma scan must be offloaded off the event loop
  FIX 4  _seen_keys and the per-row _query_hashes blob must stay bounded

The tests inject a fake Chroma collection so they need neither a real MemPalace
store nor fastembed.

Additional adversarially-verified findings fixed alongside the above:

  FIX 5  re-ingesting a fact whose deterministic mem_id already has a reviewed
         (approved/rejected/archived) row must not clobber it back to pending
  FIX 6  the idempotency key must include lane fields (memory_type/scope/
         entity), matching _memory_id, so same-text-different-lane facts don't
         collide in the dedup cache
  FIX 7  delete_user must purge that user's _seen_keys entries so re-teaching a
         forgotten fact isn't dropped as a stale duplicate
  FIX 8  review(decision='edit') must carry forward scope/visibility/
         source_excerpt/extra metadata instead of defaulting them away
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

    # ---- deletes ------------------------------------------------------------
    def delete(self, *, ids=None, where=None):
        if ids is not None:
            targets = list(ids)
        elif where is not None:
            targets = [
                _id for _id, row in self.rows.items() if _match_where(where, row["metadata"])
            ]
        else:
            targets = []
        for _id in targets:
            self.rows.pop(_id, None)

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
    # The per-user index must shrink with evictions too, not just _seen_keys
    # itself — otherwise it leaks one sha256 string per ingest forever.
    assert sum(len(v) for v in svc._seen_keys_by_user.values()) <= 5
    svc._seen_keys._maxlen = cap  # restore


def test_bounded_key_set_eviction_callback_prunes_per_user_index():
    """_seen_keys eviction must notify _seen_keys_by_user (heap-leak guard)."""
    svc = memory_service.MemoryService.__new__(memory_service.MemoryService)
    svc._seen_keys_by_user = {}
    svc._seen_keys = _BoundedKeySet(maxlen=2, on_evict=svc._on_seen_key_evicted)
    svc._remember_seen_key("u1", "k1")
    svc._remember_seen_key("u1", "k2")
    svc._remember_seen_key("u2", "k3")  # evicts k1 (oldest)
    assert "k1" not in svc._seen_keys
    assert svc._seen_keys_by_user == {"u1": {"k2"}, "u2": {"k3"}}
    svc._remember_seen_key("u2", "k4")  # evicts k2 -> u1's set empties out
    assert "u1" not in svc._seen_keys_by_user, "empty per-user sets must be dropped"
    assert svc._seen_keys_by_user == {"u2": {"k3", "k4"}}


async def test_query_hashes_blob_is_bounded(svc):
    ref = await _seed(svc, "enjoys hiking")
    # Surface this memory under many distinct queries.
    for i in range(_MAX_QUERY_HASHES + 50):
        await svc.tick_access("u1", [ref.id], query=f"distinct query {i}")

    meta = svc._fake.rows[ref.id]["metadata"]
    stored = [h for h in (meta.get("_query_hashes") or "").split(",") if h]
    assert len(stored) <= _MAX_QUERY_HASHES, "stored hash blob must stay capped"
    # The blob never evicts, so it fills exactly to the cap...
    assert len(stored) == _MAX_QUERY_HASHES
    # ...and unique_query_count freezes there (== min(distinct, cap)) instead of
    # inflating past it as churned-out queries keep arriving.
    assert meta["unique_query_count"] == _MAX_QUERY_HASHES


async def test_unique_query_count_does_not_inflate_after_saturation(svc):
    """Greptile P2: an evicted/churned query must NOT re-count once the bounded
    window is saturated, or the diversity signal inflates without truly new queries."""
    ref = await _seed(svc, "enjoys hiking")

    # Fill the window exactly to the cap with distinct queries.
    for i in range(_MAX_QUERY_HASHES):
        await svc.tick_access("u1", [ref.id], query=f"q{i}")
    meta = svc._fake.rows[ref.id]["metadata"]
    assert meta["unique_query_count"] == _MAX_QUERY_HASHES
    assert len([h for h in meta["_query_hashes"].split(",") if h]) == _MAX_QUERY_HASHES

    # A brand-new distinct query after saturation must not bump the count: the
    # window is full and we can no longer distinguish new from already-dropped.
    await svc.tick_access("u1", [ref.id], query="a totally new query")
    meta = svc._fake.rows[ref.id]["metadata"]
    assert meta["unique_query_count"] == _MAX_QUERY_HASHES, "saturated count must freeze"

    # Re-issuing one of the earliest queries (the kind the old sliding window would
    # have evicted and then re-counted) must also leave the count untouched.
    await svc.tick_access("u1", [ref.id], query="q0")
    meta = svc._fake.rows[ref.id]["metadata"]
    assert meta["unique_query_count"] == _MAX_QUERY_HASHES, "re-seen query must not re-count"
    # access_count keeps climbing even while the distinct-query signal is frozen.
    assert meta["access_count"] == _MAX_QUERY_HASHES + 2


# ── FIX 5: re-ingest must not clobber an already-reviewed row back to pending ──

async def test_reingest_after_restart_does_not_clobber_approved_status(svc):
    """Simulates the real failure scenario: propose -> approve -> process
    restart (in-memory _seen_keys lost) -> the same proposal is POSTed again.
    The durable mem_id is deterministic (text+lanes only), so the second
    ingest must not resurrect a fresh 'pending' row over the approved one."""
    ref = await svc.ingest(
        "I prefer tea", user_id="u1", source="test", status="pending"
    )
    approved = await svc.review(ref.id, decision="approve", actor="reviewer")
    assert approved.metadata["status"] == "approved"

    # Simulate a process restart: the fast-path cache is gone.
    svc._seen_keys = _BoundedKeySet()
    svc._seen_keys_by_user = {}

    replay = await svc.ingest(
        "I prefer tea", user_id="u1", source="test", status="pending"
    )
    assert replay is None, "replayed proposal must be dropped as a duplicate"

    row = await svc.get(ref.id)
    assert row.metadata["status"] == "approved", "status must not regress to pending"
    assert row.metadata.get("reviewed_by") == "reviewer", "review history must survive"


async def test_reingest_of_still_pending_row_is_allowed(svc):
    """A row that was never reviewed (still 'pending') has no review history to
    lose, so a duplicate ingest attempt for it may proceed normally."""
    ref = await svc.ingest(
        "likes rainy days", user_id="u1", source="test", status="pending"
    )
    svc._seen_keys = _BoundedKeySet()
    svc._seen_keys_by_user = {}

    again = await svc.ingest(
        "likes rainy days", user_id="u1", source="test", status="pending"
    )
    assert again is not None
    assert again.id == ref.id


# ── FIX 6: idempotency key must be lane-aware, matching _memory_id ─────────────

async def test_idempotency_key_distinguishes_lanes(svc):
    """Same text, different memory_type, ingested back-to-back in one process
    (no restart) must both land as distinct rows, not have the second dropped
    as a false-positive duplicate."""
    fact_ref = await svc.ingest(
        "loves jazz", user_id="u1", source="test", memory_type="fact"
    )
    pref_ref = await svc.ingest(
        "loves jazz", user_id="u1", source="test", memory_type="preference"
    )
    assert fact_ref is not None
    assert pref_ref is not None
    assert fact_ref.id != pref_ref.id
    assert len(svc._fake.rows) == 2


# ── FIX 7: delete_user must purge that user's idempotency-cache entries ────────

async def test_delete_user_purges_seen_keys_for_reteaching(svc):
    ref = await svc.ingest("owns a cat named milo", user_id="u1", source="test")
    assert ref is not None
    idem_key = ref.metadata["idempotency_key"]
    assert idem_key in svc._seen_keys

    await svc.delete_user("u1", actor="admin")
    assert idem_key not in svc._seen_keys, "forgotten user's cache entries must be purged"
    assert "u1" not in svc._seen_keys_by_user

    # Re-teaching the same fact after the forget must succeed, not be dropped.
    retaught = await svc.ingest("owns a cat named milo", user_id="u1", source="test")
    assert retaught is not None


async def test_delete_user_does_not_purge_other_users_keys(svc):
    ref1 = await svc.ingest("fact for u1", user_id="u1", source="test")
    ref2 = await svc.ingest("fact for u2", user_id="u2", source="test")
    assert ref1 is not None and ref2 is not None
    key2 = ref2.metadata["idempotency_key"]

    await svc.delete_user("u1", actor="admin")
    assert key2 in svc._seen_keys, "unrelated user's cache entries must survive"


# ── FIX 8: edit must carry forward scope/visibility/source_excerpt/extras ─────

async def test_edit_preserves_shared_scope_and_extras(svc):
    ref = await svc.ingest(
        "family wifi password rotated monthly",
        user_id="u1",
        source="test",
        status="pending",
        scope="shared",
        source_excerpt="said during dinner",
        metadata={"custom_note": "from kitchen assistant"},
    )
    assert ref.metadata["visibility"] == "family"
    assert ref.metadata["scope"] == "shared"
    assert ref.metadata.get("candidate_custom_note") == "from kitchen assistant"

    edited = await svc.review(
        ref.id, decision="edit", edits="family wifi password rotated quarterly",
        actor="tester",
    )
    assert edited.metadata["visibility"] == "family", "edit must not downgrade visibility"
    assert edited.metadata["scope"] == "shared"
    assert edited.metadata.get("source_excerpt") == "said during dinner"
    assert edited.metadata.get("candidate_custom_note") == "from kitchen assistant"
