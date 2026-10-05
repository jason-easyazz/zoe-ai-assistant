"""The memory-loss class (docs/knowledge/memory-loss-audit-2026-10-05.md), service side.

* a: ``MemoryService`` refuses a live-palace write from a test/harness context (the audit rows a
  suite wrote for the owner's real id) — wired at the choke points, not just the helper module;
* d: a hard delete writes a content-free ``delete_user`` audit row FIRST (fail closed), the user's
  per-row trail is purged but the tombstone survives; no other module may remove rows directly;
* weekly no-op rewrite: exact duplicates are archived (audit row, row kept), same words on a
  different entity are left alone, near-duplicates still go through ``edit``.

Each test names its negative control.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import live_store_guard as g
import memory_digest
import memory_service
from memory_service import MemoryService, MemoryServiceError

SERVICE_DIR = pathlib.Path(memory_service.__file__).resolve().parent


# ── a: the guard is wired into the service ───────────────────────────────────────────────────

@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZOE_LIVE_PALACE_DIR", raising=False)
    palace = tmp_path / ".mempalace"
    palace.mkdir()
    return str(palace)


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
async def test_audit_append_does_not_swallow_a_guard_trip(live_dir):
    """``_append_audit`` is best-effort for I/O errors; a guard trip must propagate (it used to be
    swallowed as 'audit append failed' — the audit-only phantom rows were exactly this)."""
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation):
        await svc._append_audit(mem_id="x", user_id="jason", actor="t", action="ingest",
                                before=None, after={"text": "t"})


@pytest.fixture
def harness(monkeypatch):
    """A declared harness (``ZOE_HARNESS=1``): it may OPEN the live palace read-side, so the write
    guards — not the open guard — are what stand between it and the household's rows."""
    monkeypatch.setattr(g, "non_service_context", lambda: "harness")


@pytest.mark.asyncio
async def test_harness_ingest_for_a_real_id_is_refused_at_ingest(live_dir, harness):
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation, match="refusing ingest for"):
        await svc.ingest("The owner's fixture sentence.", user_id="jason", source="chat_regex")


def test_harness_row_write_is_refused_at_the_row_door(live_dir, harness):
    """``_write_row`` is the door review/edit/supersede also use — guarded on its own, so a path
    that never calls ``ingest`` is covered too."""
    svc = MemoryService(data_dir=live_dir)
    with pytest.raises(g.LiveStoreViolation, match="refusing row write for"):
        svc._write_row("zoe_jason_x", "text", {"user_id": "jason"})


@pytest.mark.asyncio
async def test_harness_audit_row_for_a_real_id_is_refused_even_with_the_drawers_stubbed(live_dir, harness):
    """THE original bug: the drawers were a test fake, the audit went to the live palace. The audit
    door is guarded on its own, so stubbing the drawers cannot reopen it."""
    svc = MemoryService(data_dir=live_dir)
    svc._write_row = lambda *a, **k: None            # drawers stubbed out, as test_mempalace_integration did
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


def test_control_harness_may_write_its_own_demo_id_to_a_scratch_dir(tmp_path, harness):
    MemoryService(data_dir=str(tmp_path / "scratch"))._write_row  # attribute access only
    g.assert_write_allowed(str(tmp_path / "scratch"), "jason", "row write")   # not the live dir: allowed


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


# ── d: a hard delete leaves a record ─────────────────────────────────────────────────────────

class _Col:
    def __init__(self, ids=None, fail_upsert=False, log=None):
        self.ids = ids or []
        self.upserts, self.deleted, self.where = [], [], None
        self.fail_upsert, self.log = fail_upsert, log if log is not None else []

    def get(self, **kw):
        self.where = kw.get("where")
        return {"ids": list(self.ids)}

    def delete(self, **kw):
        self.deleted.extend(kw.get("ids") or [])
        self.log.append("delete")

    def upsert(self, **kw):
        if self.fail_upsert:
            raise RuntimeError("audit store down")
        self.upserts.append(kw)
        self.log.append("tombstone")


def _svc(rows, audit, tmp_path):
    svc = MemoryService(data_dir=str(tmp_path / "scratch"))
    svc._collection = lambda: rows
    svc._audit_collection = lambda: audit
    return svc


