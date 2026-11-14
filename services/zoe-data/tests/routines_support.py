"""Shared fixture for the routines tests: SQLite with the REAL migration 0082, ``db_compat.get_compat_db`` pointed at it.

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
import routines_store as rs
import user_erase_gate
from db_pool import _Cursor, _ExecResult

SVC = Path(__file__).resolve().parents[1]
MIGRATION = SVC / "alembic" / "versions" / "0082_routines.py"
ALEX, SAM, RILEY = "m-alex", "m-sam", "m-riley"
#: a fixed household morning (UTC household clock in these tests): Thursday 2026-11-12, 07:30
THU = datetime(2026, 11, 12, 7, 30, tzinfo=timezone.utc)


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
    spec = importlib.util.spec_from_file_location("mig_0082", MIGRATION)
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
    monkeypatch.setattr(rs, "_now_utc", lambda: THU)
    getattr(rs, "reset_walks", lambda: None)()
    user_erase_gate.reset()
    yield db
    getattr(rs, "reset_walks", lambda: None)()
    user_erase_gate.reset()
