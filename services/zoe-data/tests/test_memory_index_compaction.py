"""In-process drawers index compaction (flag-dark ``ZOE_MEMORY_INDEX_COMPACT``).

The one hazard: ``get_drawers_collection``'s fallback CREATES the collection with
``hnsw:space=cosine`` when the name is missing — during the delete/recreate swap that would
race the rebuild into the wrong space. The maintenance gate makes it impossible: the opener
waits (bounded) while the gate is cleared. Everything here runs against fakes — no chroma.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import memory_service
from memory_service import IndexCompactionError, MemoryServiceError, compact_drawers_index_sync

pytestmark = pytest.mark.ci_safe


class _FakeCol:
    def __init__(self, name, space, *, fail_add_at=None, short_reach=False):
        self.name, self.metadata = name, {"hnsw:space": space}
        self.configuration_json = {"hnsw": {"space": space}}
        self.ids, self.embs, self.docs, self.metas = [], [], [], []
        self.add_calls, self.fail_add_at, self.short_reach = [], fail_add_at, short_reach
        self.export_short = False

    def count(self):
        return len(self.ids)

    def get(self, ids=None, include=None):
        ix = range(len(self.ids)) if ids is None else [self.ids.index(i) for i in ids]
        if self.export_short:
            ix = list(ix)[:-1]
        return {"ids": [self.ids[i] for i in ix], "embeddings": [self.embs[i] for i in ix],
                "documents": [self.docs[i] for i in ix], "metadatas": [self.metas[i] for i in ix]}

    def add(self, *, ids, embeddings, documents, metadatas):
        self.add_calls.append(len(ids))
        if self.fail_add_at is not None and len(self.add_calls) == self.fail_add_at:
            raise RuntimeError("simulated add failure")
        self.ids += list(ids); self.embs += [list(e) for e in embeddings]
        self.docs += list(documents); self.metas += list(metadatas)

    def query(self, *, query_texts, n_results, include, where=None):
        rows = self.ids if where is None else [i for i, m in zip(self.ids, self.metas) if m.get("user_id") == where["user_id"]]
        if self.short_reach and where is None:
            rows = rows[:1]
        return {"ids": [rows[:n_results]]}

    def update(self, *, ids, metadatas):
        for i, m in zip(ids, metadatas):
            self.metas[self.ids.index(i)] = dict(m)


class _FakeClient:
    """``fail_delete``: ``"partial"`` removes the collection THEN raises (chroma's SegmentAPI
    drops the segments before the sysdb row); ``"before"`` raises with the collection still
    listed; ``"always"`` raises on every delete (so the restore cannot clear the name either)."""

    def __init__(self, col=None, *, new_cols=(), fail_delete=None):
        self.cols = {col.name: col} if col else {}
        self.created, self.deleted, self._new = [], [], list(new_cols)
        self.fail_delete, self.delete_calls = fail_delete, 0

    def get_collection(self, name, embedding_function=None):
        if name not in self.cols:
            raise ValueError(f"Collection {name} does not exist")
        return self.cols[name]

    def list_collections(self):
        return [SimpleNamespace(name=n) for n in self.cols]

    def create_collection(self, name, metadata=None, embedding_function=None):
        col = self._new.pop(0) if self._new else _FakeCol(name, metadata["hnsw:space"])
        col.metadata = dict(metadata or {})
        self.cols[name] = col
        self.created.append(dict(metadata or {}))
        return col

    def delete_collection(self, name):
        self.delete_calls += 1
        mode = self.fail_delete
        if mode == "always" or (self.delete_calls == 1 and mode in ("partial", "before")):
            if mode == "partial":
                self.cols.pop(name, None)
            raise RuntimeError(f"simulated delete failure ({mode})")
        self.deleted.append(name)
        del self.cols[name]


def _live(n=250, space="l2"):
    col = _FakeCol("mempalace_drawers", space)
    for i in range(n):
        owner = "jason" if i % 2 else f"demo_bar_{i}"
        col.add(ids=[f"m{i}"], embeddings=[[0.1 * i, 1.0, 2.0]], documents=[f"fact {i}"],
                metadatas=[{"user_id": owner, "status": "approved"}])
    col.add_calls.clear()
    return col


@pytest.fixture(autouse=True)
def _gate_open():
    memory_service.clear_maintenance_block()
    memory_service._ACTIVE_OPS = 0
    memory_service._OP_THREAD.depth = 0
    yield
    memory_service.clear_maintenance_block()
    assert memory_service._ACTIVE_OPS == 0, "a test leaked a collection lease"


@pytest.fixture
def palace(tmp_path):
    p = tmp_path / "mempalace"
    p.mkdir()
    (p / "chroma.sqlite3").write_bytes(b"not a real db")
    return p


def _compact(palace, client, tmp_path, **kw):
    return compact_drawers_index_sync(str(palace), backups_dir=str(tmp_path / "backups"),
                                      client=client, ef=object(), drain_s=kw.pop("drain_s", 2.0), **kw)


# ── the gate ─────────────────────────────────────────────────────────────────────────────

def test_reads_block_while_the_gate_is_cleared_and_resume_when_set(monkeypatch):
    client = _FakeClient(_live(3))
    monkeypatch.setattr(memory_service, "_palace_client", lambda d: client)
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: object())
    monkeypatch.setattr(memory_service, "_MAINTENANCE_WAIT_S", 5.0)
    memory_service._MAINTENANCE_OPEN.clear()
    got = []
    t = threading.Thread(target=lambda: got.append(memory_service.get_drawers_collection("/x")))
    t.start()
    t.join(0.2)
    assert t.is_alive() and got == []              # blocked
    memory_service._MAINTENANCE_OPEN.set()
    t.join(2)
    assert got == [client.cols["mempalace_drawers"]]  # resumed with the (new) handle


def test_gate_timeout_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(memory_service, "_MAINTENANCE_WAIT_S", 0.05)
    memory_service._MAINTENANCE_OPEN.clear()
    with pytest.raises(MemoryServiceError, match="maintenance in progress"):
        memory_service.get_drawers_collection("/x")


def test_fallback_create_cannot_run_while_the_gate_is_cleared(monkeypatch):
    """The hazard itself: a missing collection + an open gate creates it as cosine; the same
    call with the gate cleared raises BEFORE touching the client."""
    client = _FakeClient()  # no drawers collection, as mid-swap
    monkeypatch.setattr(memory_service, "_palace_client", lambda d: client)
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: object())
    monkeypatch.setattr(memory_service, "_MAINTENANCE_WAIT_S", 0.05)
    memory_service._MAINTENANCE_OPEN.clear()
    with pytest.raises(MemoryServiceError):
        memory_service.get_drawers_collection("/x")
    assert client.created == []
    memory_service._MAINTENANCE_OPEN.set()
    memory_service.get_drawers_collection("/x")
    assert client.created == [{"hnsw:space": "cosine"}]   # the documented fallback, gate open


# ── the sequence ─────────────────────────────────────────────────────────────────────────

def test_compaction_rebuilds_in_batches_keeps_the_space_and_backs_up(palace, tmp_path):
    client = _FakeClient(_live(250))
    r = _compact(palace, client, tmp_path)
    assert r["ok"] and r["changed"] and not r["restored"]
    assert r["space"] == "l2" and client.created == [{"hnsw:space": "l2"}]
    new = client.cols["mempalace_drawers"]
    assert new.add_calls == [100, 100, 50] and new.count() == 250 and r["elements_added"] == 250
    assert r["verify"]["ok"] and r["verify"]["unfiltered_reach"] == 250 and r["verify"]["filtered_owner"] > 0
    assert (tmp_path / "backups").exists() and r["backup_tar"].endswith(".tar") and r["export"].endswith(".json")
    assert memory_service._MAINTENANCE_OPEN.is_set()


def test_compaction_preserves_a_cosine_space_too(palace, tmp_path):
    client = _FakeClient(_live(5, space="cosine"))
    assert _compact(palace, client, tmp_path)["space"] == "cosine"
    assert client.created == [{"hnsw:space": "cosine"}]


def test_short_export_aborts_before_the_delete(palace, tmp_path):
    col = _live(10)
    col.export_short = True
    client = _FakeClient(col)
    with pytest.raises(IndexCompactionError, match="aborted before any change") as ei:
        _compact(palace, client, tmp_path)
    assert client.deleted == [] and client.created == [] and ei.value.report["changed"] is False
    assert ei.value.report["status"] == "aborted"
    assert not (tmp_path / "backups").exists()      # nothing written either
    assert memory_service._MAINTENANCE_OPEN.is_set()


def test_unexpected_error_before_the_delete_is_a_structured_abort(palace, tmp_path):
    """No unstructured 500: a backup failure (backups_dir is a FILE) still raises
    IndexCompactionError with a report, nothing changed, gate reopened."""
    client = _FakeClient(_live(5))
    (tmp_path / "backups").write_text("not a dir")
    with pytest.raises(IndexCompactionError, match="aborted before any change") as ei:
        _compact(palace, client, tmp_path)
    assert ei.value.report["status"] == "aborted" and ei.value.report["changed"] is False
    assert client.deleted == [] and memory_service._MAINTENANCE_OPEN.is_set()


def test_failed_add_after_the_delete_restores_from_the_export(palace, tmp_path):
    broken = _FakeCol("mempalace_drawers", "l2", fail_add_at=2)
    client = _FakeClient(_live(250), new_cols=[broken])
    with pytest.raises(IndexCompactionError, match="restored=True") as ei:
        _compact(palace, client, tmp_path)
    rep = ei.value.report
    assert rep["changed"] and rep["restored"] and rep["restored_count"] == 250
    assert client.deleted == ["mempalace_drawers", "mempalace_drawers"]  # original, then the broken rebuild
    assert client.created == [{"hnsw:space": "l2"}] * 2
    assert client.cols["mempalace_drawers"].count() == 250
    assert rep["status"] == "restored" and rep["maintenance_blocked"] is False
    assert memory_service._MAINTENANCE_OPEN.is_set()


# ── the delete itself can raise (Codex P1, #1827 round 2) ────────────────────────────────

@pytest.mark.parametrize("mode", ["partial", "before"])
def test_delete_raising_is_recovered_with_a_structured_report(palace, tmp_path, mode):
    """``delete_collection`` raising — with the segments already gone (partial) or with the
    name still listed (before) — lands in the SAME recovery as a failed rebuild: the store
    may have changed, so the rows are put back from the export, verified, the gate reopens,
    and the error is structured (changed=True, restored=True) instead of a bare 500."""
    client = _FakeClient(_live(30), fail_delete=mode)
    with pytest.raises(IndexCompactionError, match="simulated delete failure") as ei:
        _compact(palace, client, tmp_path)
    rep = ei.value.report
    assert rep["changed"] is True and rep["restored"] is True and rep["restored_count"] == 30
    assert rep["status"] == "restored" and rep["ok"] is False and "error" in rep
    assert client.cols["mempalace_drawers"].count() == 30 and client.created == [{"hnsw:space": "l2"}]
    assert client.deleted == (["mempalace_drawers"] if mode == "before" else [])   # delete-if-listed, then rebuild
    assert memory_service._MAINTENANCE_OPEN.is_set() and memory_service.maintenance_state()["maintenance_blocked"] is False


# ── a failed restore keeps the gate CLOSED (Codex P2, #1827 round 2) ─────────────────────

def _blocked_compaction(palace, tmp_path):
    client = _FakeClient(_live(12), new_cols=[_FakeCol("mempalace_drawers", "l2", fail_add_at=1),
                                              _FakeCol("mempalace_drawers", "l2", fail_add_at=1)])
    with pytest.raises(IndexCompactionError, match="restored=False") as ei:
        _compact(palace, client, tmp_path)
    return client, ei.value.report


def test_failed_restore_keeps_the_gate_closed_and_fails_fast(palace, tmp_path, monkeypatch):
    client, rep = _blocked_compaction(palace, tmp_path)
    assert rep["status"] == "blocked" and rep["changed"] and rep["restored"] is False and "restore_error" in rep
    assert rep["maintenance_blocked"] is True and rep["backup_tar"].endswith(".tar")
    assert not memory_service._MAINTENANCE_OPEN.is_set()
    state = memory_service.maintenance_state()
    assert state["maintenance_blocked"] is True and "simulated add failure" in state["maintenance_reason"]
    assert state["backup_tar"] == rep["backup_tar"] and state["since"]
    # openers and leases fail FAST with the reason, not after the 60 s wait — and never create
    monkeypatch.setattr(memory_service, "_palace_client", lambda d: client)
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: object())
    t0 = time.monotonic()
    with pytest.raises(MemoryServiceError, match="FAILED CLOSED"):
        memory_service.get_drawers_collection("/x")
    with pytest.raises(MemoryServiceError, match="FAILED CLOSED"):
        with memory_service.collection_op():
            pass
    assert time.monotonic() - t0 < 1.0 and len(client.created) == 2   # only the two failed rebuilds
    # a further compaction is refused too (operator recovery, not a retry loop)
    with pytest.raises(IndexCompactionError, match="gate is closed") as ei:
        _compact(palace, client, tmp_path)
    assert ei.value.report["status"] == "blocked" and ei.value.report["changed"] is False
    memory_service.clear_maintenance_block()
    assert memory_service._MAINTENANCE_OPEN.is_set()


def test_waiter_that_entered_before_the_block_is_released_with_the_reason(monkeypatch):
    monkeypatch.setattr(memory_service, "_MAINTENANCE_WAIT_S", 5.0)
    monkeypatch.setattr(memory_service, "_MAINTENANCE_POLL_S", 0.05)
    memory_service._MAINTENANCE_OPEN.clear()                  # a compaction is in progress
    errors = []

    def waiter():
        try:
            with memory_service.collection_op():
                pass
        except MemoryServiceError as exc:
            errors.append(str(exc))

    t = threading.Thread(target=waiter)
    t.start()
    t.join(0.2)
    assert t.is_alive()                                        # blocked, waiting for the reopen
    memory_service._block_maintenance("restore failed", {"backup_tar": "/b.tar"})
    t.join(2)
    assert not t.is_alive() and errors and "FAILED CLOSED" in errors[0] and "/b.tar" in errors[0]


def test_failed_verification_restores(palace, tmp_path):
    client = _FakeClient(_live(20), new_cols=[_FakeCol("mempalace_drawers", "l2", short_reach=True)])
    with pytest.raises(IndexCompactionError, match="verification failed") as ei:
        _compact(palace, client, tmp_path)
    assert ei.value.report["restored"] and client.cols["mempalace_drawers"].count() == 20
    assert ei.value.report["verify"]["unfiltered_reach"] == 1


def test_second_compaction_is_refused_while_one_runs(palace, tmp_path):
    assert memory_service._COMPACT_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(IndexCompactionError, match="already running") as ei:
            _compact(palace, _FakeClient(_live(3)), tmp_path)
        assert ei.value.report["status"] == "busy"
    finally:
        memory_service._COMPACT_LOCK.release()


# ── the export must not truth-test chroma's ndarray (Codex P1, #1827) ────────────────────

class _ArrayLike:
    """chroma 1.5.x returns ``get(include=["embeddings"])`` as a NumPy ndarray: it has a
    length and iterates, and ``bool()`` on it RAISES. The list-based fakes above cannot
    catch an ``x or []`` on it — this one can."""

    def __init__(self, rows):
        self._rows = [list(r) for r in rows]

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, ix):
        return self._rows[ix]

    def __bool__(self):
        raise ValueError("The truth value of an array with more than one element is ambiguous")


def _array_col(n, wrap):
    col = _live(n)
    plain_get = col.get

    def get(ids=None, include=None):
        out = plain_get(ids=ids, include=include)
        out["embeddings"] = wrap(out["embeddings"])
        return out

    col.get = get
    return col


def test_export_handles_an_ndarray_like_embeddings_column(palace, tmp_path):
    client = _FakeClient(_array_col(120, _ArrayLike))
    r = _compact(palace, client, tmp_path)
    assert r["ok"] and r["rows"] == 120 and r["dims"] == 3
    assert client.cols["mempalace_drawers"].embs[7] == [0.1 * 7, 1.0, 2.0]   # bit-identical, as lists


def test_export_handles_a_real_numpy_ndarray(palace, tmp_path):
    np = pytest.importorskip("numpy")
    client = _FakeClient(_array_col(120, lambda rows: np.asarray(rows, dtype=np.float32)))
    r = _compact(palace, client, tmp_path)
    assert r["ok"] and r["rows"] == 120
    assert all(isinstance(x, float) for x in client.cols["mempalace_drawers"].embs[0])


# ── in-flight operations are drained, not raced (Codex P1, #1827) ────────────────────────

def _hold_lease(hold: threading.Event, started: threading.Event, ended: list):
    with memory_service.collection_op():
        started.set()
        hold.wait(5)
        ended.append(time.monotonic())


def test_in_flight_op_delays_the_compaction_until_it_ends(palace, tmp_path):
    client = _FakeClient(_live(10))
    col = client.cols["mempalace_drawers"]
    exported = []
    plain_get = col.get
    col.get = lambda ids=None, include=None: (exported.append(time.monotonic()), plain_get(ids=ids, include=include))[1]
    hold, started, ended = threading.Event(), threading.Event(), []
    t = threading.Thread(target=_hold_lease, args=(hold, started, ended))
    t.start()
    assert started.wait(2) and memory_service._ACTIVE_OPS == 1
    threading.Timer(0.4, hold.set).start()                         # the op finishes 0.4 s from now
    r = _compact(palace, client, tmp_path, drain_s=3.0)
    t.join(2)
    assert r["ok"] and r["drain_seconds"] >= 0.3
    assert exported and ended and exported[0] >= ended[0]          # export only after the op ended


def test_never_ending_op_aborts_with_no_change_within_the_bound(palace, tmp_path):
    client = _FakeClient(_live(10))
    hold, started, ended = threading.Event(), threading.Event(), []
    t = threading.Thread(target=_hold_lease, args=(hold, started, ended))
    t.start()
    try:
        assert started.wait(2)
        t0 = time.monotonic()
        with pytest.raises(IndexCompactionError, match="could not drain 1 in-flight") as ei:
            _compact(palace, client, tmp_path, drain_s=0.2)
        assert time.monotonic() - t0 < 1.5
        assert ei.value.report["changed"] is False and client.deleted == [] and client.created == []
        assert ei.value.report["status"] == "busy"
        assert not (tmp_path / "backups").exists()
        assert memory_service._MAINTENANCE_OPEN.is_set()           # readers are released again
    finally:
        hold.set()
        t.join(2)


# ── direct collection users hold the lease (Codex P1, #1827 round 2) ─────────────────────

class _SlowCol:
    """A collection whose ``get`` blocks (a long digest scan) until released."""

    def __init__(self, hold: threading.Event, started: threading.Event):
        self.hold, self.started, self.calls = hold, started, []

    def get(self, **kw):
        self.started.set()
        self.hold.wait(5)
        self.calls.append(kw)
        return {"ids": [], "metadatas": []}

    def upsert(self, **kw):
        self.calls.append(kw)


def test_leased_drawers_holds_the_lease_only_for_each_call(monkeypatch):
    svc = memory_service.MemoryService(data_dir="/x")
    slow = _SlowCol(threading.Event(), threading.Event())
    svc._collection = lambda: slow                              # the digest tests' own patch style
    col = memory_service.leased_drawers(svc)
    seen = []
    svc._collection = lambda: (seen.append((memory_service._ACTIVE_OPS, memory_service._OP_THREAD.depth)), slow)[1]
    col.get(where={"user_id": {"$eq": "u"}}, include=["metadatas"])
    slow.hold.set()
    col.upsert(ids=["a"], documents=["d"], metadatas=[{}])
    assert seen == [(1, 1), (1, 1)] and memory_service._ACTIVE_OPS == 0   # leased per call, released after
    assert slow.calls[1]["ids"] == ["a"]
    with pytest.raises(AttributeError):
        col._collection                                           # no private passthrough


def test_long_running_digest_pass_makes_the_compaction_wait_or_refuse(palace, tmp_path, monkeypatch):
    """The reviewer's race: a digest pass mid-``col.get`` through the leased proxy counts as
    an in-flight op, so the drain sees it — it waits when the op ends in time and refuses
    (status busy → 409) after the budget, with nothing changed."""
    client = _FakeClient(_live(8))
    svc = memory_service.MemoryService(data_dir=str(palace))
    hold, started = threading.Event(), threading.Event()
    slow = _SlowCol(hold, started)
    svc._collection = lambda: slow
    col = memory_service.leased_drawers(svc)
    t = threading.Thread(target=lambda: col.get(where={"user_id": {"$eq": "u"}}))
    t.start()
    try:
        assert started.wait(2) and memory_service._ACTIVE_OPS == 1
        with pytest.raises(IndexCompactionError, match="could not drain 1 in-flight") as ei:
            _compact(palace, client, tmp_path, drain_s=0.2)
        assert ei.value.report["status"] == "busy" and client.deleted == []
        threading.Timer(0.3, hold.set).start()                  # the pass finishes; now it drains
        r = _compact(palace, client, tmp_path, drain_s=3.0)
        assert r["ok"] and r["drain_seconds"] >= 0.2
    finally:
        hold.set()
        t.join(2)


def test_compact_route_returns_409_when_the_drain_budget_is_exhausted(palace, tmp_path, monkeypatch):
    """End to end through the real service method + route, with the drain budget made small."""
    client = _FakeClient(_live(4))
    monkeypatch.setattr(memory_service, "_palace_client", lambda d: client)
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: object())
    monkeypatch.setattr(memory_service, "_MAINTENANCE_DRAIN_S", 0.2)
    monkeypatch.setattr(memory_service, "_COMPACT_BACKUPS_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("ZOE_MEMORY_INDEX_COMPACT", "1")
    svc = memory_service.MemoryService(data_dir=str(palace))
    hold, started = threading.Event(), threading.Event()
    slow = _SlowCol(hold, started)
    svc._collection = lambda: slow
    t = threading.Thread(target=lambda: memory_service.leased_drawers(svc).get())
    t.start()
    try:
        assert started.wait(2)
        r = _client(monkeypatch, svc).post("/api/memories/maintenance/compact-index", headers={"X-Internal-Token": "tok"})
        assert r.status_code == 409, r.text
        assert r.json()["status"] == "busy" and r.json()["changed"] is False
    finally:
        hold.set()
        t.join(2)


def test_digest_passes_never_take_a_raw_handle():
    """Source lockdown: the four async passes go through ``leased_drawers``; a raw
    ``svc._collection()`` in memory_digest would reintroduce the unleased op."""
    import inspect

    import memory_digest as md

    assert "._collection()" not in inspect.getsource(md)
    for fn in (md._resolve_pending_person_links, md._rem_reinforce_pass, md._deep_sleep_pass, md._synthesis_pass):
        assert "leased_drawers(svc)" in inspect.getsource(fn), fn.__name__


def test_lease_counter_returns_to_zero_after_exceptions_and_nesting():
    with pytest.raises(RuntimeError):
        with memory_service.collection_op():
            assert memory_service._ACTIVE_OPS == 1
            with memory_service.collection_op():                  # re-entrant: still ONE lease
                assert memory_service._ACTIVE_OPS == 1 and memory_service._OP_THREAD.depth == 2
            raise RuntimeError("op failed")
    assert memory_service._ACTIVE_OPS == 0 and memory_service._OP_THREAD.depth == 0
    with pytest.raises(RuntimeError):
        memory_service._leased_call(lambda: (_ for _ in ()).throw(RuntimeError("fn failed")))
    assert memory_service._ACTIVE_OPS == 0


def test_no_lease_is_admitted_while_the_gate_is_closed_and_run_sync_holds_one(monkeypatch):
    memory_service._MAINTENANCE_OPEN.clear()
    with pytest.raises(MemoryServiceError, match="maintenance in progress"):
        with memory_service.collection_op(timeout=0.05):
            pass
    assert memory_service._ACTIVE_OPS == 0
    memory_service._MAINTENANCE_OPEN.set()
    client = _FakeClient(_live(2))
    monkeypatch.setattr(memory_service, "_palace_client", lambda d: client)
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: object())
    seen = []

    def op():
        seen.append((memory_service._ACTIVE_OPS, memory_service._OP_THREAD.depth))
        return memory_service.get_drawers_collection("/x")            # leased: no second gate wait

    import asyncio
    assert asyncio.run(memory_service.MemoryService._run_sync(op)) is client.cols["mempalace_drawers"]
    assert seen == [(1, 1)] and memory_service._ACTIVE_OPS == 0


def test_compaction_refuses_to_run_under_its_own_lease(palace, tmp_path):
    with memory_service.collection_op():
        with pytest.raises(IndexCompactionError, match="wait for itself"):
            _compact(palace, _FakeClient(_live(3)), tmp_path)
    assert memory_service._ACTIVE_OPS == 0


# ── the routes ───────────────────────────────────────────────────────────────────────────

def _prefer_zoe_data_modules():
    """In a COMBINED session (this file + tests/unit/…) ``tests/conftest.py`` inserts
    ``services/zoe-auth`` ahead of ``services/zoe-data``, so the routers' ``from models
    import …`` resolves to zoe-auth's package and the router import fails. The lanes run
    separately in CI; here, pin zoe-data first before the first router import."""
    import os
    import sys

    zoe_data = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [zoe_data] + [p for p in sys.path if os.path.abspath(p or ".") != zoe_data]
    for name in ("models", "routers"):
        mod = sys.modules.get(name)
        if mod is not None and not str(getattr(mod, "__file__", "") or "").startswith(zoe_data):
            del sys.modules[name]


def _client(monkeypatch, svc):
    _prefer_zoe_data_modules()
    import routers.memories as memories_mod

    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memories_mod, "_svc", lambda: svc)
    app = FastAPI()
    app.include_router(memories_mod.router)
    return TestClient(app)


class _Svc:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    async def index_health(self):
        return {"collection": "mempalace_drawers", "tombstone_ratio": 6.17, "compaction_advised": True}

    async def compact_index(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def test_routes_need_the_internal_token(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_INDEX_COMPACT", "1")
    c = _client(monkeypatch, _Svc({"ok": True}))
    assert c.get("/api/memories/maintenance/index-health").status_code == 403       # TestClient is not loopback
    assert c.post("/api/memories/maintenance/compact-index").status_code == 403
    h = {"X-Internal-Token": "tok"}
    assert c.get("/api/memories/maintenance/index-health", headers=h).json()["compaction_advised"] is True
    assert c.post("/api/memories/maintenance/compact-index", headers=h).json() == {"ok": True}


def _sqlite_palace(tmp_path, live=258, added=1591):
    import pickle
    import sqlite3

    p = tmp_path / "palace"
    p.mkdir()
    con = sqlite3.connect(p / "chroma.sqlite3")
    con.executescript("CREATE TABLE collections (id TEXT, name TEXT); CREATE TABLE segments (id TEXT, scope TEXT, collection TEXT);"
                      "CREATE TABLE embeddings (id INTEGER, segment_id TEXT);"
                      "CREATE TABLE max_seq_id (segment_id TEXT, seq_id INTEGER);"
                      "CREATE TABLE embeddings_queue (seq_id INTEGER, operation INTEGER, topic TEXT);")
    con.execute("INSERT INTO collections VALUES ('c1', 'mempalace_drawers')")
    con.execute("INSERT INTO segments VALUES ('m1', 'METADATA', 'c1')")
    con.execute("INSERT INTO segments VALUES ('v1', 'VECTOR', 'c1')")
    con.execute("INSERT INTO max_seq_id VALUES ('v1', 100)")
    con.executemany("INSERT INTO embeddings VALUES (?, 'm1')", [(i,) for i in range(live)])
    con.commit(); con.close()
    (p / "v1").mkdir()
    (p / "v1" / "index_metadata.pickle").write_bytes(pickle.dumps({"total_elements_added": added}))
    return p


def test_health_route_answers_while_the_gate_is_closed(monkeypatch, tmp_path):
    """Codex P2: the health read is what an operator calls DURING a compaction to watch it.
    The real ``MemoryService.index_health`` must not take the collection lease — a leased
    call would block on the closed gate (here: fail fast at 50 ms → 503)."""
    svc = memory_service.MemoryService(data_dir=str(_sqlite_palace(tmp_path)))
    monkeypatch.setattr(memory_service, "_MAINTENANCE_WAIT_S", 0.05)
    c = _client(monkeypatch, svc)
    memory_service._MAINTENANCE_OPEN.clear()
    r = c.get("/api/memories/maintenance/index-health", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200, r.text
    assert r.json()["tombstone_ratio"] == 6.17 and r.json()["compaction_advised"] is True
    assert memory_service._ACTIVE_OPS == 0


def test_health_route_serialises_an_empty_collection_and_reports_a_closed_gate(monkeypatch, tmp_path):
    """Codex P2: live=0 with elements ever added used to make the ratio ``inf`` and Starlette's
    ``JSONResponse`` (allow_nan=False) raise — the weekly trigger then saw health as down.
    And the gate state rides on the same payload."""
    svc = memory_service.MemoryService(data_dir=str(_sqlite_palace(tmp_path, live=0, added=40)))
    c = _client(monkeypatch, svc)
    r = c.get("/api/memories/maintenance/index-health", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["live_rows"] == 0 and body["elements_added"] == 40
    assert body["tombstone_ratio"] is None and body["ratio_known"] is False and body["compaction_advised"] is None
    assert body["maintenance_open"] is True and body["maintenance_blocked"] is False and body["maintenance_reason"] is None
    memory_service._block_maintenance("restore failed: boom", {"backup_tar": "/b.tar", "export": "/e.json"})
    body = c.get("/api/memories/maintenance/index-health", headers={"X-Internal-Token": "tok"}).json()
    assert body["maintenance_blocked"] is True and body["maintenance_open"] is False
    assert "boom" in body["maintenance_reason"] and body["backup_tar"] == "/b.tar"


def test_compact_route_is_dark_without_the_flag(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_INDEX_COMPACT", raising=False)
    svc = _Svc({"ok": True})
    r = _client(monkeypatch, svc).post("/api/memories/maintenance/compact-index", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 404 and svc.calls == 0


def test_compact_route_maps_failures(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_INDEX_COMPACT", "1")
    h = {"X-Internal-Token": "tok"}
    busy = _client(monkeypatch, _Svc(exc=IndexCompactionError("could not drain 1 in-flight op", {"changed": False, "status": "busy"})))
    assert busy.post("/api/memories/maintenance/compact-index", headers=h).status_code == 409
    failed = _client(monkeypatch, _Svc(exc=IndexCompactionError("boom (after delete; restored=True)",
                                                                {"changed": True, "restored": True, "status": "restored"})))
    r = failed.post("/api/memories/maintenance/compact-index", headers=h)
    assert r.status_code == 500 and r.json()["restored"] is True and r.json()["ok"] is False
    blocked = _client(monkeypatch, _Svc(exc=IndexCompactionError("boom (after delete; restored=False)",
                                                                 {"changed": True, "restored": False, "status": "blocked",
                                                                  "maintenance_blocked": True})))
    r = blocked.post("/api/memories/maintenance/compact-index", headers=h)
    assert r.status_code == 500 and r.json()["status"] == "blocked" and r.json()["maintenance_blocked"] is True
