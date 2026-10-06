"""The memory-loss class (docs/knowledge/memory-loss-audit-2026-10-05.md), service side.

* a: ``MemoryService`` refuses a live-palace write from a test/harness context (the audit rows a
  suite wrote for the owner's real id) — wired at the choke points AND at the drawers accessor that
  every direct writer (digest passes, tick_access, supersede, zoe_agent) shares;
* d: a hard delete writes a content-free INTENT row first (fail closed) and a DONE row after the delete
  succeeded, the user's per-row trail is purged but the tombstones survive; no other module may remove
  rows, and ``_delete_ids`` has exactly one caller.

Each test names its negative control.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import live_store_guard as g
import memory_reject_ledger as reject_ledger
import memory_service
from memory_service import MemoryService, MemoryServiceError

SERVICE_DIR = pathlib.Path(memory_service.__file__).resolve().parent
REPO = SERVICE_DIR.parent.parent


# ── a: the guard is wired into the service ───────────────────────────────────────────────────

@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    """A directory the guard treats as the household palace (declared via the additive override —
    HOME is deliberately NOT how the guard finds the live dir any more)."""
    palace = tmp_path / "household-palace"
    palace.mkdir()
    monkeypatch.setenv("ZOE_LIVE_PALACE_DIR", str(palace))
    return str(palace)


@pytest.fixture
def harness(monkeypatch):
    """A declared harness (``ZOE_HARNESS=1``): it may OPEN the live palace, so the write guards — not
    the open guard — are what stand between it and the household's rows."""
    monkeypatch.setattr(g, "non_service_context", lambda: "harness")


def test_palace_client_refuses_the_live_dir_from_pytest(live_dir):
    with pytest.raises(g.LiveStoreViolation):
        memory_service._palace_client(live_dir)


def test_palace_client_guard_runs_before_the_client_cache(live_dir, monkeypatch):
    """A cached client must not launder the check (it runs on EVERY call)."""
    import os
    monkeypatch.setitem(memory_service._AUDIT_CLIENTS, os.path.realpath(live_dir), object())
    with pytest.raises(g.LiveStoreViolation):
        memory_service._palace_client(live_dir)


@pytest.mark.asyncio
async def test_ingest_for_a_real_id_against_the_live_dir_fails_loudly(live_dir):
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation):
        await svc.ingest("The owner's fixture sentence.", user_id="jason", source="chat_regex")


@pytest.mark.asyncio
async def test_a_trip_survives_broad_except_exception_handlers(live_dir, harness):
    """The write paths are full of ``except Exception`` best-effort handlers; a trip is a
    BaseException so none of them can turn it into a warning and a green test."""
    svc = MemoryService(data_dir=live_dir)
    assert not issubclass(g.LiveStoreViolation, Exception)
    before = g.trip_count()
    swallowed = False
    try:
        try:
            await svc.ingest("x sentence here", user_id="jason", source="chat_regex")
        except Exception:                       # the shape of expert_dispatch / extractors / consolidation
            swallowed = True
    except g.LiveStoreViolation:
        pass
    assert not swallowed and g.trip_count() == before + 1


@pytest.mark.asyncio
async def test_audit_append_does_not_swallow_a_guard_trip(live_dir, harness):
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation, match="refusing audit append for"):
        await svc._append_audit(mem_id="x", user_id="jason", actor="t", action="ingest",
                                before=None, after={"text": "t"})


@pytest.mark.asyncio
async def test_control_same_ingest_against_a_scratch_dir_still_works(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path / "scratch"))
    writes = []
    svc._write_row = lambda mem_id, text, md: writes.append(mem_id)

    async def _noop(**_kw):
        return None

    svc._append_audit = _noop
    svc._get_sync = lambda _id: None
    ref = await svc.ingest("The owner's fixture sentence.", user_id="jason", source="chat_regex")
    assert ref is not None and writes == [ref.id]


@pytest.mark.asyncio
async def test_harness_ingest_for_a_real_id_is_refused_at_ingest(live_dir, harness):
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation, match="refusing ingest for"):
        await svc.ingest("The owner's fixture sentence.", user_id="jason", source="chat_regex")


def test_harness_row_write_is_refused_at_the_row_door(live_dir, harness):
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation, match="refusing row write for"):
        svc._write_row("zoe_jason_x", "text", {"user_id": "jason"})


