"""Forgotten text is PHYSICALLY gone ("forgotten means forever", owner 2026-10-06).

A Chroma API ``delete`` removes the row and nothing else: the text stays in the SQLite file (free pages,
in-page slack, the FTS5 trigram index, the ``embeddings_queue`` write-ahead log) and in orphan HNSW segment
directories. ``memory_residue`` is the verifier + the SQLite scrub, ``memory_service.erase_residue_sync`` runs
it under the maintenance gate after every hard delete / forget, ``MemoryService.erase_rows`` is the forget
path's drawer erase. Real Chroma on a throwaway directory (never the household palace); the embedder is a
deterministic stand-in (network-free).

Every claim names its negative control: the same flow with the erase switched OFF
(``ZOE_MEMORY_PHYSICAL_ERASE=0``) must leave the canary on disk - otherwise the scan measures nothing.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys

import pytest

chromadb = pytest.importorskip("chromadb")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import memory_residue  # noqa: E402
import memory_service  # noqa: E402
from memory_service import MemoryService  # noqa: E402

pytestmark = pytest.mark.ci_safe

CANARY = "canary-7f3a9c-Quillfeather"
USER = "demo_resid_7f3a9c01"
OTHER = "demo_resid_7f3a9c02"


def _vec(text: str) -> list[float]:
    h = hashlib.sha256(text.encode()).digest()
    return [((h[i % 32] + i) % 97) / 97.0 + 0.01 for i in range(384)]


class _Embedding:
    """A Chroma collection that embeds ``documents`` / ``query_texts`` deterministically (no model)."""

    def __init__(self, col):
        self._col = col

    def __getattr__(self, name):
        return getattr(self._col, name)

    def _emb(self, kw):
        if kw.get("embeddings") is None and kw.get("documents") is not None:
            kw = dict(kw, embeddings=[_vec(d) for d in kw["documents"]])
        return kw

    def upsert(self, **kw):
        return self._col.upsert(**self._emb(kw))

    def add(self, **kw):
        return self._col.add(**self._emb(kw))

    def query(self, **kw):
        if kw.get("query_texts") is not None:
            kw = dict(kw, query_embeddings=[_vec(t) for t in kw.pop("query_texts")])
        return self._col.query(**kw)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "palace")
    os.makedirs(data_dir)
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    svc = MemoryService(data_dir=data_dir)
    client = memory_service._palace_client(data_dir)
    drawers = _Embedding(client.get_or_create_collection("mempalace_drawers"))
    svc._collection = lambda: drawers
    return svc, data_dir, drawers


def _seed(svc, drawers, *, n_other=40):
    """A canary row (+ its audit trail) for USER, a second USER row that does NOT name it, and filler rows of
    OTHER - enough rows that Chroma's pages and FTS index are real."""
    meta = {"user_id": USER, "status": "approved", "source_excerpt": f"my neighbour {CANARY} keeps bees"}
    svc._write_row("zoe_canary", f"My neighbour {CANARY} keeps bees on Marlow Street", meta)
    svc._write_row("zoe_plain", "My favourite colour is green and I like gardens", {"user_id": USER, "status": "approved"})
    for i in range(n_other):
        svc._write_row(f"zoe_other_{i}", f"Ordinary household sentence number {i} about the weather",
                       {"user_id": OTHER, "status": "approved"})
    svc._append_audit_sync("zoe_canary", USER, USER, "archive", {"status": "approved", "text": f"neighbour {CANARY}"},
                           {"status": "archived", "text": f"neighbour {CANARY}"}, f"forget_entity:{CANARY}")


def _hits(data_dir: str) -> dict:
    return memory_residue.scan_palace(data_dir, CANARY, copy=True, scratch=os.path.dirname(data_dir))["tokens"][CANARY]


# ── the instrument and the problem ──────────────────────────────────────────────────────────────

def test_the_scan_finds_a_live_canary_and_an_api_delete_does_not_remove_it(palace):
    """Positive control for the instrument AND the measured defect: after ``col.delete`` the text is still on disk."""
    svc, data_dir, drawers = palace
    _seed(svc, drawers)
    assert _hits(data_dir)["total"] > 0, "instrument blind: a stored canary was not found"
    svc._delete_ids(["zoe_canary"])
    assert drawers.get(ids=["zoe_canary"])["ids"] == []                       # gone from the API ...
    after = _hits(data_dir)
    assert after["total"] > 0, "expected the API delete to leave residue (the defect this module fixes)"
    assert "chroma.sqlite3" in after["files"]