@pytest.mark.asyncio
async def test_delete_user_writes_a_content_free_tombstone_before_removing(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    log: list[str] = []
    rows, audit = _Col(["zoe_jason_aa", "zoe_jason_bb"], log=log), _Col(["audit-1"], log=log)
    removed = await _svc(rows, audit, tmp_path).delete_user("jason", actor="admin", reason="rtbf")
    assert removed == 2 and rows.deleted == ["zoe_jason_aa", "zoe_jason_bb"]
    assert log.index("tombstone") < log.index("delete"), "record first, then remove"
    (tomb,) = audit.upserts
    md = tomb["metadatas"][0]
    assert md["action"] == "delete_user" and md["actor"] == "admin" and md["reason"] == "rtbf"
    body = json.loads(md["before"])
    assert body["rows_removed"] == 2 and len(body["id_hashes"]) == 2 and body["truncated"] is False
    assert "zoe_jason_aa" not in json.dumps(tomb), "ids are hashed; nothing text-like is recorded"
    # the purge of the user's own per-row trail must exclude tombstones, so they outlive it
    assert audit.where == {"$and": [{"user_id": "jason"}, {"action": {"$ne": "delete_user"}}]}


@pytest.mark.asyncio
async def test_delete_user_fails_closed_when_the_tombstone_cannot_be_written(tmp_path):
    """No record → no removal (negative control for 'delete first, audit later')."""
    rows, audit = _Col(["zoe_jason_aa"]), _Col(fail_upsert=True)
    with pytest.raises(MemoryServiceError):
        await _svc(rows, audit, tmp_path).delete_user("jason", actor="admin")
    assert rows.deleted == [], "rows were removed although the removal could not be recorded"


@pytest.mark.asyncio
async def test_empty_synthetic_sweep_is_silent_but_an_empty_real_user_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    audit = _Col()
    await _svc(_Col(), audit, tmp_path).delete_user("demo_bar_ab0e0001", actor="internal:forget-synthetic")
    assert audit.upserts == []                       # nothing removed, nothing worth a row
    await _svc(_Col(), audit, tmp_path).delete_user("jason", actor="admin")
    assert len(audit.upserts) == 1                   # a real user's trail was purged: recorded


@pytest.mark.asyncio
async def test_tombstone_hash_list_is_capped_inside_the_audit_json_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    ids = [f"zoe_demo_x_{i:04d}" for i in range(500)]
    audit = _Col()
    await _svc(_Col(ids), audit, tmp_path).delete_user("demo_bar_ab0e0001", actor="a")
    raw = audit.upserts[0]["metadatas"][0]["before"]
    assert len(raw) < 4000 and json.loads(raw)["truncated"] is True and json.loads(raw)["rows_removed"] == 500


def _removal_calls(source: str) -> list[tuple[str, str]]:
    """(enclosing function, call) for every ``<x>.delete(ids=|where=...)`` / ``delete_collection``."""
    hits = []

    class V(ast.NodeVisitor):
        fn = "<module>"

        def visit_FunctionDef(self, node):
            old, self.fn = self.fn, node.name
            self.generic_visit(node)
            self.fn = old
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            f = node.func
            if isinstance(f, ast.Attribute):
                kws = {k.arg for k in node.keywords}
                if f.attr == "delete_collection" or (f.attr == "delete" and kws & {"ids", "where"}):
                    hits.append((self.fn, f.attr))
            self.generic_visit(node)

    V().visit(ast.parse(source))
    return hits


# The only functions allowed to remove palace rows. Every one is either audited (delete_user), a
# verified rebuild with export + tar backup + count parity (compaction), or deletes the audit
# trail itself on the same audited path.
_REMOVERS = {
    ("memory_service.py", "_delete_ids"), ("memory_service.py", "_delete_audit_for_user_sync"),
    ("memory_service.py", "compact_drawers_index_sync"), ("memory_service.py", "_restore_drawers"),
}


def test_no_module_removes_palace_rows_outside_the_audited_paths():
    offenders = []
    for path in sorted(SERVICE_DIR.glob("*.py")) + sorted((SERVICE_DIR / "routers").glob("*.py")):
        for fn, call in _removal_calls(path.read_text()):
            if (path.name, fn) not in _REMOVERS:
                offenders.append(f"{path.name}:{fn}:{call}")
    assert not offenders, f"unaudited removal path(s) — route through MemoryService.delete_user: {offenders}"


def test_control_the_scanner_does_flag_a_raw_delete():
    assert _removal_calls("def purge(col):\n    col.delete(ids=['a'])\n") == [("purge", "delete")]
    assert _removal_calls("def rebuild(c):\n    c.delete_collection('x')\n") == [("rebuild", "delete_collection")]
    assert _removal_calls("def ok(d):\n    d.pop('k')\n") == []


# ── weekly consolidation: no-op rewrites become archives ─────────────────────────────────────

class _Ref:
    def __init__(self, id_, text, **md):
        self.id, self.text = id_, text
        self.metadata = {"confidence": 0.7, "added_at": id_, "memory_type": "fact", **md}


class _Svc:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def list_by_status(self, **_kw):
        return list(self.rows)

    async def review(self, mem_id, *, decision, actor, edits=None, note=None, **_kw):
        self.calls.append((mem_id, decision, actor, edits, note))
        return object()


@pytest.mark.asyncio
async def test_exact_duplicates_are_archived_not_rewritten():
    svc = _Svc([_Ref("a", "Mum likes tea."), _Ref("b", "mum likes   tea"), _Ref("c", "Dad drives a ute.")])
    merged = await memory_digest._merge_near_duplicates(svc, "jason")
    assert merged == 1
    assert [(c[0], c[1]) for c in svc.calls] == [("b", "archive")]   # the earlier row is the keeper
    assert all(c[1] != "edit" for c in svc.calls), "an identical-text 'edit' is the no-op in-place rewrite"
    assert svc.calls[0][2] == "consolidation" and "exact duplicate" in svc.calls[0][4]


@pytest.mark.asyncio
async def test_same_words_on_different_entities_are_left_completely_alone():
    """138 legacy rows are 3 texts x 47 distinct notes/journals/people: not duplicates."""
    svc = _Svc([_Ref(f"n{i}", "Shopping list", entity_type="note", entity_id=f"e{i}", memory_type="note")
                for i in range(5)])
    assert await memory_digest._merge_near_duplicates(svc, "family-admin") == 0
    assert svc.calls == [], "no archive, no edit, no audit row: nothing is rewritten"


@pytest.mark.asyncio
async def test_different_memory_type_is_not_a_duplicate():
    svc = _Svc([_Ref("a", "Mum likes tea.", memory_type="fact"), _Ref("b", "Mum likes tea.", memory_type="preference")])
    assert await memory_digest._merge_near_duplicates(svc, "jason") == 0 and svc.calls == []


@pytest.mark.asyncio
async def test_control_near_duplicates_still_merge_through_edit():
    a = "Mum likes strong black tea every morning with toast"
    b = "Mum likes strong black tea every morning with toast please"
    svc = _Svc([_Ref("a", a), _Ref("b", b)])
    assert await memory_digest._merge_near_duplicates(svc, "jason") == 1
    assert [c[1] for c in svc.calls] == ["edit"], "the near-duplicate path is unchanged"