@pytest.mark.asyncio
async def test_harness_audit_row_for_a_real_id_is_refused_even_with_the_drawers_stubbed(live_dir, harness):
    """THE original bug: the drawers were a test fake, the audit went to the live palace."""
    svc = MemoryService(data_dir=live_dir)
    svc._write_row = lambda *a, **k: None
    with pytest.raises(g.LiveStoreViolation, match="refusing audit append for"):
        await svc._append_audit(mem_id="zoe_jason_x", user_id="jason", actor="t", action="ingest",
                                before=None, after={"text": "t"})


@pytest.mark.asyncio
async def test_harness_delete_user_is_refused_before_anything_is_touched(live_dir, harness):
    rows, audit = _Col(["zoe_jason_x"]), _Col()
    svc = MemoryService(data_dir=live_dir)
    svc._collection, svc._audit_collection = (lambda: rows), (lambda: audit)
    with pytest.raises(g.LiveStoreViolation, match="refusing delete_user for"):
        await svc.delete_user("jason", actor="admin")
    assert rows.deleted == [] and audit.upserts == []


class _RawCol:
    """Stands in for a chroma collection: records mutations."""
    def __init__(self):
        self.calls = []

    def get(self, **kw):
        return {"ids": []}

    def add(self, *a, **k): self.calls.append(("add", a, k))
    def upsert(self, *a, **k): self.calls.append(("upsert", a, k))
    def update(self, *a, **k): self.calls.append(("update", a, k))
    def delete(self, *a, **k): self.calls.append(("delete", a, k))


def test_direct_drawer_writers_share_one_guarded_accessor(live_dir, harness):
    """The digest passes' ``col.upsert``, ``tick_access``'s / supersede's ``col.update`` and zoe_agent all get
    their handle from ``get_drawers_collection``; in a harness on the live palace that handle is a
    write-checking proxy: a real user's metadata is refused, ids-only mutations (no user to prove
    synthetic) are refused, reads pass, a demo id passes."""
    col = _RawCol()
    proxy = g.guard_collection(col, live_dir)
    assert isinstance(proxy, g.GuardedCollection)
    proxy.get(ids=["a"])                                              # reads are untouched
    proxy.upsert(ids=["a"], documents=["t"], metadatas=[{"user_id": "demo_bar_ab0e0001"}])
    assert [c[0] for c in col.calls] == ["upsert"]
    for op, kw in (("upsert", {"ids": ["a"], "documents": ["t"], "metadatas": [{"user_id": "jason"}]}),
                   ("update", {"ids": ["a"], "metadatas": [{"user_id": "jason"}]}),
                   ("update", {"ids": ["a"], "metadatas": [{"wing": "jason"}]}),
                   ("delete", {"ids": ["a"]}),                       # no user attached → cannot prove synthetic
                   ("add", {"ids": ["a"], "documents": ["t"], "metadatas": [{"user_id": "guest"}]})):
        with pytest.raises(g.LiveStoreViolation):
            getattr(proxy, op)(**kw)
    with pytest.raises(g.LiveStoreViolation):                         # positional metadatas (ids, docs, metas)
        proxy.upsert(["a"], ["t"], [{"user_id": "jason"}])
    assert len(col.calls) == 1, "a refused mutation must not reach chroma"


def test_control_service_and_scratch_get_the_raw_collection(live_dir, tmp_path, monkeypatch):
    col = _RawCol()
    assert g.guard_collection(col, str(tmp_path / "scratch")) is col          # not the live palace
    monkeypatch.setattr(g, "non_service_context", lambda: "")
    assert g.guard_collection(col, live_dir) is col                           # the service: zero overhead


def test_get_drawers_collection_hands_out_the_guarded_proxy(live_dir, harness, monkeypatch):
    class _Client:
        def get_collection(self, name, embedding_function=None):
            return _RawCol()

    monkeypatch.setattr(memory_service, "_palace_client", lambda _d: _Client())
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: object())
    monkeypatch.setattr(memory_service, "_wait_for_maintenance_gate", lambda: None)
    col = memory_service.get_drawers_collection(live_dir)
    with pytest.raises(g.LiveStoreViolation):
        col.update(ids=["a"], metadatas=[{"user_id": "jason"}])               # tick_access / supersede shape


# ── d: a hard delete leaves a record ─────────────────────────────────────────────────────────