def test_scrub_sqlite_erases_it_and_the_store_keeps_working(palace):
    svc, data_dir, drawers = palace
    _seed(svc, drawers)
    svc._delete_ids(["zoe_canary"])
    svc._delete_audit_for_rows_sync(["zoe_canary"])
    assert _hits(data_dir)["total"] > 0
    rep = memory_residue.scrub_sqlite(data_dir)
    assert rep["vacuumed"] and rep["fts_rebuilt"]
    assert _hits(data_dir)["total"] == 0
    # the open client still reads, writes and searches ...
    assert len(drawers.get(where={"user_id": OTHER})["ids"]) == 40
    drawers.upsert(ids=["after"], documents=["written after the scrub"], metadatas=[{"user_id": USER}])
    assert drawers.get(ids=["after"])["ids"] == ["after"]
    assert drawers.query(query_texts=["gardens"], n_results=3)["ids"][0]
    assert drawers.get(where_document={"$contains": "gardens"})["ids"] == ["zoe_plain"]   # FTS still answers


def test_blanking_the_queue_never_touches_a_row_that_was_re_added(palace):
    """ADD(x) ... DELETE(x) ... ADD(x): only the first ADD is blanked; the re-add (a re-teach) must survive."""
    svc, data_dir, drawers = palace
    drawers.upsert(ids=["x"], documents=[f"first life {CANARY}"], metadatas=[{"user_id": USER}])
    drawers.delete(ids=["x"])
    drawers.upsert(ids=["x"], documents=["second life, taught again"], metadatas=[{"user_id": USER}])
    con = sqlite3.connect(os.path.join(data_dir, "chroma.sqlite3"), isolation_level=None)
    try:
        blanked = memory_residue.blank_deleted_queue_text(con)
        left = [r[0] for r in con.execute("SELECT metadata FROM embeddings_queue WHERE id = 'x' ORDER BY seq_id")]
    finally:
        con.close()
    assert blanked >= 1
    assert CANARY not in "".join(m or "" for m in left)
    assert any("second life" in (m or "") for m in left), "the re-added row's record was blanked"


