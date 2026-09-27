"""B0.8: zoe-data's own drawers opener (memory_service.get_drawers_collection).

It replaced ``mempalace.palace.get_collection``. What it must keep: one client per resolved
palace dir, shared with the audit collection; the existing collection opened as-is with the
cached MiniLM EF (never re-created or re-configured); and cosine only for a brand-new palace.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import memory_service  # noqa: E402

pytestmark = pytest.mark.ci_safe


class _NotFound(Exception):
    pass


class _FakeClient:
    def __init__(self, path, existing=("mempalace_drawers",), broken=False):
        self.path = path
        self.existing = set(existing)
        self.broken = broken
        self.created = []
        self.got = []

    def get_collection(self, name, embedding_function=None):
        self.got.append((name, embedding_function))
        if self.broken and name in self.existing:
            raise RuntimeError("index corrupt")
        if name not in self.existing:
            raise _NotFound(name)
        return {"name": name, "path": self.path, "ef": embedding_function}

    def list_collections(self):
        return [types.SimpleNamespace(name=n) for n in self.existing]

    def create_collection(self, name, metadata=None, embedding_function=None):
        self.created.append((name, metadata, embedding_function))
        self.existing.add(name)
        return {"name": name, "path": self.path, "ef": embedding_function, "metadata": metadata}

    def get_or_create_collection(self, name):
        return {"name": name, "path": self.path}


@pytest.fixture
def fake_chroma(monkeypatch):
    clients = {}

    def make(**kw):
        def persistent_client(*, path):
            clients[path] = _FakeClient(path, **kw)
            return clients[path]
        monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace(__version__="1.5.9", PersistentClient=persistent_client))
        return clients

    monkeypatch.setattr(memory_service, "_AUDIT_CLIENTS", {})
    sentinel_ef = object()
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: sentinel_ef)
    return make, sentinel_ef


def test_opens_existing_drawers_with_cached_ef_and_shares_client_with_audit(fake_chroma, tmp_path):
    make, ef = fake_chroma
    clients = make()
    d = tmp_path / "palace"
    d.mkdir()
    col = memory_service.get_drawers_collection(str(d) + os.sep)
    real = os.path.realpath(str(d))
    assert col == {"name": "mempalace_drawers", "path": real, "ef": ef}
    svc = memory_service.MemoryService(data_dir=str(d))
    assert svc._audit_collection()["path"] == real
    assert svc._collection()["ef"] is ef
    assert list(clients) == [real]  # ONE client for drawers + audit, every spelling
    assert clients[real].created == []  # never re-created or re-configured


def test_brand_new_palace_gets_cosine_drawers(fake_chroma, tmp_path):
    make, ef = fake_chroma
    clients = make(existing=())
    col = memory_service.get_drawers_collection(str(tmp_path))
    assert col["metadata"] == {"hnsw:space": "cosine"}
    assert clients[os.path.realpath(str(tmp_path))].created == [("mempalace_drawers", {"hnsw:space": "cosine"}, ef)]


def test_error_on_existing_collection_is_raised_not_recreated(fake_chroma, tmp_path):
    make, _ = fake_chroma
    clients = make(broken=True)
    with pytest.raises(RuntimeError, match="index corrupt"):
        memory_service.get_drawers_collection(str(tmp_path))
    assert clients[os.path.realpath(str(tmp_path))].created == []


def test_runtime_no_longer_imports_mempalace_package():
    here = os.path.join(os.path.dirname(__file__), "..")
    for name in ("memory_service.py", "zoe_agent.py"):
        src = open(os.path.join(here, name), encoding="utf-8").read()
        assert "from mempalace.palace import" not in src, name


def _palace_db(path, sysdb_version):
    import sqlite3
    path.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path / "chroma.sqlite3")
    con.execute("CREATE TABLE migrations (dir TEXT, version INTEGER)")
    con.executemany("INSERT INTO migrations VALUES ('sysdb', ?)", [(v,) for v in range(1, sysdb_version + 1)])
    con.commit()
    con.close()


@pytest.mark.parametrize("sysdb,client,ok", [
    (10, "1.5.9", True), (9, "0.6.3", True),
    (10, "0.6.3", False),  # old client on the migrated palace: dies anyway, refuse clearly
    (9, "1.5.9", False),   # new client on a 0.6 palace: would migrate it IN PLACE — refuse
])
def test_client_must_match_palace_format(tmp_path, sysdb, client, ok):
    d = tmp_path / "palace"
    _palace_db(d, sysdb)
    if ok:
        memory_service._check_palace_format(str(d), client)
    else:
        with pytest.raises(RuntimeError, match="refusing to open"):
            memory_service._check_palace_format(str(d), client)


@pytest.mark.parametrize("client", ["1.5.9", "0.6.3"])
def test_existing_db_with_unreadable_format_is_refused(tmp_path, client):
    """An EXISTING chroma.sqlite3 whose format cannot be read (no migrations table: partial
    restore / unknown schema) must fail closed for every client — a 1.x client would otherwise
    initialise or migrate it in place before the guard identified it. Only a MISSING db is new."""
    import sqlite3
    d = tmp_path / "palace"; d.mkdir()
    con = sqlite3.connect(d / "chroma.sqlite3"); con.execute("CREATE TABLE unrelated (x INTEGER)"); con.commit(); con.close()
    with pytest.raises(RuntimeError, match="cannot be identified"):
        memory_service._check_palace_format(str(d), client)
    # negative control of the allowed case: a missing db is a brand-new palace
    memory_service._check_palace_format(str(tmp_path / "fresh"), client)


def test_format_guard_runs_before_the_client_is_built(monkeypatch, tmp_path):
    d = tmp_path / "palace"
    _palace_db(d, 9)
    built = []
    monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace(
        __version__="1.5.9", PersistentClient=lambda *, path: built.append(path)))
    monkeypatch.setattr(memory_service, "_AUDIT_CLIENTS", {})
    with pytest.raises(RuntimeError):
        memory_service._palace_client(str(d))
    assert built == []  # chromadb never touched the 0.6 palace
