"""A6 step 2: the nightly dreaming passes run their vector-store calls OFF the event loop, with the same writes, and a forget
that lands mid-pass still wins.

A deterministic in-memory store stands in for the drawers collection. The golden writes below are what origin/main's passes
wrote for the same seed (checked by running this scenario on main before the change): same ids, documents, metadata.
Negative controls: (1) the same slow-store harness with the store call put back inline shows the whole block; (2) the same
racing forget with the write's re-check removed resurrects the erased row.
"""
from __future__ import annotations

import asyncio
import copy
import functools
import datetime
import time

import pytest

import memory_digest as md
import memory_service as ms

pytestmark = pytest.mark.ci_safe

UID = "u-test"


class Store:
    """Enough of a chroma collection for the four passes: get(where=/ids=, include=), upsert. Records every write."""

    def __init__(self, rows: dict, slow_s: float = 0.0):
        self.rows, self.slow_s, self.writes, self.after_get = rows, slow_s, [], None

    @staticmethod
    def _match(meta: dict, where: dict) -> bool:
        if "$and" in where:
            return all(Store._match(meta, w) for w in where["$and"])
        (k, cond), = where.items()
        op, v = next(iter(cond.items()))
        return meta.get(k) == v if op == "$eq" else meta.get(k) != v

    def get(self, *, ids=None, where=None, include=()):
        slow = self.slow_s  # the read sees the rows as they were when it STARTED, then takes its time
        keys = [i for i in (ids or self.rows) if i in self.rows and (where is None or self._match(self.rows[i][1], where))]
        out = {"ids": keys}
        if "documents" in include:
            out["documents"] = [self.rows[i][0] for i in keys]
        if "metadatas" in include:
            out["metadatas"] = [copy.deepcopy(self.rows[i][1]) for i in keys]
        if slow:
            time.sleep(slow)
        hook, self.after_get = self.after_get, None
        if hook:
            hook()
        return out

    def upsert(self, *, ids, documents, metadatas):
        self.writes.append((list(ids), list(documents), copy.deepcopy(list(metadatas))))
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, copy.deepcopy(m))


class Svc:
    def __init__(self, store: Store):
        self.store, self._user_locks = store, {}

    async def search(self, doc, *, user_id, limit):
        return [type("R", (), {"id": i}) for i in sorted(self.store.rows) if self.store.rows[i][0] != doc][:limit]

    async def tick_access(self, user_id, ids):
        return None

    async def ingest(self, text, **kw):
        self.store.rows["syn-1"] = (text, {"user_id": UID, "status": "approved", "source": "synthesis"})
        if kw.get("created_ids") is not None:
            kw["created_ids"].append("syn-1")
        return type("R", (), {"id": "syn-1"})

    async def erase_rows(self, user_id, ids, **kw):
        assert not self._user_locks[user_id].locked()  # the erase gate must never be taken under the memory lock
        for rid in ids:
            self.store.rows.pop(rid, None)


def _seed() -> dict:
    # "today" read when the rows are seeded, not at import: collection can run before midnight UTC and the test after it (CI did:
    # the REM pass then saw no row of ITS today and wrote nothing)
    today = datetime.datetime.utcnow().date().isoformat()
    rows = {}
    for n in range(6):
        rows[f"m{n}"] = (f"User fact number {n}.", {"user_id": UID, "status": "approved", "source": "chat",
                                                    "added_at": f"{today}T0{n}:00:00Z" if n < 2 else "2020-01-01T00:00:00Z",
                                                    "concept_tags": "tea" if n >= 1 else "", "consolidation_count": 0,
                                                    "unique_query_count": 5 if n == 3 else 0})
    rows["p1"] = ("User is pending.", {"user_id": UID, "status": "pending", "added_at": "2020-01-01T00:00:00Z",
                                       "unique_query_count": 4})
    rows["p2"] = ("User is pending too.", {"user_id": UID, "status": "pending", "added_at": "2020-01-01T00:00:00Z",
                                           "unique_query_count": 0})
    rows["x1"] = ("Someone else.", {"user_id": "other", "status": "pending", "added_at": "2020-01-01T00:00:00Z"})
    return rows


ON_POST: list = []


class _Resp:
    def json(self):
        return {"choices": [{"message": {"content": "User has a steady tea habit across the week."}}]}


class _Client:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        for hook in ON_POST:
            hook()
        return _Resp()


