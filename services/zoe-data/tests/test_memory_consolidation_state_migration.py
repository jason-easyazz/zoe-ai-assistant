"""Schema-integrity tests for the memory_consolidation_state table.

Covers two related fixes:
  - 0014 brings ``memory_consolidation_state`` under Alembic (it used to be
    created by runtime ``CREATE TABLE IF NOT EXISTS`` only — schema drift, and it
    broke least-privilege Postgres roles with no CREATE). The migration runs
    forward on a sample SQLite DB, is rerun-safe, and downgrade drops the table.
  - 0007's ``people.context`` ADD COLUMN is now ``IF NOT EXISTS`` so a
    partial-failure re-run doesn't die on duplicate-column (Postgres production
    dialect, asserted via offline SQL render).

Production is PostgreSQL; the migrations are dialect-aware (matching 0013) so the
upgrade path can be exercised on SQLite here.
"""
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))

VERSIONS = SVC / "alembic" / "versions"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    # Offline render only needs a dialect, not a reachable server.
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg2://u:p@localhost/db")
    return cfg


def _load_migration(filename: str, mod_name: str):
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _columns(conn, table: str) -> dict:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    # name -> (type, notnull, pk)
    return {r[1]: (r[2], r[3], r[5]) for r in rows}


# ─── 0014 is wired correctly into the chain ───────────────────────────────────

def test_0014_is_single_head_after_0013():
    script = ScriptDirectory.from_config(_alembic_config())
    assert list(script.get_heads()) == ["0014"], "expected exactly one head: 0014"
    rev = script.get_revision("0014")
    assert rev.down_revision == "0013"


# ─── 0014 creates the table, is rerun-safe, and downgrades cleanly ────────────

def test_0014_creates_table_and_is_rerun_safe_on_sample_db():
    migration = _load_migration("0014_memory_consolidation_state.py", "mig_0014")
    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        # Run the real migration forward against an empty sample DB.
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        cols = _columns(conn, "memory_consolidation_state")
        assert set(cols) == {
            "session_id", "user_id", "last_consolidated_at", "turns_consolidated",
        }
        # session_id is the PRIMARY KEY; user_id is NOT NULL.
        assert cols["session_id"][2] == 1, "session_id must be the PRIMARY KEY"
        assert cols["user_id"][1] == 1, "user_id must be NOT NULL"
        assert cols["last_consolidated_at"][1] == 1
        assert cols["turns_consolidated"][1] == 1

        # Inserting a row using only the required columns exercises the defaults.
        conn.execute(text(
            "INSERT INTO memory_consolidation_state (session_id, user_id) "
            "VALUES ('s1', 'u1')"
        ))
        row = conn.execute(text(
            "SELECT turns_consolidated, last_consolidated_at "
            "FROM memory_consolidation_state WHERE session_id='s1'"
        )).fetchone()
        assert row[0] == 0, "turns_consolidated DEFAULT 0"
        assert row[1] is not None, "last_consolidated_at has a default timestamp"

        # Re-running the migration is a harmless no-op (IF NOT EXISTS) — proves
        # it coexists with a table the runtime DDL may already have created and
        # survives a partial-failure replay.
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(text(
            "SELECT count(*) FROM memory_consolidation_state"
        )).scalar() == 1, "rerun must not drop or duplicate existing data"

        # Downgrade removes the table.
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        names = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()]
        assert "memory_consolidation_state" not in names


