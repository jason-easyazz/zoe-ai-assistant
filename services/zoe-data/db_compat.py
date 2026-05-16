"""
db_compat.py — asyncpg compatibility shim providing an aiosqlite-style cursor API.

Used exclusively during the PostgreSQL migration to allow large files (mcp_server.py,
etc.) to work with asyncpg using minimal changes. New code should use db_pool.get_db()
and asyncpg native API directly.

The shim converts:
  - ? placeholders → $1, $2, ... via _adapt_params
  - cursor = await db.execute(sql, params)  (returns _Cursor wrapping rows)
  - rows = await cursor.fetchall()          (returns list of asyncpg Records)
  - row  = await cursor.fetchone()          (returns first Record or None)
  - await db.commit()                       (no-op; asyncpg auto-commits)
  - await db.close()                        (no-op; connection managed by pool)
"""
from __future__ import annotations
from typing import Any


class _Cursor:
    """Buffered cursor wrapping a list of asyncpg Records."""
    __slots__ = ("_rows",)

    def __init__(self, rows: list):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    @property
    def lastrowid(self):
        # Only relevant for RETURNING id queries; those should use fetchrow() directly
        if self._rows and len(self._rows) > 0:
            r = self._rows[0]
            try:
                return r["id"]
            except (KeyError, IndexError):
                return None
        return None


class AsyncpgCompat:
    """Thin wrapper around asyncpg Connection providing aiosqlite-compatible execute API.

    This is a migration shim — do not use for new code.
    Supports both: cursor = await db.execute(...) and async with db.execute(...) as cursor:
    """
    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        pass  # no-op: asyncpg Records already behave like dicts

    def execute(self, sql: str, *params):
        """Return dual-mode result: awaitable or async context manager."""
        from db_pool import _adapt_params, _ExecResult
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = params[0]
        return _ExecResult(self._do_execute(sql, params))

    async def _do_execute(self, sql: str, params) -> _Cursor:
        from db_pool import _adapt_params
        sql_pg, args = _adapt_params(sql, params)
        stripped = sql_pg.strip().upper().lstrip("(")
        if (
            stripped.startswith("SELECT")
            or stripped.startswith("WITH ")
            or stripped.startswith("EXPLAIN")
            or "RETURNING" in stripped
        ):
            rows = await self._conn.fetch(sql_pg, *args)
            return _Cursor(list(rows))
        else:
            await self._conn.execute(sql_pg, *args)
            return _Cursor([])

    async def commit(self) -> None:
        pass  # asyncpg auto-commits outside of explicit transactions

    async def close(self) -> None:
        pass  # connection is managed by the pool acquire() context manager

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    # Forward any other attribute access to the underlying asyncpg connection
    def __getattr__(self, name: str):
        return getattr(self._conn, name)


from contextlib import asynccontextmanager


@asynccontextmanager
async def get_compat_db():
    """Async context manager yielding AsyncpgCompat wrapping a pooled connection.

    Usage (drop-in for old aiosqlite.connect pattern):
        async with get_compat_db() as db:
            cursor = await db.execute("SELECT ...", (param,))
            rows = await cursor.fetchall()
    """
    from db_pool import get_db_ctx
    async with get_db_ctx() as conn:
        yield conn