class _Col:
    def __init__(self, ids=None, fail_upsert_action=None, fail_delete=False, log=None):
        self.ids = ids or []
        self.upserts, self.deleted, self.where = [], [], None
        self.fail_upsert_action, self.fail_delete, self.log = fail_upsert_action, fail_delete, log if log is not None else []

    def get(self, **kw):
        self.where = kw.get("where")
        return {"ids": list(self.ids)}

    def delete(self, **kw):
        if self.fail_delete:
            raise RuntimeError("store down")
        self.deleted.extend(kw.get("ids") or [])
        self.log.append("delete")

    def upsert(self, **kw):
        action = kw["metadatas"][0]["action"]
        if self.fail_upsert_action in (True, action):
            raise RuntimeError("audit store down")
        self.upserts.append(kw)
        self.log.append(action)


def _svc(rows, audit, tmp_path):
    svc = MemoryService(data_dir=str(tmp_path / "scratch"))
    svc._collection = lambda: rows
    svc._audit_collection = lambda: audit
    return svc


@pytest.mark.asyncio
async def test_delete_user_records_intent_first_and_completion_after(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    log: list[str] = []
    rows, audit = _Col(["zoe_jason_aa", "zoe_jason_bb"], log=log), _Col(["audit-1"], log=log)
    removed = await _svc(rows, audit, tmp_path).delete_user("jason", actor="admin", reason="rtbf")
    assert removed == 2 and rows.deleted == ["zoe_jason_aa", "zoe_jason_bb"]
    assert log.index("delete_user") < log.index("delete") < log.index("delete_user_done")
    intent, done = (u["metadatas"][0] for u in audit.upserts)
    assert intent["action"] == "delete_user" and intent["actor"] == "admin" and intent["reason"] == "rtbf"
    body = json.loads(intent["before"])
    assert body["rows_targeted"] == 2 and len(body["id_hashes"]) == 2 and body["truncated"] is False
    assert "rows_removed" not in body, "the intent must not claim a removal that has not happened"
    assert done["action"] == "delete_user_done" and json.loads(done["before"]) == {"rows_removed": 2}
    assert intent["mempalace_id"] == done["mempalace_id"] and intent["mempalace_id"].startswith("delete_user:")
    assert "zoe_jason_aa" not in json.dumps(audit.upserts), "ids are hashed; nothing text-like is recorded"
    # the purge of the user's own per-row trail must exclude both tombstone actions
    assert audit.where == {"$and": [{"user_id": "jason"}, {"action": {"$nin": [
        "delete_user", "delete_user_done", "forget_erase", "forget_erase_done"]}}]}


@pytest.mark.asyncio
async def test_a_failed_delete_leaves_an_intent_and_no_false_removed_record(tmp_path):
    rows, audit = _Col(["zoe_jason_aa"], fail_delete=True), _Col()
    with pytest.raises(MemoryServiceError):
        await _svc(rows, audit, tmp_path).delete_user("jason", actor="admin")
    actions = [u["metadatas"][0]["action"] for u in audit.upserts]
    assert actions == ["delete_user"], "attempted is recorded; 'done' / 'rows_removed' must not exist"
    assert "rows_removed" not in audit.upserts[0]["metadatas"][0]["before"]


@pytest.mark.asyncio
async def test_delete_user_fails_closed_when_the_intent_cannot_be_written(tmp_path):
    """No record → no removal (negative control for 'delete first, audit later')."""
    rows, audit = _Col(["zoe_jason_aa"]), _Col(fail_upsert_action=True)
    with pytest.raises(MemoryServiceError):
        await _svc(rows, audit, tmp_path).delete_user("jason", actor="admin")
    assert rows.deleted == [], "rows were removed although the removal could not be recorded"


@pytest.mark.asyncio
async def test_a_sweep_that_matches_nothing_writes_no_tombstone(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    audit = _Col()
    await _svc(_Col(), audit, tmp_path).delete_user("demo_bar_ab0e0001", actor="internal:forget-synthetic")
    await _svc(_Col(), audit, tmp_path).delete_user("jason", actor="admin")
    assert audit.upserts == []                      # nothing removed, nothing worth a permanent row


@pytest.mark.asyncio
async def test_tombstone_hash_list_is_capped_inside_the_audit_json_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    ids = [f"zoe_demo_x_{i:04d}" for i in range(500)]
    audit = _Col()
    await _svc(_Col(ids), audit, tmp_path).delete_user("demo_bar_ab0e0001", actor="a")
    raw = audit.upserts[0]["metadatas"][0]["before"]
    assert len(raw) < 4000 and json.loads(raw)["truncated"] is True and json.loads(raw)["rows_targeted"] == 500


# The structural lockdown. Every way a module can remove palace rows, found by AST over the WHOLE
# service tree (packages included) and the scripts that touch chroma.
_BAD_ATTRS = {"delete", "delete_collection", "delete_where"}
_CHROMA_RECEIVER_HINTS = ("client", "chroma", "palace", "system", "col")   # for ``reset`` (chroma's wipe-everything)

# (path suffix, enclosing function) allowed to remove rows. Service: the audited delete_user path plus the
# verified compaction rebuild. Scripts: operator tools that take a backup first or work on a scratch copy.
_REMOVERS = {
    ("memory_service.py", "_delete_ids"), ("memory_service.py", "_delete_audit_for_user_sync"),
    ("memory_service.py", "compact_drawers_index_sync"), ("memory_service.py", "_restore_drawers"),
    # forget path (erase_rows): the audited row erase - tombstone intent row first, rows, their per-row audit trail
    ("memory_service.py", "_delete_audit_for_rows_sync"),
    # the ZMB disk lab's collection wrapper: real Chroma, but ONLY over a throwaway directory under the lab's scratch root
    ("scripts/perf/zmb/lab_driver.py", "delete"),
    # the forgotten ledger's own row (a salted hash, never palace text): an explicit re-teach releases it (#1883)
    ("memory_forgotten.py", "release"),
    ("scripts/maintenance/compact_drawers_index.py", "compact"),
    ("scripts/maintenance/check_memory_tombstones.py", "compact"),
    ("scripts/maintenance/remediate_ownerless_memories.py", "delete_rows"),
    ("scripts/maintenance/remediate_ownerless_memories.py", "main"),    # ``args.delete`` — the argparse flag, not a call
    ("scripts/maintenance/chroma_migrate_rehearsal.py", "probe_roundtrip"),
    ("scripts/maintenance/chroma_migrate_rehearsal.py", "probe_recall"),
}


def _removal_sites(source: str) -> list[tuple[str, str]]:
    """(enclosing function, what) for every removal-shaped reference: ``x.delete`` / ``x.delete_collection``
    in ANY form (call with positional/keyword/``**kw`` args, ``d = col.delete`` aliases, ``getattr(x,
    "delete")``) and chroma's ``client.reset()``. Route decorators (``@router.delete``) are not removals."""
    tree = ast.parse(source)
    decorators = {id(d) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  for dec in n.decorator_list for d in ast.walk(dec)}
    hits: list[tuple[str, str]] = []

    class V(ast.NodeVisitor):
        fn = "<module>"

        def visit_FunctionDef(self, node):
            old, self.fn = self.fn, node.name
            self.generic_visit(node)
            self.fn = old
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Attribute(self, node):
            if id(node) not in decorators:
                if node.attr in _BAD_ATTRS:
                    hits.append((self.fn, node.attr))
                elif node.attr == "reset" and any(h in ast.unparse(node.value).lower() for h in _CHROMA_RECEIVER_HINTS):
                    hits.append((self.fn, "reset"))
            self.generic_visit(node)

        def visit_Call(self, node):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == "getattr" and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value in _BAD_ATTRS | {"reset"}):
                hits.append((self.fn, f"getattr:{node.args[1].value}"))
            self.generic_visit(node)

    V().visit(tree)
    return hits


def _tracked_sources():
    """Every non-test .py under services/zoe-data (all packages) + the scripts that open chroma."""
    for p in sorted(SERVICE_DIR.rglob("*.py")):
        rel = p.relative_to(SERVICE_DIR).as_posix()
        if rel.startswith(("tests/", "venv", ".venv", "node_modules")) or "/venv" in rel:
            continue
        yield rel, p.read_text()
    for p in sorted((REPO / "scripts").rglob("*.py")):
        text = p.read_text()
        if any(k in text for k in ("chromadb", "memory_service", "PersistentClient", "palace_client")):
            yield p.relative_to(REPO).as_posix(), text


def test_no_module_removes_palace_rows_outside_the_audited_paths():
    offenders = []
    for rel, text in _tracked_sources():
        for fn, what in _removal_sites(text):
            if not any(rel.endswith(suffix) and fn == name for suffix, name in _REMOVERS):
                offenders.append(f"{rel}:{fn}:{what}")
    assert not offenders, f"unaudited removal path(s) — route through MemoryService.delete_user: {offenders}"


def test_delete_ids_has_exactly_the_two_audited_callers():
    """``_delete_ids`` is the unaudited primitive; the tombstone lives in its callers: ``delete_user`` (the
    hard delete) and ``erase_rows`` (the forget path: content-free ``forget_erase`` intent row first, ``_done``
    after). A new caller (any module, any alias) would skip it and pass the scanner above."""
    refs = []
    for rel, text in _tracked_sources():
        tree = ast.parse(text)
        for fn_node in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for n in ast.walk(fn_node):
                if (isinstance(n, ast.Attribute) and n.attr == "_delete_ids") or (isinstance(n, ast.Name) and n.id == "_delete_ids"):
                    refs.append((rel, fn_node.name))
    assert set(refs) - {("memory_service.py", "_delete_ids")} == {
        ("memory_service.py", "delete_user"), ("memory_service.py", "erase_rows")}, refs


@pytest.mark.parametrize("source,expected", [
    ("def purge(col):\n    col.delete(ids=['a'])\n", [("purge", "delete")]),
    ("def purge(col):\n    col.delete(['a'])\n", [("purge", "delete")]),                     # positional ids
    ("def purge(col, **kw):\n    col.delete(**kw)\n", [("purge", "delete")]),                # **kwargs
    ("def purge(col):\n    d = col.delete\n    d(ids=['a'])\n", [("purge", "delete")]),      # alias
    ("def purge(col):\n    getattr(col, 'delete')(ids=['a'])\n", [("purge", "getattr:delete")]),
    ("def rebuild(c):\n    c.delete_collection('x')\n", [("rebuild", "delete_collection")]),
    ("def wipe(client):\n    client.reset()\n", [("wipe", "reset")]),
    ("def wipe(self):\n    self._palace.reset()\n", [("wipe", "reset")]),
])
def test_control_the_scanner_flags_every_walk_around(source, expected):
    assert _removal_sites(source) == expected


@pytest.mark.parametrize("source", [
    "def ok(d):\n    d.pop('k')\n",
    "@router.delete('/x')\nasync def remove():\n    return 1\n",                              # a route, not a removal
    "def ok(ctx, tok):\n    ctx.reset(tok)\n",                                                # ContextVar.reset
])
def test_control_the_scanner_leaves_innocent_code_alone(source):
    assert _removal_sites(source) == []


@pytest.fixture(autouse=False)
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "ledger.json"))
    reject_ledger.reset_for_tests()
    yield
    reject_ledger.reset_for_tests()


