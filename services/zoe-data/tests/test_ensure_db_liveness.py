"""Regression: person_extractor._ensure_db(None) must return a LIVE connection.

The bug (found by exploratory testing 2026-07-09): _ensure_db did
``async for _db in get_db(): return _db`` — abandoning db_pool.get_db()'s
generator, whose ``finally`` then released the pooled connection back to the
pool. Callers (the idle re-linker `_resolve_pending_person_links`, and the
write-path person resolution at memory_extractor.py) got a DEAD connection
("connection has been released back to the pool") and silently no-op'd —
`AsyncpgCompat.close()` is a pool-managed no-op so nothing flagged it.

The fake-DB unit tests never caught it because it is asyncpg-pool-release
specific. This pins the fix: _ensure_db(None) hands back an owned connection
that stays live until the caller's ``await _db.close()`` releases it exactly once.
Also pins that the graph-recall boost resolves with the ambiguity-safe resolver.
"""
import asyncio
import inspect
import re

import pytest

pytestmark = pytest.mark.ci_safe

import person_extractor


class _FakeConn:
    def __init__(self):
        self.released = False

    async def fetchval(self, sql, *a):
        if self.released:
            raise RuntimeError("cannot call Connection.fetchval(): connection has been released back to the pool")
        return 1

    async def execute(self, sql, *a):
        if self.released:
            raise RuntimeError("released")
        return "OK"

    def is_in_transaction(self):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()
        self.acquired = 0
        self.released = 0

    async def acquire(self):
        self.acquired += 1
        return self.conn

    async def release(self, conn):
        self.released += 1
        conn.released = True


async def _exercise():
    import db_pool  # real module in the services/zoe-data test env
    fp = _FakePool()
    orig_pool, orig_rel = db_pool.get_pool, db_pool._release_safely
    db_pool.get_pool = lambda: fp

    async def _rel(pool, conn):
        await pool.release(conn)

    db_pool._release_safely = _rel  # _ensure_db imports db_pool lazily + reads these at call time
    try:
        db, opened = await person_extractor._ensure_db(None)
        assert opened is True
        # THE BUG: the connection must NOT be released before the caller closes it.
        assert fp.conn.released is False, "connection released prematurely — the _ensure_db bug is back"
        # ...and it must be usable (a query would raise on a released conn).
        assert await db.fetchval("SELECT 1") == 1
        # close() must perform a REAL release (AsyncpgCompat.close is a no-op).
        await db.close()
        assert fp.conn.released is True and fp.released == 1, "close() did not release the pooled connection exactly once"
        # a provided db is never owned/closed by us
        _db2, opened2 = await person_extractor._ensure_db(object())
        assert opened2 is False
    finally:
        db_pool.get_pool, db_pool._release_safely = orig_pool, orig_rel


def test_ensure_db_none_returns_live_owned_connection():
    asyncio.run(_exercise())


def test_graph_recall_boost_uses_ambiguity_safe_resolver():
    """The boost's query→person resolution must use the ambiguity-safe
    `_resolve_unique_person_uuid` (skip on ambiguity), not the loose
    `_resolve_person_uuid` (substring first-row → wrong person on a fragment)."""
    from memory_service import MemoryService
    src = inspect.getsource(MemoryService._graph_depth_by_pid)
    assert "_resolve_unique_person_uuid" in src, "boost must use the ambiguity-safe resolver"
    # the loose resolver must not be what actually resolves the start person here
    assert not re.search(r"await\s+_resolve_person_uuid\(", src), (
        "boost still calls the loose _resolve_person_uuid — ambiguous fragments boost the wrong person"
    )
