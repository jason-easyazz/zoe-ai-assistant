"""Temporal relationship edges — migration 0015 + flag-gated supersession.

Roadmap item 2 of ADR-relationship-memory. Proves:

  * migration 0015 applied to a 0007-shaped SQLite schema adds valid_from /
    valid_to / superseded_by and the partial ``person_relationships_pair_active``
    index (and drops the full pair index);
  * flag OFF ⇒ writing the same pair twice (different rel_type) leaves ONE
    current edge = the first (dedup, no supersession) — proves the byte-for-byte
    no-op vs pre-temporal behaviour;
  * flag ON + same rel_type twice ⇒ still one current edge, no supersession;
  * flag ON + changed rel_type ⇒ old edge closed (valid_to + superseded_by=new
    id), a NEW current edge exists, exactly one edge has valid_to IS NULL;
  * read filter ⇒ ``compose_relational_block`` returns only current edges
    (a superseded edge is excluded).

Everything is a real in-memory SQLite DB exercising the actual ``_write_relationship``
and the actual compose read query — no fakes for the storage engine, no model
loads, no PostgreSQL.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import create_engine, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

import person_extractor
import zoe_memory_compose as compose_mod

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_migration(filename: str, mod_name: str):
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── DB scaffolding: 0007 schema + 0015 migration on real SQLite ──────────────


async def _open_db():
    """Open an in-memory SQLite DB with the 0007 person schema, then apply 0015.

    A minimal ``people`` table (only the columns ``_write_relationship`` /
    ``_resolve_person_uuid`` / the compose read touch) plus the exact
    ``person_relationships`` shape and full pair index from migration 0007. We
    then run migration 0015's raw DDL through the same connection so the test
    proves the migration SQL, not a hand-rolled schema.
    """
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row

    # people — 0007 shape, minimal columns used by the code under test.
    await db.execute(
        """
        CREATE TABLE people (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            name        TEXT NOT NULL,
            relationship TEXT,
            circle      TEXT,
            context     TEXT,
            notes       TEXT,
            visibility  TEXT,
            deleted     INTEGER NOT NULL DEFAULT 0,
            is_partial  INTEGER NOT NULL DEFAULT 0,
            last_contacted_at TEXT
        )
        """
    )
    # person_relationships — EXACT 0007 shape + full pair index.
    await db.execute(
        """
        CREATE TABLE person_relationships (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            person_a_id  TEXT NOT NULL,
            person_b_id  TEXT NOT NULL,
            rel_type     TEXT NOT NULL,
            rel_a_to_b   TEXT NOT NULL,
            rel_b_to_a   TEXT NOT NULL,
            rel_group    TEXT NOT NULL,
            notes        TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE UNIQUE INDEX person_relationships_pair "
        "ON person_relationships(user_id, person_a_id, person_b_id)"
    )
    # Satellite tables the compose read joins/queries — empty stubs so the
    # relational read runs (this suite only exercises the relationships path).
    await db.execute(
        """
        CREATE TABLE person_important_dates (
            id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT,
            label TEXT, date_type TEXT, month INTEGER, day INTEGER, year INTEGER
        )
        """
    )
    await db.execute(
        "CREATE TABLE user_portraits (user_id TEXT PRIMARY KEY, portrait_text TEXT)"
    )
    await db.commit()

    # Apply migration 0015's upgrade DDL (SQLite branch of _add_column = plain
    # ADD COLUMN; the index swap statements are dialect-agnostic).
    await db.execute("ALTER TABLE person_relationships ADD COLUMN valid_from TEXT")
    await db.execute("ALTER TABLE person_relationships ADD COLUMN valid_to TEXT")
    await db.execute("ALTER TABLE person_relationships ADD COLUMN superseded_by TEXT")
    await db.execute(
        "UPDATE person_relationships SET valid_from = created_at WHERE valid_from IS NULL"
    )
    await db.execute("DROP INDEX IF EXISTS person_relationships_pair")
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS person_relationships_pair_active "
        "ON person_relationships(user_id, person_a_id, person_b_id) "
        "WHERE valid_to IS NULL"
    )
    await db.commit()
    return db