@pytest.fixture
def store(monkeypatch):
    st = Store(_seed())
    svc = Svc(st)
    monkeypatch.setattr(ms, "get_memory_service", lambda: svc)
    monkeypatch.setattr(ms, "leased_drawers", lambda s: st)

    async def _tags(doc):
        return ["tea"]

    monkeypatch.setattr(md, "_extract_concept_tags", _tags)
    monkeypatch.setattr(md, "_promotion_score", lambda meta: 0.9 if meta.get("unique_query_count", 0) >= 3 else 0.1)
    monkeypatch.setattr(md, "_passes_quality_gate", lambda t: True)
    monkeypatch.setattr(md.httpx, "AsyncClient", _Client)
    ON_POST.clear()
    yield st
    ON_POST.clear()
    ms._MAINTENANCE_OPEN.set()


GOLDEN_REM_IDS = [["m1", "m2", "m3", "m4", "m5"], ["m0"], ["m0", "m2", "m3", "m4", "m5"], ["m1"]]


async def test_passes_write_exactly_what_they_wrote_on_the_loop(store):
    await md._rem_reinforce_pass(UID)
    assert [w[0] for w in store.writes] == GOLDEN_REM_IDS
    assert set(store.writes[1][2][0]["related_ids"].split(",")) == {"m1", "m2", "m3", "m4", "m5"}
    assert store.writes[1][2][0]["consolidation_count"] == 1
    assert store.writes[1][2][0]["concept_tags"] == "tea" and store.writes[1][1] == ["User fact number 0."]
    rem = list(store.writes)
    store.writes.clear()
    await md._deep_sleep_pass(UID)
    assert [(w[0], w[2][0]["status"], w[2][0]["consolidation_count"]) for w in store.writes] == [
        (["p1"], "approved", 1), (["p2"], "archived", 1)]
    store.writes.clear()
    await md._synthesis_pass(UID)
    (ids, docs, metas), = store.writes
    assert ids == ["syn-1"] and docs == ["User has a steady tea habit across the week."]
    assert metas[0]["concept_tags"] == "tea" and metas[0]["related_ids"] == "m0,m1,m2,m3,m4,m5"
    assert all(len(w[0]) == len(w[1]) == len(w[2]) for w in rem)


async def _max_gap_while(coro) -> float:
    gaps, done = [], asyncio.Event()

    async def tick():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(tick())
    await asyncio.sleep(0.03)
    try:
        await coro
    finally:
        done.set()
        await t
    return max(gaps)


async def test_a_slow_store_call_no_longer_blocks_the_loop(store):
    store.slow_s = 0.4
    gap = await _max_gap_while(md._deep_sleep_pass(UID))
    assert gap < 0.15, f"deep sleep pass blocked the loop {gap:.3f}s"


async def test_negative_control_the_same_call_inline_blocks(store, monkeypatch):
    async def inline(call, /, **kw):
        return call(**kw)

    monkeypatch.setattr(md, "_store", inline)
    store.slow_s = 0.4
    gap = await _max_gap_while(md._deep_sleep_pass(UID))
    assert gap >= 0.36, f"negative control saw only {gap:.3f}s"


async def _race_forget(store) -> None:
    """An erase takes the owner's lock while the pass is reading, deletes p1, then releases."""
    svc = ms.get_memory_service()
    store.slow_s = 0.2
    pass_task = asyncio.create_task(md._deep_sleep_pass(UID))
    await asyncio.sleep(0.05)                     # the pass's read is in flight off the loop
    async with svc._user_locks.setdefault(UID, asyncio.Lock()):
        store.slow_s = 0.0
        store.rows.pop("p1")                     # the erase lands
        await asyncio.sleep(0.3)                  # ... and holds the lock past the read
    await pass_task


async def test_a_forget_during_a_pass_still_wins(store):
    await _race_forget(store)
    assert "p1" not in store.rows and [w[0] for w in store.writes] == [["p2"]]


async def test_negative_control_without_the_recheck_the_forget_is_undone(store, monkeypatch):
    async def blind(svc, user_id, col, *, ids, documents, metadatas, **_):
        await md._store(col.upsert, ids=ids, documents=documents, metadatas=metadatas)

    monkeypatch.setattr(md, "_store_upsert", blind)
    await _race_forget(store)
    assert "p1" in store.rows  # resurrected: the case the re-check prevents