# ── the metric is incremented exactly once, and only for the write-quality gate ───────────────

def _gate_counter(source, reason):
    from memory_metrics import memory_quality_reject_count as c
    return c.labels(source=source, reason=reason)._value.get()


def test_a_gate_reject_increments_the_quality_counter_exactly_once(_ledger):
    before = _gate_counter("digest", "question_mark")
    reject_ledger.record_reject("digest", "question_mark")
    assert _gate_counter("digest", "question_mark") == before + 1


def test_the_three_logging_sites_no_longer_double_count(caplog, _ledger):
    import expert_dispatch
    import memory_extractor
    for mod in (expert_dispatch, memory_extractor):
        before = _gate_counter("voice_fact", "too_short")
        mod._record_quality_reject("voice_fact", "too_short", "x")
        assert _gate_counter("voice_fact", "too_short") == before + 1, mod.__name__


def test_service_refusals_do_not_pollute_the_gate_counter(tmp_path, _ledger):
    import memory_service
    svc = memory_service.MemoryService(data_dir=str(tmp_path / "scratch"))
    before = _gate_counter("voice_fact", "dedup")
    for status in ("pii_reject", "tombstone_drop", "opt_out", "dedup"):
        svc._bump(status, "voice_fact")
    svc._bump("ok", "voice_fact")           # control: a successful write is not a refusal
    svc._bump("error", "voice_fact")        # control: a failed write is loud elsewhere, not a reject
    s = reject_ledger.summary(24)
    assert s["rejected"] == 4 and set(s["reasons"]) == {"pii_reject", "tombstone_drop", "opt_out", "dedup"}
    assert _gate_counter("voice_fact", "dedup") == before, "dedup is not a write-quality-gate reject"


