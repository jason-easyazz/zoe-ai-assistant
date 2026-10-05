"""The people-graph half of the memory-authority wall.

``person_relationships`` is the one people-graph table where a writer can CLOSE an existing
fact (``person_extractor._write_relationship`` supersedes an edge when
``ZOE_TEMPORAL_RELATIONSHIPS_ENABLED`` is on). Edges are stamped with their writer
(migration 0037: ``authority`` / ``origin``); an ``inferred`` writer can never close an edge
the user stated - or an unstamped legacy edge, which is the user's until shown otherwise - and
leaves a pending candidate instead. Also pinned: a person fact the memory wall refused is not
written to the structured people tables either.

Real in-memory SQLite (the 0007/0015 shapes + the REAL 0037 migration), a fake Chroma
collection for the candidate row; no network, no model (``ci_safe``). Synthetic people only.
"""
from __future__ import annotations

import asyncio
import logging
import os

import aiosqlite
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

import memory_authority as ma
import memory_service
import person_extractor
from memory_service import MemoryService
from test_memory_authority import _Col  # the where-honouring Chroma stand-in
from test_temporal_relationships import (
    _all_edges, _current_edges, _load_migration, _open_db, _seed_people,
)

pytestmark = pytest.mark.ci_safe

USER = "jason"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", "1")
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-authority-people")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


async def _db_with_0037():
    db = await _open_db()
    await db.execute("ALTER TABLE person_relationships ADD COLUMN authority TEXT")
    await db.execute("ALTER TABLE person_relationships ADD COLUMN origin TEXT")
    await db.commit()
    return db


def test_migration_0037_adds_the_two_nullable_columns_and_is_additive():
    migration = _load_migration("0037_relationship_edge_authority.py", "mig_0037")
    assert (migration.revision, migration.down_revision) == ("0037", "0036")
    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        conn.execute(text(
            "CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
            "rel_type TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO person_relationships VALUES ('r1','u1','spouse')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        cols = {r[1]: r for r in conn.execute(text("PRAGMA table_info(person_relationships)")).fetchall()}
        assert {"authority", "origin"} <= set(cols) and not cols["authority"][3]  # nullable
        assert conn.execute(text("SELECT authority, origin FROM person_relationships")).fetchone() == (None, None)


@pytest.mark.asyncio
async def test_new_edges_are_stamped_with_their_writer():
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db,
                                                   authority="user_stated", origin="conversation")
        async with db.execute("SELECT authority, origin FROM person_relationships") as cur:
            assert tuple(await cur.fetchone()) == ("user_stated", "conversation")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_stale_schema_without_the_columns_still_writes():
    """Migration-safe: before 0037 runs the stamp is a silent no-op."""
    db = await _open_db()  # no authority/origin columns
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db)
        assert [e["rel_type"] for e in await _current_edges(db)] == ["friend"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_user_stated_change_closes_the_edge_and_keeps_history():
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db,
                                                   authority="user_stated")
        await person_extractor._write_relationship(USER, "Alice", "Bob", "spouse", "family", db,
                                                   authority="user_stated")
        assert [e["rel_type"] for e in await _current_edges(db)] == ["spouse"]
        assert len(await _all_edges(db)) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_inferred_writer_cannot_close_a_user_stated_edge(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db,
                                                   authority="user_stated")
        await person_extractor._write_relationship(USER, "Alice", "Bob", "spouse", "family", db,
                                                   authority="inferred", origin="digest")
        assert [e["rel_type"] for e in await _current_edges(db)] == ["friend"]
        assert len(await _all_edges(db)) == 1
        assert "AUTHORITY_BLOCKED writer=digest kind=relationship" in caplog.text
        cands = await svc.list_by_status(user_id=USER, status="disputed")
        assert len(cands) == 1 and cands[0].metadata["contradicts_id"].startswith("edge:")
        assert cands[0].metadata["authority"] == ma.INFERRED
        # break-the-fix control: the kill switch lets the same inferred write close the edge
        os.environ["ZOE_MEMORY_AUTHORITY"] = "0"
        try:
            await person_extractor._write_relationship(USER, "Alice", "Bob", "spouse", "family", db,
                                                       authority="inferred", origin="digest")
        finally:
            del os.environ["ZOE_MEMORY_AUTHORITY"]
        assert [e["rel_type"] for e in await _current_edges(db)] == ["spouse"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_unstamped_legacy_edge_is_the_users_until_shown_otherwise():
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db)
        await db.execute("UPDATE person_relationships SET authority=NULL, origin=NULL")
        await db.commit()
        await person_extractor._write_relationship(USER, "Alice", "Bob", "spouse", "family", db,
                                                   authority="inferred", origin="digest")
        assert [e["rel_type"] for e in await _current_edges(db)] == ["friend"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_inference_may_correct_its_own_edge():
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(USER, "Alice", "Bob", "friend", "personal", db,
                                                   authority="inferred", origin="digest")
        await person_extractor._write_relationship(USER, "Alice", "Bob", "spouse", "family", db,
                                                   authority="inferred", origin="digest")
        assert [e["rel_type"] for e in await _current_edges(db)] == ["spouse"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_regex_extraction_of_the_users_own_words_is_user_stated(svc):
    """process_text reads the user's turn: its edges are the user's and may change an edge."""
    db = await _db_with_0037()
    try:
        await _seed_people(db)
        await person_extractor.process_text("Alice is Bob's sister", user_id=USER, db=db)
        async with db.execute("SELECT authority, origin FROM person_relationships") as cur:
            rows = [tuple(r) for r in await cur.fetchall()]
        assert rows and all(a == "user_stated" and o == "conversation" for a, o in rows)
    finally:
        await db.close()


def _apply_work_fact(svc):
    async def run():
        db = await _db_with_0037()
        await db.execute("CREATE TABLE person_activities (id TEXT, person_id TEXT, user_id TEXT, "
                         "activity_type TEXT, description TEXT, source TEXT, venue TEXT, "
                         "session_id TEXT, mem_id TEXT)")
        await _seed_people(db)
        try:
            # the user said Alice's work; a model-sourced restatement with another employer
            await svc.ingest("Alice works at Acme.", user_id=USER, source="voice_fact",
                             entity_type="person", entity_id="pa", status="approved")
            ok = await person_extractor.apply_person_fact(
                "Alice", "work", "Alice works at Initech.", user_id=USER, source="synthesis", db=db)
            async with db.execute("SELECT count(*) FROM person_activities") as cur:
                (n,) = await cur.fetchone()
            return ok, n
        finally:
            await db.close()

    return asyncio.run(run())


def test_a_person_fact_the_memory_wall_held_back_is_not_written_to_the_people_tables(svc):
    """apply_person_fact: the memory write left a pending candidate -> no dates / activities."""
    ok, n = _apply_work_fact(svc)
    assert ok is False and n == 0
    pending = asyncio.run(svc.list_by_status(user_id=USER, status="disputed"))
    assert [r.text for r in pending] == ["Alice works at Initech."]


def test_break_the_fix_control_without_the_wall_the_people_tables_are_written(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    ok, n = _apply_work_fact(svc)
    assert ok is True and n == 1