def test_0014_postgres_branch_mirrors_runtime_ddl():
    """The Postgres branch must match the runtime _ensure_state_table DDL."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from alembic import command
        command.upgrade(_alembic_config(), "0013:0014", sql=True)
    sql = buf.getvalue().lower()
    assert "create table if not exists memory_consolidation_state" in sql
    # Exact Postgres types/defaults the runtime path uses.
    assert "session_id text primary key" in sql
    assert "user_id text not null" in sql
    assert "last_consolidated_at timestamptz not null default now()" in sql
    assert "turns_consolidated int not null default 0" in sql
    # The runtime DDL creates no secondary index (the PK is the only key), so the
    # migration must not either — keep them byte-for-byte in sync.
    assert "create index" not in sql
    assert "create unique index" not in sql


# ─── 0007 context ADD COLUMN is rerun-safe (Postgres) ─────────────────────────

def _render_0006_to_0007(dialect_url: str) -> str:
    cfg = Config()
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", dialect_url)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from alembic import command
        command.upgrade(cfg, "0006:0007", sql=True)
    return buf.getvalue().lower()


def test_0007_context_add_column_is_idempotent_on_postgres():
    sql = _render_0006_to_0007("postgresql+psycopg2://u:p@localhost/db")
    # Postgres (production): previously-unguarded ADD COLUMN now uses IF NOT
    # EXISTS, so a partial-failure re-run no longer dies on duplicate-column.
    assert "add column if not exists context text not null default 'personal'" in sql
    # Net schema is unchanged — the column is still TEXT NOT NULL DEFAULT.
    assert "alter table people add column context" not in sql


def test_0007_context_add_column_sqlite_uses_plain_add_column():
    # SQLite has no ADD COLUMN IF NOT EXISTS; the dialect branch must emit plain
    # ADD COLUMN so the migration parses under SQLite, with the same net column.
    sql = _render_0006_to_0007("sqlite://")
    assert "add column context text not null default 'personal'" in sql
    assert "if not exists context" not in sql


# ─── Runtime _ensure_state_table gates CREATE for least-privilege roles ────────

import asyncio  # noqa: E402

import memory_idle_consolidation as mic  # noqa: E402


class _InsufficientPrivilege(Exception):
    """Stand-in for asyncpg.InsufficientPrivilegeError (SQLSTATE 42501)."""
    sqlstate = "42501"

    def __init__(self, msg="permission denied for schema public"):
        super().__init__(msg)


class _FakeConn:
    """Minimal asyncpg-shaped conn: fetchval() for existence, execute() for DDL.

    ``raise_on_create`` simulates a role without CREATE privilege; ``race_create``
    simulates a concurrent creator that makes the table appear despite the error.
    """

    def __init__(self, *, exists, raise_on_create=False, race_create=False):
        self._exists = exists
        self.raise_on_create = raise_on_create
        self.race_create = race_create
        self.create_attempted = False

    async def fetchval(self, sql, *args):
        s = sql.lower()
        if "to_regclass" in s or "sqlite_master" in s:
            return "memory_consolidation_state" if self._exists else None
        return None

    async def execute(self, sql, *args):
        if "create table" in sql.lower():
            self.create_attempted = True
            if self.raise_on_create:
                if self.race_create:
                    self._exists = True  # concurrent creator won the race
                raise _InsufficientPrivilege()
            self._exists = True
        return "CREATE TABLE"


def _run_ensure(conn):
    mic._state_table_ready = False  # reset the once-per-process guard
    try:
        asyncio.run(mic._ensure_state_table(conn))
    finally:
        mic._state_table_ready = False


def test_runtime_skips_create_when_table_exists_without_create_privilege():
    """The blocker: once 0014 has created the table, a role with no CREATE
    privilege must NOT issue a CREATE (Postgres checks the privilege before the
    IF NOT EXISTS short-circuit). The existence check must skip CREATE entirely."""
    conn = _FakeConn(exists=True, raise_on_create=True)
    _run_ensure(conn)  # must not raise
    assert conn.create_attempted is False, "CREATE must be skipped when table exists"


def test_runtime_creates_table_on_unmigrated_dev_db():
    """Dev/test convenience preserved: a DB without the migration still gets the
    table created (role has CREATE privilege)."""
    conn = _FakeConn(exists=False, raise_on_create=False)
    _run_ensure(conn)
    assert conn.create_attempted is True


def test_runtime_tolerates_privilege_error_when_table_appears_concurrently():
    """Defense-in-depth: existence check said absent, CREATE hit a privilege
    error, but a concurrent creator made the table appear → continue, no raise."""
    conn = _FakeConn(exists=False, raise_on_create=True, race_create=True)
    _run_ensure(conn)  # must not raise
    assert conn.create_attempted is True


def test_runtime_reraises_when_table_truly_absent_and_no_privilege():
    """If the table is genuinely missing and the role can't CREATE, consolidation
    can't work — surface the error rather than swallow it."""
    conn = _FakeConn(exists=False, raise_on_create=True, race_create=False)
    with pytest.raises(_InsufficientPrivilege):
        _run_ensure(conn)
