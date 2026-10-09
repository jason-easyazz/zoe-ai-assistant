"""Shared fakes for the durable-forgetting tests (``test_forget_durable_ledger.py``,
``test_memory_tombstones_ledger.py``). Not a test module.

Everything is in-process and synthetic: the where-honouring Chroma stand-in from the memory-authority pack
(``_Col``), a real in-memory SQLite database whose ``memory_forgotten`` table is created by the REAL 0038
migration (offline SQL generation), the derived-store tables the cascade clears, and ``db_pool.get_db_ctx``
pointed at it. Names are the benchmark lab's invented people (``scripts/perf/zmb/world.py``): never a household's.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import types
from pathlib import Path

import aiosqlite
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_pool
import memory_forgotten
import memory_service
import memory_tombstones
from memory_service import MemoryService
from test_memory_authority import _Col  # the where-honouring Chroma stand-in
from test_temporal_relationships import _open_db  # people + person_relationships (0007 + 0015)

SALT = "unit-test-forget-ledger-secret-0123456789"
USER = "demo_bar_00000001"
OTHER = "demo_bar_00000002"

# The ZMB F3 inputs (scripts/perf/zmb/scenarios/forgetting.json, world seed slots): the forgotten friend, their
# home, the re-teach home, and the day's transcript line with the proposed fact.
FRIEND = "Dana"
HOME = "Hobart"
LIMA = "Lisbon"
SEED_FACT = f"User's friend {FRIEND} lives in {HOME}."
RETEACH_FACT = f"User's friend {FRIEND} lives in {LIMA}."
TRANSCRIPT = ("okay so that was the plan and then " + FRIEND +
              " rang about the weekend and we will see about the shopping later on tonight")
PROPOSED = f"User's friend {FRIEND} is visiting at the weekend."

_MIGRATIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def load_migration():
    spec = importlib.util.spec_from_file_location("mig_0038", _MIGRATIONS / "0038_memory_forgotten.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def migration_sql(direction: str = "upgrade") -> str:
    """The SQL the real migration emits for SQLite (offline mode), so tests run the migration's own DDL."""
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="sqlite", opts={"as_sql": True, "output_buffer": buf})
    with Operations.context(ctx):
        getattr(load_migration(), direction)()
    return buf.getvalue()


_DERIVED_DDL = """
CREATE TABLE IF NOT EXISTS user_portraits (user_id TEXT PRIMARY KEY, portrait_text TEXT);
CREATE TABLE user_model_cards (user_id TEXT PRIMARY KEY, card_json TEXT NOT NULL, card_text TEXT NOT NULL);
CREATE TABLE open_loops (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, loop_text TEXT NOT NULL,
    context TEXT, follow_up_hint TEXT, resolved BOOLEAN DEFAULT FALSE, resolved_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS chat_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New Chat');
CREATE TABLE IF NOT EXISTS chat_messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    metadata TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE proactive_candidates (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'loop',
    text TEXT NOT NULL, hint TEXT NOT NULL DEFAULT '', cue_words TEXT NOT NULL DEFAULT '');
"""


async def open_forgotten_db():
    """In-memory SQLite: the people graph (0007 + 0015) + the derived stores + the REAL 0038 table."""
    db = await _open_db()
    await db.execute("ALTER TABLE people ADD COLUMN updated_at TEXT")  # the real table has it (0001)
    await db.executescript(_DERIVED_DDL)
    await db.executescript(migration_sql("upgrade"))
    await db.commit()
    return db


def use_db(monkeypatch, db) -> None:
    """Point ``db_pool.get_db_ctx`` (what the ledger and the cascade open) at ``db``."""
    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)


async def table_dump(db, table: str) -> list[tuple]:
    async with db.execute(f"SELECT * FROM {table}") as cur:
        return [tuple(r) for r in await cur.fetchall()]


class TombstoneClock:
    """Steps ``memory_tombstones``' monotonic clock (the lab's fake clock): ``advance(360)`` = six minutes."""

    def __init__(self, monkeypatch):
        self._real = memory_tombstones.time
        self.offset = 0.0
        monkeypatch.setattr(memory_tombstones, "time", types.SimpleNamespace(
            monotonic=lambda: self._real.monotonic() + self.offset))

    def advance(self, seconds: float) -> None:
        self.offset += float(seconds)


@pytest.fixture(autouse=True)
def ledger_env(monkeypatch):
    """The ledger configured (salt set), an in-process backend, clean state before and after."""
    monkeypatch.setenv(memory_forgotten.SALT_ENV, SALT)
    monkeypatch.delenv(memory_forgotten.SHIELD_DAYS_ENV, raising=False)
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    backend = memory_forgotten.MemoryBackend()
    memory_forgotten.set_backend(backend)
    memory_tombstones.clear_all()
    import memory_forget_cascade
    memory_forget_cascade.reset_state()
    yield backend
    memory_forgotten.set_backend(None)
    memory_tombstones.clear_all()
    memory_forget_cascade.reset_state()


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-forget-durable")
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


def rows_naming(svc, name: str, *, user: str = USER, statuses=("approved", "pending", "disputed")) -> list[str]:
    """Texts of the user's rows that name ``name`` in one of ``statuses`` (the F3 probe)."""
    low = name.lower()
    return [d for d, m in svc._col.rows.values()
            if (m.get("user_id") or m.get("wing")) == user and m.get("status") in statuses and low in d.lower()]


@pytest.fixture
def no_offers(monkeypatch):
    """``pending_suggestions`` needs a DB the forget handler's offer withdrawal does not need here."""
    async def none(_uid, _name):
        return 0

    monkeypatch.setitem(sys.modules, "pending_suggestions",
                        types.SimpleNamespace(resolve_person_offers_by_name=none))
