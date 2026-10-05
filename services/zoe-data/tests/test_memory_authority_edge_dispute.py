"""An approved relationship dispute must change the GRAPH, not just leave an approved memory.

``person_extractor._edge_may_change`` parks an inferred relationship that would close a protected
edge as a ``disputed`` candidate (``contradicts_id="edge:<id>"``). Review of #1868: approving that
candidate through ``MemoryService.review`` only looked for a Chroma id, never found the edge, and
left the structured relationship unchanged while the candidate became an approved memory that
contradicted it. Now the candidate carries the edge change (edge id, person ids, old / new
relationship) and approval applies it (``person_extractor.apply_edge_dispute``); rejection leaves
the edge alone; an approval that cannot be applied approves nothing.

Real in-memory SQLite (the 0007/0015 shapes + the 0037 columns), fake Chroma; synthetic people
(``ci_safe``).
"""
from __future__ import annotations

import contextlib

import pytest

import db_pool
import person_extractor
from test_memory_authority_people import USER, _db_with_0037, _env, svc  # noqa: F401 - fixtures
from test_temporal_relationships import _all_edges, _current_edges, _seed_people

pytestmark = pytest.mark.ci_safe


async def _disputed_edge(svc, db):
    """Alice/Bob stated as friends by the user; a digest infers 'spouse' -> one edge candidate."""
    await _seed_people(db)
    await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db,
                                               authority="user_stated", origin="conversation")
    await person_extractor._write_relationship(USER, "Alice", "Bob", "spouse", "family", db,
                                               authority="inferred", origin="digest")
    (cand,) = await svc.list_by_status(user_id=USER, status="disputed")
    return cand


def _use_db(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)


async def _rels(db):
    return [(e["rel_type"], e["valid_to"] is None) for e in await _all_edges(db)]


@pytest.mark.asyncio
async def test_the_candidate_carries_the_whole_edge_change(svc):
    db = await _db_with_0037()
    try:
        cand = await _disputed_edge(svc, db)
        (edge,) = await _current_edges(db)
        md = cand.metadata
        assert md["contradicts_id"] == "edge:" + edge["id"] and md["edge_id"] == edge["id"]
        assert (md["edge_person_a_id"], md["edge_person_b_id"]) == ("pa", "pb")
        assert (md["edge_old_rel"], md["edge_new_rel"], md["edge_rel_group"]) == ("friend", "spouse", "family")
        assert md["edge_old_text"] == "Alice is Bob's friend."
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_approving_the_dispute_changes_the_edge_and_the_memory_consistently(svc, monkeypatch):
    db = await _db_with_0037()
    try:
        cand = await _disputed_edge(svc, db)
        (old,) = await _current_edges(db)
        _use_db(monkeypatch, db)
        done = await svc.review(cand.id, decision="approve", actor=USER)
        assert done is not None and done.metadata["status"] == "approved"
        assert done.metadata["authority"] == "user_confirmed"
        # the structured relationship really changed, history kept
        (cur,) = await _current_edges(db)
        assert cur["rel_type"] == "spouse" and cur["id"] != old["id"]
        assert await _rels(db) == [("friend", False), ("spouse", True)]
        async with db.execute("SELECT superseded_by, valid_to FROM person_relationships WHERE id=?",
                              (old["id"],)) as c:
            sup, valid_to = tuple(await c.fetchone())
        assert sup == cur["id"] and valid_to
        async with db.execute("SELECT authority, origin FROM person_relationships WHERE id=?",
                              (cur["id"],)) as c:
            assert tuple(await c.fetchone()) == ("user_confirmed", "review_ui")
        # the approved memory points at both edges
        assert done.metadata["edge_applied_id"] == cur["id"]
        assert done.metadata["supersedes_edge_id"] == old["id"]
        # approving again is a no-op (idempotent): no third edge
        await svc.review(cand.id, decision="approve", actor=USER)
        assert len(await _all_edges(db)) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejecting_the_dispute_leaves_the_edge_intact(svc, monkeypatch):
    db = await _db_with_0037()
    try:
        cand = await _disputed_edge(svc, db)
        before = await _all_edges(db)
        _use_db(monkeypatch, db)
        done = await svc.review(cand.id, decision="reject", actor=USER)
        assert done is not None and done.metadata["status"] == "rejected"
        assert await _all_edges(db) == before
        assert [e["rel_type"] for e in await _current_edges(db)] == ["friend"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_approval_that_cannot_reach_the_graph_approves_nothing(svc, monkeypatch):
    """Break the apply path (no edge data on the candidate): the candidate stays disputed and the
    edge stays - never an approved memory that contradicts the graph."""
    db = await _db_with_0037()
    try:
        cand = await _disputed_edge(svc, db)
        _use_db(monkeypatch, db)
        _doc, md = svc._col.rows[cand.id]
        md.pop("edge_new_rel")
        assert await svc.review(cand.id, decision="approve", actor=USER) is None
        assert (await svc.get(cand.id)).metadata["status"] == "disputed"
        assert [e["rel_type"] for e in await _current_edges(db)] == ["friend"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failed_insert_reopens_the_old_edge(monkeypatch):
    """apply_edge_dispute never leaves the pair without a current edge."""
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db)
        (old,) = await _current_edges(db)
        real = db.execute

        async def failing(sql, *a, **k):
            if "INSERT INTO person_relationships" in sql:
                raise RuntimeError("disk full")
            return await real(sql, *a, **k)

        monkeypatch.setattr(db, "execute", failing)
        assert await person_extractor.apply_edge_dispute(db, USER, old["id"], new_rel_type="spouse") is None
        monkeypatch.undo()
        assert [(e["id"], e["rel_type"]) for e in await _current_edges(db)] == [(old["id"], "friend")]
    finally:
        await db.close()