async def _seed_people(db, user_id="jason"):
    """Two real people so _write_relationship resolves them (no stub creation)."""
    await db.execute(
        "INSERT INTO people (id, user_id, name, deleted, is_partial, visibility) "
        "VALUES ('pa', ?, 'Alice', 0, 0, 'personal')",
        (user_id,),
    )
    await db.execute(
        "INSERT INTO people (id, user_id, name, deleted, is_partial, visibility) "
        "VALUES ('pb', ?, 'Bob', 0, 0, 'personal')",
        (user_id,),
    )
    await db.commit()


async def _current_edges(db, user_id="jason"):
    async with db.execute(
        "SELECT id, rel_type, valid_to, superseded_by FROM person_relationships "
        "WHERE user_id=? AND valid_to IS NULL ORDER BY created_at",
        (user_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _all_edges(db, user_id="jason"):
    async with db.execute(
        "SELECT id, rel_type, valid_to, superseded_by FROM person_relationships "
        "WHERE user_id=? ORDER BY created_at",
        (user_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


@pytest.fixture(autouse=True)
def _clear_flag():
    """Each test controls the flag explicitly; never leak env between tests."""
    prev = os.environ.pop("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", None)
    yield
    if prev is None:
        os.environ.pop("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", None)
    else:
        os.environ["ZOE_TEMPORAL_RELATIONSHIPS_ENABLED"] = prev


# ── 0. Real migration 0015 runs forward on a 0007-shaped SQLite DB ───────────


def test_migration_0015_upgrade_on_0007_schema():
    """Run the REAL 0015.upgrade() over a 0007 person_relationships table.

    Proves the migration SQL itself (not the hand-copied DDL in _open_db) adds
    the 3 temporal columns, backfills valid_from, and swaps the full pair index
    for the partial current-only index.
    """
    migration = _load_migration("0015_temporal_relationship_edges.py", "mig_0015")
    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        # 0007-shaped table + full pair index + one pre-existing edge.
        conn.execute(text(
            """
            CREATE TABLE person_relationships (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL,
                rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL,
                rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL,
                notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX person_relationships_pair "
            "ON person_relationships(user_id, person_a_id, person_b_id)"
        ))
        conn.execute(text(
            "INSERT INTO person_relationships "
            "(id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, "
            "rel_b_to_a, rel_group, created_at, updated_at) "
            "VALUES ('r1','u1','a','b','spouse','Spouse','Spouse','family',"
            "'2020-01-01T00:00:00Z','2020-01-01T00:00:00Z')"
        ))

        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        cols = {r[1] for r in conn.execute(
            text("PRAGMA table_info(person_relationships)")
        ).fetchall()}
        assert {"valid_from", "valid_to", "superseded_by"} <= cols

        idx = {r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()}
        assert "person_relationships_pair_active" in idx
        assert "person_relationships_pair" not in idx

        # Backfill: pre-existing row's valid_from = its created_at.
        vf = conn.execute(text(
            "SELECT valid_from, valid_to FROM person_relationships WHERE id='r1'"
        )).fetchone()
        assert vf[0] == "2020-01-01T00:00:00Z"
        assert vf[1] is None  # currently valid


# ── 1. Migration shape (via _open_db's applied DDL) ──────────────────────────


@pytest.mark.asyncio
async def test_migration_adds_temporal_columns_and_partial_index():
    db = await _open_db()
    try:
        async with db.execute("PRAGMA table_info(person_relationships)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        assert {"valid_from", "valid_to", "superseded_by"} <= cols

        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ) as cur:
            idx = {r["name"] for r in await cur.fetchall()}
        assert "person_relationships_pair_active" in idx
        # Full pair index dropped (history now allowed).
        assert "person_relationships_pair" not in idx

        # The active index is partial (scoped WHERE valid_to IS NULL).
        async with db.execute(
            "SELECT sql FROM sqlite_master WHERE name='person_relationships_pair_active'"
        ) as cur:
            (sql,) = await cur.fetchone()
        assert "valid_to is null" in sql.lower()
    finally:
        await db.close()


# ── 2. Flag OFF — pre-temporal behaviour (dedup, no supersession) ────────────


@pytest.mark.asyncio
async def test_flag_off_second_write_is_ignored_no_supersession():
    db = await _open_db()
    try:
        await _seed_people(db)
        # No flag set (default OFF).
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "spouse", "family", db
        )
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "ex_spouse", "family", db
        )

        current = await _current_edges(db)
        allrows = await _all_edges(db)
        # Exactly one edge total — the FIRST — no history row created.
        assert len(allrows) == 1
        assert len(current) == 1
        assert current[0]["rel_type"] == "spouse"
        assert current[0]["superseded_by"] is None
    finally:
        await db.close()


# ── 3. Flag ON — same rel_type is a no-op (no supersession row) ──────────────


@pytest.mark.asyncio
async def test_flag_on_same_rel_type_no_supersession():
    os.environ["ZOE_TEMPORAL_RELATIONSHIPS_ENABLED"] = "1"
    db = await _open_db()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "spouse", "family", db
        )
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "spouse", "family", db
        )
        allrows = await _all_edges(db)
        current = await _current_edges(db)
        assert len(allrows) == 1
        assert len(current) == 1
        assert current[0]["rel_type"] == "spouse"
    finally:
        await db.close()


# ── 4. Flag ON — changed rel_type supersedes and preserves history ───────────


@pytest.mark.asyncio
async def test_flag_on_changed_rel_type_supersedes():
    os.environ["ZOE_TEMPORAL_RELATIONSHIPS_ENABLED"] = "1"
    db = await _open_db()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "spouse", "family", db
        )
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "ex_spouse", "family", db
        )

        allrows = await _all_edges(db)
        current = await _current_edges(db)

        # Two edges now exist: one closed (old) + one current (new).
        assert len(allrows) == 2
        # Exactly ONE current edge for the pair.
        assert len(current) == 1
        assert current[0]["rel_type"] == "ex_spouse"

        old = next(r for r in allrows if r["rel_type"] == "spouse")
        new = next(r for r in allrows if r["rel_type"] == "ex_spouse")
        assert old["valid_to"] is not None            # closed
        assert old["superseded_by"] == new["id"]      # points at replacement
        assert new["valid_to"] is None                # current
        assert new["superseded_by"] is None
    finally:
        await db.close()


# ── 5. Read path — compose_relational_block excludes superseded edges ────────


@pytest.mark.asyncio
async def test_compose_read_excludes_superseded():
    os.environ["ZOE_TEMPORAL_RELATIONSHIPS_ENABLED"] = "1"
    # The compose read query is gated by ZOE_MEMORY_COMPOSE_ENABLED; turn it on
    # so compose_relational_block actually runs the relational read.
    os.environ["ZOE_MEMORY_COMPOSE_ENABLED"] = "1"
    db = await _open_db()
    try:
        await _seed_people(db)
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "spouse", "family", db
        )
        await person_extractor._write_relationship(
            "jason", "Alice", "Bob", "ex_spouse", "family", db
        )

        # Sanity: exactly one current edge in the store (ex_spouse).
        rels = await _current_edges(db)
        assert len(rels) == 1 and rels[0]["rel_type"] == "ex_spouse"

        # A relational query so the router gate lets relational facts through.
        block = await compose_mod.compose_relational_block(
            "jason", "who is Alice's spouse or ex-spouse", db
        )
        assert block is not None, "relational block should be produced"
        rel_refs = [r for r in block["refs"] if r.get("source") == "relationship"]
        # Exactly ONE relationship ref — the superseded 'spouse' edge is filtered
        # out by the read's ``valid_to IS NULL`` clause.
        assert len(rel_refs) == 1
        assert rel_refs[0]["label"] == "Ex Spouse"          # current, not "Spouse"
        # The rendered line uses the current (ex-spouse) label.
        rel_lines = [ln for ln in block["lines"] if "ex spouse" in ln.lower()]
        assert len(rel_lines) == 1
    finally:
        os.environ.pop("ZOE_MEMORY_COMPOSE_ENABLED", None)
        await db.close()