def test_scrub_sqlite_never_creates_a_database(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert "skipped" in memory_residue.scrub_sqlite(empty)
    assert not (empty / "chroma.sqlite3").exists()


# ── orphan HNSW segment directories ─────────────────────────────────────────────────────────────

def test_orphan_segment_dirs_are_found_and_removed_and_live_ones_are_kept(palace, tmp_path):
    svc, data_dir, drawers = palace
    _seed(svc, drawers, n_other=3)
    live = {c.name for c in os.scandir(data_dir) if c.is_dir()}
    orphan = os.path.join(data_dir, "11111111-2222-3333-4444-555555555555")
    os.makedirs(orphan)
    with open(os.path.join(orphan, "data_level0.bin"), "wb") as fh:
        fh.write(b"\x00" * 64 + CANARY.encode() + b"\x00" * 64)       # a pre-rebuild index with residue in it
    assert [p.name for p in memory_residue.orphan_segment_dirs(data_dir)] == ["11111111-2222-3333-4444-555555555555"]
    assert _hits(data_dir)["files"].get("11111111-2222-3333-4444-555555555555/data_level0.bin") == 1
    assert memory_residue.remove_orphan_segments(data_dir) == ["11111111-2222-3333-4444-555555555555"]
    assert not os.path.exists(orphan)
    assert {c.name for c in os.scandir(data_dir) if c.is_dir()} == live     # nothing a collection owns was touched
    assert len(drawers.get(where={"user_id": OTHER})["ids"]) == 3


# ── the service paths ───────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_user_leaves_no_trace_on_disk(palace):
    svc, data_dir, drawers = palace
    _seed(svc, drawers)
    assert _hits(data_dir)["total"] > 0
    removed = await svc.delete_user(USER, actor="admin", reason="rtbf")
    assert removed == 2
    assert _hits(data_dir)["total"] == 0, "the audited hard delete left the text on disk"
    rep = svc.last_erase_report
    assert rep["enabled"] and rep["ok"] is True and rep["verify"]["checked"] and rep["verify"]["clean"]
    assert len(drawers.get(where={"user_id": OTHER})["ids"]) == 40        # another member's rows untouched
    tomb = svc._audit_collection().get(where={"action": "delete_user"})
    assert len(tomb["ids"]) == 1 and CANARY not in str(tomb)               # the tombstone names no text


@pytest.mark.asyncio
async def test_negative_control_erase_off_leaves_the_text(palace, monkeypatch):
    """Same flow, ZOE_MEMORY_PHYSICAL_ERASE=0: the canary MUST still be on disk (red without the fix)."""
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    svc, data_dir, drawers = palace
    _seed(svc, drawers)
    await svc.delete_user(USER, actor="admin")
    assert _hits(data_dir)["total"] > 0
    assert svc.last_erase_report is None or svc.last_erase_report.get("enabled") is False


@pytest.mark.asyncio
async def test_erase_rows_is_the_forget_path_and_touches_only_the_callers_rows(palace):
    svc, data_dir, drawers = palace
    _seed(svc, drawers)
    # a row of ANOTHER member whose id the caller names must not be erased (ownership guard)
    res = await svc.erase_rows(USER, ["zoe_canary", "zoe_other_0", "zoe_missing"], actor=USER,
                               reason="forgotten by request (entity)")
    assert res["rows_removed"] == 1 and res["audit_removed"] == 1
    assert res["physical"]["ok"] is True
    assert _hits(data_dir)["total"] == 0
    assert sorted(drawers.get(ids=["zoe_other_0", "zoe_plain"])["ids"]) == ["zoe_other_0", "zoe_plain"]
    tomb = svc._audit_collection().get(where={"action": {"$in": ["forget_erase", "forget_erase_done"]}})
    assert len(tomb["ids"]) == 2 and CANARY not in str(tomb) and "Quillfeather" not in str(tomb)
    # a later delete_user of the same member keeps those tombstones (they are the record OF the removal)
    await svc.delete_user(USER, actor="admin")
    kept = svc._audit_collection().get(where={"action": {"$in": ["forget_erase", "forget_erase_done"]}})
    assert len(kept["ids"]) == 2


@pytest.mark.asyncio
async def test_erase_rows_fails_closed_when_the_intent_row_cannot_be_written(palace, monkeypatch):
    svc, data_dir, drawers = palace
    _seed(svc, drawers, n_other=2)

    def boom(*a, **k):
        raise RuntimeError("audit store down")
    monkeypatch.setattr(svc, "_append_audit_sync", boom)
    with pytest.raises(memory_service.MemoryServiceError):
        await svc.erase_rows(USER, ["zoe_canary"], actor=USER)
    assert drawers.get(ids=["zoe_canary"])["ids"] == ["zoe_canary"], "nothing may be deleted without the intent row"


@pytest.mark.asyncio
async def test_scrub_residue_verifies_a_token_and_is_idempotent(palace):
    svc, data_dir, drawers = palace
    _seed(svc, drawers)
    svc._delete_ids(["zoe_canary"])
    svc._delete_audit_for_rows_sync(["zoe_canary"])
    first = await svc.scrub_residue(tokens=[CANARY])
    assert first["ok"] is True and first["verify"]["clean"] and first["scrub"]["sqlite"]["vacuumed"]
    second = await svc.scrub_residue(tokens=[CANARY])
    assert second["ok"] is True
    assert _hits(data_dir)["total"] == 0


def test_the_gate_is_reopened_after_an_erase_even_when_it_fails(palace, monkeypatch):
    svc, data_dir, drawers = palace

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(memory_residue, "scrub_sqlite", boom)
    rep = memory_service.erase_residue_sync(data_dir, needles=[CANARY])
    assert rep["ok"] is False and "disk full" in str(rep["scrub"]["error"])
    assert memory_service._MAINTENANCE_OPEN.is_set()
    drawers.upsert(ids=["still-writable"], documents=["gate reopened"], metadatas=[{"user_id": USER}])


def test_text_left_in_an_index_file_triggers_the_rebuild_and_a_second_verify(palace, monkeypatch):
    """The HNSW branch: residue in a non-sqlite file -> rebuild (flag on) -> verify again."""
    svc, data_dir, drawers = palace
    calls = []
    verdicts = iter([{"checked": True, "needles": 1, "sqlite_hits": 0, "index_hits": 2, "clean": False, "seconds": 0},
                     {"checked": True, "needles": 1, "sqlite_hits": 0, "index_hits": 0, "clean": True, "seconds": 0}])
    monkeypatch.setattr(memory_service, "_verify_residue", lambda *_a, **_k: next(verdicts))
    monkeypatch.setattr(memory_service, "compact_drawers_index_sync",
                        lambda *a, **k: calls.append("rebuild") or {"status": "ok", "rows": 3, "seconds": 0.1})
    monkeypatch.setenv("ZOE_MEMORY_INDEX_COMPACT", "1")
    rep = memory_service.erase_residue_sync(data_dir, needles=[CANARY])
    assert calls == ["rebuild"] and rep["ok"] is True and rep["rebuild"]["status"] == "ok"
    # negative control: flag off -> no rebuild, the verdict stays red
    monkeypatch.delenv("ZOE_MEMORY_INDEX_COMPACT")
    verdicts = iter([{"checked": True, "needles": 1, "sqlite_hits": 0, "index_hits": 2, "clean": False, "seconds": 0}])
    monkeypatch.setattr(memory_service, "_verify_residue", lambda *_a, **_k: next(verdicts))
    calls.clear()
    rep = memory_service.erase_residue_sync(data_dir, needles=[CANARY])
    assert calls == [] and rep["ok"] is False


def test_heap_scrub_posture_is_reported(monkeypatch):
    monkeypatch.delenv("MALLOC_PERTURB_", raising=False)
    monkeypatch.setattr(memory_residue, "_HEAP_SCRUB_ON", False)
    assert memory_service.heap_scrub_active() is False
    monkeypatch.setenv("MALLOC_PERTURB_", "85")
    assert memory_service.heap_scrub_active() is True
    monkeypatch.setenv("MALLOC_PERTURB_", "0")
    assert memory_service.heap_scrub_active() is False        # 0 = off in glibc
    monkeypatch.delenv("MALLOC_PERTURB_")
    monkeypatch.setattr(memory_residue, "_HEAP_SCRUB_ON", True)
    assert memory_service.heap_scrub_active() is True         # the runtime mallopt twin
    assert memory_residue.scrubbed_env({})["MALLOC_PERTURB_"] == "85"


def test_enable_heap_scrub_is_idempotent_reversible_and_never_raises(monkeypatch):
    monkeypatch.delenv("MALLOC_PERTURB_", raising=False)
    monkeypatch.setattr(memory_residue, "_HEAP_SCRUB_ON", False)
    first = memory_residue.enable_heap_scrub()
    try:
        assert first in (True, False)                         # False only where there is no glibc
        assert memory_residue.enable_heap_scrub() is first
        assert memory_residue.heap_scrub_on() is first
    finally:
        memory_residue.disable_heap_scrub()                   # leave the test process as it was
    assert memory_residue.heap_scrub_on() is False


# ── the forget handler end to end (real intent handler, real Chroma) ────────────────────────────

def _forget_env(monkeypatch, svc):
    import types
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    stub = types.ModuleType("pending_suggestions")

    async def no_offers(_u, _n):
        return 0
    stub.resolve_person_offers_by_name = no_offers
    monkeypatch.setitem(sys.modules, "pending_suggestions", stub)


@pytest.mark.asyncio
async def test_forget_entity_erases_every_status_and_leaves_nothing_on_disk(palace, monkeypatch):
    """'forget everything about X': the approved rows are archived AND erased; the pending / superseded /
    rejected rows naming X (an edited fact's old version, a model's candidate) are erased too; nobody else's
    rows, and nothing unrelated of the caller's, is touched; no byte of the canary is left on disk."""
    import intent_router
    svc, data_dir, drawers = palace
    for rid, status in (("a1", "approved"), ("s1", "superseded"), ("p1", "pending"), ("r1", "rejected"),
                        ("x1", "archived")):
        svc._write_row(rid, f"My neighbour {CANARY} keeps bees {rid}",
                       {"user_id": USER, "status": status, "source_excerpt": f"{CANARY} {rid}"})
    svc._write_row("keep", "I like green and gardens", {"user_id": USER, "status": "approved"})
    svc._write_row("theirs", f"The other member also mentions {CANARY} here", {"user_id": OTHER, "status": "approved"})
    svc._append_audit_sync("a1", USER, USER, "ingest", {}, {"text": CANARY}, "")
    _forget_env(monkeypatch, svc)

    reply = await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "Quillfeather"}), USER)
    assert "forgotten" in (reply or "").lower(), reply
    left = sorted(drawers.get()["ids"])
    assert "keep" in left and "theirs" in left, "an unrelated row / another member's row was erased"
    assert not {"a1", "s1", "p1", "r1", "x1"} & set(left), left
    # the only canary bytes left are the OTHER member's own row (never the caller's to erase)
    hits = _hits(data_dir)
    assert hits["total"] > 0 and "chroma.sqlite3" in hits["files"]
    await svc.erase_rows(OTHER, ["theirs"], actor=OTHER)
    assert _hits(data_dir)["total"] == 0


