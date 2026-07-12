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


# ── Pool-leak regression (live outage 2026-07-12) ────────────────────────────
# apply_person_fact and process_text acquired an OWNED pooled connection via
# _ensure_db(None) and never released it — one leak per chat turn, pool max 10,
# no acquire timeout → after ~8 memory-bearing turns EVERY /api/chat request
# blocked forever while /health stayed green. These pin the release.

async def _exercise_leak(fn_call):
    import db_pool
    fp = _FakePool()
    orig_pool, orig_rel = db_pool.get_pool, db_pool._release_safely
    db_pool.get_pool = lambda: fp

    async def _rel(pool, conn):
        await pool.release(conn)

    db_pool._release_safely = _rel
    try:
        await fn_call()
        assert fp.acquired >= 1, "expected the call to acquire an owned connection"
        assert fp.released == fp.acquired, (
            f"POOL LEAK: acquired {fp.acquired} but released {fp.released} — "
            "this is the 2026-07-12 chat-wedge bug"
        )
    finally:
        db_pool.get_pool, db_pool._release_safely = orig_pool, orig_rel


def test_apply_person_fact_releases_owned_connection(monkeypatch):
    async def _fake_ingest(*a, **k):
        return "mem-1"
    async def _fake_resolve(name, user_id, db):
        return None  # unresolved → early return path (the common leak path)
    monkeypatch.setattr(person_extractor, "_ingest_to_mempalace", _fake_ingest)
    monkeypatch.setattr(person_extractor, "_resolve_person_uuid", _fake_resolve)

    async def _call():
        ok = await person_extractor.apply_person_fact(
            "Caitlin", "preference", "likes tea", user_id="demo-leak", source="test"
        )
        assert ok is True
    asyncio.run(_exercise_leak(_call))


def test_process_text_releases_owned_connection():
    async def _call():
        # No pattern matches → written == 0, but the owned conn must still release.
        n = await person_extractor.process_text(
            "the weather is nice today and nothing personal is said",
            user_id="demo-leak", source="test",
        )
        assert n == 0
    asyncio.run(_exercise_leak(_call))
