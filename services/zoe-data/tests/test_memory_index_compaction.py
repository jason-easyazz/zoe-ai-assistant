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
    def __init__(self, col=None, *, new_cols=()):
        self.cols = {col.name: col} if col else {}
        self.created, self.deleted, self._new = [], [], list(new_cols)

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
    memory_service._MAINTENANCE_OPEN.set()
    yield
    memory_service._MAINTENANCE_OPEN.set()


@pytest.fixture
def palace(tmp_path):
    p = tmp_path / "mempalace"
    p.mkdir()
    (p / "chroma.sqlite3").write_bytes(b"not a real db")
    return p


def _compact(palace, client, tmp_path, **kw):
    return compact_drawers_index_sync(str(palace), backups_dir=str(tmp_path / "backups"),
                                      client=client, ef=object(), grace_s=0, **kw)


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
    assert not (tmp_path / "backups").exists()      # nothing written either
    assert memory_service._MAINTENANCE_OPEN.is_set()


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
    assert memory_service._MAINTENANCE_OPEN.is_set()


def test_failed_verification_restores(palace, tmp_path):
    client = _FakeClient(_live(20), new_cols=[_FakeCol("mempalace_drawers", "l2", short_reach=True)])
    with pytest.raises(IndexCompactionError, match="verification failed") as ei:
        _compact(palace, client, tmp_path)
    assert ei.value.report["restored"] and client.cols["mempalace_drawers"].count() == 20
    assert ei.value.report["verify"]["unfiltered_reach"] == 1


def test_second_compaction_is_refused_while_one_runs(palace, tmp_path):
    assert memory_service._COMPACT_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(IndexCompactionError, match="already running"):
            _compact(palace, _FakeClient(_live(3)), tmp_path)
    finally:
        memory_service._COMPACT_LOCK.release()


# ── the routes ───────────────────────────────────────────────────────────────────────────

def _client(monkeypatch, svc):
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


def test_compact_route_is_dark_without_the_flag(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_INDEX_COMPACT", raising=False)
    svc = _Svc({"ok": True})
    r = _client(monkeypatch, svc).post("/api/memories/maintenance/compact-index", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 404 and svc.calls == 0


def test_compact_route_maps_failures(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_INDEX_COMPACT", "1")
    h = {"X-Internal-Token": "tok"}
    busy = _client(monkeypatch, _Svc(exc=IndexCompactionError("a compaction is already running", {"changed": False})))
    assert busy.post("/api/memories/maintenance/compact-index", headers=h).status_code == 409
    failed = _client(monkeypatch, _Svc(exc=IndexCompactionError("boom (after delete; restored=True)",
                                                                {"changed": True, "restored": True})))
    r = failed.post("/api/memories/maintenance/compact-index", headers=h)
    assert r.status_code == 500 and r.json()["restored"] is True and r.json()["ok"] is False