@pytest.mark.asyncio
async def test_forget_entity_with_nothing_approved_still_erases_the_old_versions(palace, monkeypatch):
    import intent_router
    svc, data_dir, drawers = palace
    svc._write_row("s1", f"Old version naming {CANARY}", {"user_id": USER, "status": "superseded"})
    _forget_env(monkeypatch, svc)
    await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "Quillfeather"}), USER)
    assert drawers.get(ids=["s1"])["ids"] == []
    assert _hits(data_dir)["total"] == 0


@pytest.mark.asyncio
async def test_forget_entity_negative_control_archive_only_keeps_the_text(palace, monkeypatch):
    """ZOE_MEMORY_PHYSICAL_ERASE=0: the handler archives (the old behaviour) and the text stays - red without the fix."""
    import intent_router
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    svc, data_dir, drawers = palace
    svc._write_row("a1", f"My neighbour {CANARY} keeps bees", {"user_id": USER, "status": "approved"})
    _forget_env(monkeypatch, svc)
    await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "Quillfeather"}), USER)
    got = drawers.get(ids=["a1"], include=["metadatas"])
    assert got["ids"] == ["a1"] and got["metadatas"][0]["status"] == "archived"
    assert _hits(data_dir)["total"] > 0


# ── the compaction rebuild erases what the collection delete leaves behind ──────────────────────

