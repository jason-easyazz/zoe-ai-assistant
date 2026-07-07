"""
db_compat.py — aiosqlite-style access to the Postgres pool.

`get_compat_db()` is the only public API: it yields the pool's connection wrapper
(`db_pool.AsyncpgCompat`, the maintained shim with `rowcount` support since #860),
so legacy `?`-placeholder / cursor-style callers work unchanged. New code should
use `db_pool.get_db()` and the asyncpg native API directly.

Historical note: this module once carried its own duplicate `AsyncpgCompat`/`_Cursor`
classes; they were dead code (no runtime constructor callers) and were removed
2026-07-07 per the retire-by-removing rule after misleading the 2026-07 audit
(packet P-F1 — resolved via #1143 against the live db_pool copy instead).
"""
from __future__ import annotations

from contextlib import asynccontextmanager


@asynccontextmanager
async def get_compat_db():
    """Async context manager yielding the pooled aiosqlite-compatible connection.

    Usage (drop-in for the old aiosqlite.connect pattern):
        async with get_compat_db() as db:
            cursor = await db.execute("SELECT ...", (param,))
            rows = await cursor.fetchall()
    """
    from db_pool import get_db_ctx
    async with get_db_ctx() as conn:
        yield conn