async def test_link_resolver_reads_off_the_loop(store, monkeypatch):
    import memory_extractor
    import person_extractor

    store.rows["pp"] = ("User met someone.", {"user_id": UID, "entity_type": "person_pending",
                                              "entity_id": "slug:robin_ash"})
    monkeypatch.setattr(md, "memory_link_resolver_enabled", lambda: True)

    async def _db(db):
        return object(), False

    async def _uuid(name, uid, db):
        return "11111111-1111-1111-1111-111111111111"

    relinked = []

    async def _relink(uid, mem_id, et, eid):
        relinked.append((mem_id, et, eid))
        return True

    monkeypatch.setattr(person_extractor, "_ensure_db", _db)
    monkeypatch.setattr(memory_extractor, "_resolve_unique_person_uuid", _uuid)
    ms.get_memory_service().relink_entity = _relink
    store.slow_s = 0.3
    t0 = time.perf_counter()
    gap = await _max_gap_while(md._resolve_pending_person_links(UID))
    res_ok = relinked == [("pp", "person", "11111111-1111-1111-1111-111111111111")]
    assert res_ok and gap < 0.15 and time.perf_counter() - t0 >= 0.3


async def test_a_soft_forget_during_a_pass_still_wins(store):
    """A forget that REJECTS the row (status, not delete) mid-pass: deep sleep must not write it back as approved."""
    svc = ms.get_memory_service()
    store.slow_s = 0.2
    task = asyncio.create_task(md._deep_sleep_pass(UID))
    await asyncio.sleep(0.05)
    async with svc._user_locks.setdefault(UID, asyncio.Lock()):
        store.slow_s = 0.0
        doc, meta = store.rows["p1"]
        store.rows["p1"] = (doc, {**meta, "status": "rejected"})
        await asyncio.sleep(0.3)
    await task
    assert store.rows["p1"][1]["status"] == "rejected" and [w[0] for w in store.writes] == [["p2"]]


async def test_negative_control_an_existence_only_recheck_undoes_the_soft_forget(store, monkeypatch):
    monkeypatch.setattr(md, "_live_ids", lambda col, uid, ids, prior: set(col.get(ids=list(ids)).get("ids") or []))
    svc = ms.get_memory_service()
    store.slow_s = 0.2
    task = asyncio.create_task(md._deep_sleep_pass(UID))
    await asyncio.sleep(0.05)
    async with svc._user_locks.setdefault(UID, asyncio.Lock()):
        store.slow_s = 0.0
        store.rows["p1"] = (store.rows["p1"][0], {**store.rows["p1"][1], "status": "rejected"})
        await asyncio.sleep(0.3)
    await task
    assert store.rows["p1"][1]["status"] == "approved"  # resurrected: the case the live predicate prevents


async def _forget_while_write_stalls_on_the_gate(store) -> float:
    """Deep sleep reads, then the compaction gate closes before its write; a forget is issued meanwhile. Returns how long
    the forget waited for the owner lock."""
    svc = ms.get_memory_service()
    gate = ms._MAINTENANCE_OPEN
    store.after_get = gate.clear
    task = asyncio.create_task(md._deep_sleep_pass(UID))
    await asyncio.sleep(0.15)                       # read done, gate closed, the write is waiting on it
    asyncio.get_running_loop().call_later(0.5, gate.set)
    t0 = time.perf_counter()
    async with svc._user_locks.setdefault(UID, asyncio.Lock()):
        store.rows.pop("p1")                        # the forget lands
    waited = time.perf_counter() - t0
    await task
    return waited


async def test_a_forget_is_not_held_behind_a_write_stalled_on_the_gate(store):
    waited = await _forget_while_write_stalls_on_the_gate(store)
    assert waited < 0.1, f"the forget waited {waited:.3f}s behind the pass"
    assert "p1" not in store.rows and [w[0] for w in store.writes] == [["p2"]]


async def test_negative_control_holding_the_lock_across_the_gate_wait_makes_the_forget_wait(store, monkeypatch):
    async def old(svc, user_id, col, *, ids, documents, metadatas, **_):   # the first version: lock around the leased call
        async with svc._user_locks.setdefault(user_id, asyncio.Lock()):
            await ms.MemoryService._run_sync(functools.partial(col.upsert, ids=ids, documents=documents, metadatas=metadatas))

    monkeypatch.setattr(md, "_store_upsert", old)
    waited = await _forget_while_write_stalls_on_the_gate(store)
    assert waited >= 0.3, f"negative control: the forget waited only {waited:.3f}s"


def _forget_m3_during_the_llm_call(store):
    def hook():
        doc, meta = store.rows["m3"]
        store.rows["m3"] = (doc, {**meta, "status": "rejected"})
    ON_POST.append(hook)


async def test_synthesis_makes_nothing_from_a_source_forgotten_after_the_scan(store):
    _forget_m3_during_the_llm_call(store)
    await md._synthesis_pass(UID)
    assert "syn-1" not in store.rows and store.writes == []