@pytest.fixture
def real_compaction_palace(tmp_path, monkeypatch):
    """The service's own drawers opener over real Chroma with a deterministic embedder (no model)."""
    from chromadb import EmbeddingFunction

    class _EF(EmbeddingFunction):
        def __call__(self, input):  # noqa: A002 - chroma's signature
            return [_vec(t) for t in input]

        @staticmethod
        def name() -> str:
            return "default"

    ef = _EF()
    monkeypatch.setattr(memory_service, "_drawers_embedding_function", lambda: ef)
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    data_dir = str(tmp_path / "compact-palace")
    os.makedirs(data_dir)
    return MemoryService(data_dir=data_dir), data_dir, tmp_path


def test_compaction_erases_the_freed_pages_and_the_orphan_directory(real_compaction_palace):
    """chroma's ``delete_collection`` frees every page of the old collection (all of its text) and leaves its
    HNSW directory behind; the compaction now scrubs both while the gate is shut. Negative control: erase off."""
    svc, data_dir, tmp_path = real_compaction_palace
    for i in range(6):
        svc._write_row(f"zoe_k{i}", f"Ordinary household sentence {i} about gardens", {"user_id": OTHER, "status": "approved"})
    svc._write_row("zoe_canary", f"My neighbour {CANARY} keeps bees", {"user_id": USER, "status": "approved"})
    svc._delete_ids(["zoe_canary"])                                         # API delete only: residue on disk
    assert _hits(data_dir)["total"] > 0
    rep = memory_service.compact_drawers_index_sync(data_dir, backups_dir=str(tmp_path / "bk"))
    assert rep["status"] == "ok" and rep["rows"] == 6
    assert rep["residue"]["sqlite"]["vacuumed"] and rep["residue"]["orphans_removed"] >= 1
    assert memory_residue.orphan_segment_dirs(data_dir) == []
    assert _hits(data_dir)["total"] == 0
    assert len(svc._collection().get(where={"user_id": OTHER})["ids"]) == 6   # the rebuilt store serves
    assert memory_service._MAINTENANCE_OPEN.is_set()


def test_compaction_negative_control_erase_off_leaves_the_orphan_directory(real_compaction_palace, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    svc, data_dir, tmp_path = real_compaction_palace
    for i in range(3):
        svc._write_row(f"zoe_k{i}", f"Ordinary household sentence {i}", {"user_id": OTHER, "status": "approved"})
    svc._write_row("zoe_canary", f"My neighbour {CANARY} keeps bees", {"user_id": USER, "status": "approved"})
    svc._delete_ids(["zoe_canary"])
    rep = memory_service.compact_drawers_index_sync(data_dir, backups_dir=str(tmp_path / "bk"))
    assert rep["status"] == "ok" and rep["residue"] == {"enabled": False}
    # the pre-rebuild HNSW directory is still there (whether the text also is depends on page reuse: the live
    # palace kept 3 byte-hits after a compaction, a tiny test palace may overwrite them)
    assert len(memory_residue.orphan_segment_dirs(data_dir)) >= 1
