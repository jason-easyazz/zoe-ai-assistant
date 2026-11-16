"""Shared fixture for the routines tests: SQLite with the REAL migration 0081, ``db_compat.get_compat_db`` pointed at it.

Synthetic members only (the repo is public): ``m-alex`` / ``m-sam`` are adults, ``m-riley`` a child; nobody real.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
import memory_forgotten
import memory_tombstones
import routines_store as rs
import user_erase_gate
from db_pool import _Cursor, _ExecResult

SVC = Path(__file__).resolve().parents[1]
MIGRATION = SVC / "alembic" / "versions" / "0081_routines.py"
ALEX, SAM, RILEY = "m-alex", "m-sam", "m-riley"
#: a fixed household morning (UTC household clock in these tests): Thursday 2026-11-12, 07:30
THU = datetime(2026, 11, 12, 7, 30, tzinfo=timezone.utc)
#: a synthetic ledger secret (tests only): the forget ledger is CONFIGURED in every routines test, over an in-memory backend
LEDGER_SALT = "routines-unit-test-ledger-secret-0123456789"


class Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def q(self, sql, params=()):
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]


def migration():
    spec = importlib.util.spec_from_file_location("mig_0081", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def migrate(path: str, direction: str = "upgrade") -> None:
    mod = migration()
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(mod, direction)()
    engine.dispose()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    migrate(path)
    db = Sqlite(path)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    monkeypatch.setenv(memory_forgotten.SALT_ENV, LEDGER_SALT)
    monkeypatch.setattr(rs, "_now_utc", lambda: THU)
    memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
    memory_tombstones.clear_all()
    getattr(rs, "reset_walks", lambda: None)()
    user_erase_gate.reset()
    yield db
    getattr(rs, "reset_walks", lambda: None)()
    user_erase_gate.reset()
    memory_tombstones.clear_all()
    memory_forgotten.set_backend(None)


def seed_day(db: "Sqlite", routine_id: str, subject: str, texts, *, day: str = "2026-11-12", actor=None) -> str:
    """A day copy with lines, written straight into BOTH day tables (this slice has no day writer; the forget tests must still prove
    both tables are erased)."""
    did = f"{routine_id}:{day}"
    db.conn.execute("INSERT INTO routine_days (id, routine_id, user_id, day, snapshot_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (did, routine_id, subject, day, "2026-11-12T00:00:00+00:00", "2026-11-12T00:00:00+00:00"))
    for i, text in enumerate(texts, 1):
        db.conn.execute("INSERT INTO routine_day_steps (id, day_id, step_id, origin, text, kind, position, state, actor_user_id, prompts, "
                        "created_at, updated_at) VALUES (?, ?, NULL, 'one_off', ?, 'check', ?, 'done', ?, 0, ?, ?)",
                        (f"{did}:{i}", did, text, i, actor, "2026-11-12T00:00:00+00:00", "2026-11-12T00:00:00+00:00"))
    db.conn.commit()
    return did