async def test_negative_control_without_the_source_recheck_a_derived_row_is_made(store, monkeypatch):
    monkeypatch.setattr(md, "_live_ids", lambda col, uid, ids, prior: set(ids))
    _forget_m3_during_the_llm_call(store)
    await md._synthesis_pass(UID)
    assert "syn-1" in store.rows  # derived from a forgotten fact: the case the re-check prevents


async def test_the_write_takes_the_owners_lock_even_under_an_alias(store):
    import inspect

    svc = ms.get_memory_service()
    lock = svc._user_locks.setdefault(UID, asyncio.Lock())
    await lock.acquire()                            # a forget holding the owner's lock
    task = asyncio.create_task(md._deep_sleep_pass(UID))
    await asyncio.sleep(0.2)
    assert store.writes == []                      # the pass waits for it
    lock.release()
    await task
    assert [w[0] for w in store.writes] == [["p1"], ["p2"]]
    # review (the soft forget) keys its lock by the row's STORED owner; the pass's write keys by the same stored owner, so a
    # write made under an ALIAS spelling of the user id still waits for a forget holding the canonical owner's lock.
    assert 'user_id = current.metadata.get("user_id") or current.metadata.get("wing")' in inspect.getsource(
        ms.MemoryService.review)
    await lock.acquire()
    doc, meta = store.rows["m1"]
    write = asyncio.create_task(md._store_upsert(svc, UID.upper(), store, ids=["m1"], documents=[doc], metadatas=[meta]))
    await asyncio.sleep(0.2)
    assert not write.done()                        # the alias did not get its own lock
    lock.release()
    await write
    assert UID.upper() not in svc._user_locks


@pytest.mark.parametrize("change", ["delete", "reject", "none", "unreadable", "pre-existing"])
async def test_synthesis_source_changes_between_check_and_ingest(store, monkeypatch, caplog, change):
    """Inject AFTER the pre-check, BEFORE the write. A deletion after the post-check is outside this contract."""
    svc = ms.get_memory_service()
    caplog.set_level("INFO", logger="memory_digest")
    ingest = svc.ingest
    checks = []
    live_ids = md._live_ids

    def check(col, uid, ids, prior):
        checks.append(tuple(ids))
        if change == "unreadable" and len(checks) == 2:
            raise RuntimeError("synthetic read failure")
        return live_ids(col, uid, ids, prior)

    async def interleaved(text, **kw):
        assert len(checks) == 1 and "m3" in checks[0]  # the precise old race window, no sleeps
        async with svc._user_locks.setdefault(UID, asyncio.Lock()):
            if change in {"delete", "pre-existing"}:
                store.rows.pop("m3")
            elif change == "reject":
                doc, meta = store.rows["m3"]
                store.rows["m3"] = (doc, {**meta, "status": "rejected"})
        if change == "pre-existing":
            # A deduplicated candidate returned by ingest is NOT a creation receipt.
            return type("R", (), {"id": "syn-1"})
        return await ingest(text, **kw)

    if change == "pre-existing":
        store.rows["syn-1"] = ("An existing candidate.", {"user_id": UID, "status": "pending", "source": "synthesis"})
    monkeypatch.setattr(md, "_live_ids", check)
    monkeypatch.setattr(svc, "ingest", interleaved)
    result = await md._synthesis_pass(UID)
    if change in {"delete", "reject"}:
        assert "syn-1" not in store.rows and result["synthesized"] == 0
        assert "SYNTHESIS_SOURCE_COMPENSATED" in caplog.text
    else:
        assert "syn-1" in store.rows
        if change == "unreadable":
            assert "SYNTHESIS_SOURCE_UNKNOWN code=RuntimeError" in caplog.text
        elif change == "pre-existing":
            assert store.rows["syn-1"][0] == "An existing candidate." and store.writes == []
        else:
            assert result["synthesized"] == 1


async def test_ingest_creation_receipt_excludes_existing_pending_rows(monkeypatch):
    from test_memory_implicit_supersede import _svc

    svc, col = _svc(monkeypatch)
    created = []
    kw = dict(user_id=UID, source="explicit_teach", status="pending", created_ids=created)
    ref = await svc.ingest("User enjoys synthetic tea blends.", **kw)
    assert ref is not None and created == [ref.id] and ref.id in col.rows
    for key in svc._seen_keys_by_user.pop(UID, ()):
        svc._seen_keys.discard(key)
    created.clear()
    again = await svc.ingest("User enjoys synthetic tea blends.", **kw)
    assert again is not None and again.id == ref.id and created == []
