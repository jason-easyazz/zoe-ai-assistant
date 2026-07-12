"""Pool-exhaustion hardening (QA review 2026-07 F1 follow-ups).

The 2026-07-12 outage: a connection leak drained the 10-slot asyncpg pool and
every /api/chat request hung forever while /health stayed 200. The leak is
fixed (#1258); these pin the three structural guards added after it:

1. acquire is BOUNDED — a drained pool raises PoolExhaustedError with a clear
   "db pool exhausted" message instead of wedging the request forever.
2. zoe_db_pool_* Prometheus gauges move on acquire/release.
3. /health probes the pool and returns 503 with a pool-exhausted detail when
   acquisition genuinely times out (and ONLY then — fail-open otherwise, since
   watchdogs restart the service on 503).
"""
import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

import db_pool


class _FakeConn:
    def __init__(self):
        self.released = False

    async def fetchval(self, sql, *a):
        if self.released:
            raise RuntimeError("connection has been released back to the pool")
        return 1

    def is_in_transaction(self):
        return False


class _FakePool:
    """FakePool with gauge-introspection hooks (get_size/get_idle_size)."""

    def __init__(self, size=10, idle=3, hang=False):
        self.conn = _FakeConn()
        self.acquired = 0
        self.released_count = 0
        self._size = size
        self._idle = idle
        self._hang = hang

    async def acquire(self):
        if self._hang:
            await asyncio.Event().wait()  # never resolves — simulated exhaustion
        self.acquired += 1
        self._idle -= 1
        return self.conn

    async def release(self, conn):
        self.released_count += 1
        self._idle += 1
        conn.released = True

    def get_size(self):
        return self._size

    def get_idle_size(self):
        return self._idle


# ── 1. bounded acquire ────────────────────────────────────────────────────────

def test_acquire_timeout_raises_clear_error(monkeypatch):
    """A drained pool must fail FAST with a diagnosable error, not hang."""
    monkeypatch.setenv("ZOE_DB_ACQUIRE_TIMEOUT_S", "0.05")

    async def _run():
        fp = _FakePool(hang=True)
        with pytest.raises(db_pool.PoolExhaustedError) as ei:
            await db_pool._acquire(fp)
        msg = str(ei.value)
        assert "db pool exhausted" in msg, "error must name the condition for log grep"
        assert "connection leak" in msg

    asyncio.run(_run())


def test_acquire_timeout_env_tunable(monkeypatch):
    monkeypatch.setenv("ZOE_DB_ACQUIRE_TIMEOUT_S", "3.5")
    assert db_pool._acquire_timeout_s() == 3.5
    monkeypatch.setenv("ZOE_DB_ACQUIRE_TIMEOUT_S", "not-a-number")
    assert db_pool._acquire_timeout_s() == db_pool._DEFAULT_ACQUIRE_TIMEOUT_S
    monkeypatch.setenv("ZOE_DB_ACQUIRE_TIMEOUT_S", "-1")
    assert db_pool._acquire_timeout_s() == db_pool._DEFAULT_ACQUIRE_TIMEOUT_S
    monkeypatch.delenv("ZOE_DB_ACQUIRE_TIMEOUT_S")
    assert db_pool._acquire_timeout_s() == db_pool._DEFAULT_ACQUIRE_TIMEOUT_S


def test_get_db_ctx_surfaces_pool_exhausted(monkeypatch):
    """The public acquire path (get_db_ctx) must propagate the bounded-acquire error."""
    monkeypatch.setenv("ZOE_DB_ACQUIRE_TIMEOUT_S", "0.05")
    fp = _FakePool(hang=True)
    monkeypatch.setattr(db_pool, "get_pool", lambda: fp)

    async def _run():
        with pytest.raises(db_pool.PoolExhaustedError):
            async with db_pool.get_db_ctx():
                pass

    asyncio.run(_run())


def test_get_db_ctx_normal_path_unchanged(monkeypatch):
    """Behavior on a healthy pool is unchanged: acquire, use, release exactly once."""
    fp = _FakePool()
    monkeypatch.setattr(db_pool, "get_pool", lambda: fp)

    async def _run():
        async with db_pool.get_db_ctx() as db:
            assert await db.fetchval("SELECT 1") == 1
        assert fp.acquired == 1 and fp.released_count == 1

    asyncio.run(_run())


# ── 2. pool gauges ────────────────────────────────────────────────────────────

def test_pool_gauges_move_on_acquire_and_release(monkeypatch):
    import memory_metrics as mm

    fp = _FakePool(size=10, idle=3)
    monkeypatch.setattr(db_pool, "get_pool", lambda: fp)

    async def _run():
        async with db_pool.get_db_ctx():
            # after acquire: idle dropped to 2 → in_use 8
            assert mm.db_pool_size._value.get() == 10
            assert mm.db_pool_free._value.get() == 2
            assert mm.db_pool_in_use._value.get() == 8
        # after release: idle back to 3
        assert mm.db_pool_free._value.get() == 3
        assert mm.db_pool_in_use._value.get() == 7

    asyncio.run(_run())


def test_pool_gauges_fail_open(monkeypatch):
    """A pool without introspection methods must not break acquire/release."""

    class _Bare:
        def __init__(self):
            self.conn = _FakeConn()

        async def acquire(self):
            return self.conn

        async def release(self, conn):
            conn.released = True

    bp = _Bare()
    monkeypatch.setattr(db_pool, "get_pool", lambda: bp)

    async def _run():
        async with db_pool.get_db_ctx() as db:
            assert await db.fetchval("SELECT 1") == 1

    asyncio.run(_run())  # must not raise


# ── 3. honest /health ─────────────────────────────────────────────────────────

def test_check_pool_health_reports_exhaustion(monkeypatch):
    fp = _FakePool(hang=True)
    monkeypatch.setattr(db_pool, "_pool", fp)

    async def _run():
        healthy, detail = await db_pool.check_pool_health(timeout_s=0.05)
        assert healthy is False
        assert "db pool exhausted" in detail

    asyncio.run(_run())
    monkeypatch.setattr(db_pool, "_pool", None)


def test_check_pool_health_ok_and_releases(monkeypatch):
    fp = _FakePool()
    monkeypatch.setattr(db_pool, "_pool", fp)

    async def _run():
        healthy, detail = await db_pool.check_pool_health(timeout_s=1.0)
        assert healthy is True and detail == "ok"
        assert fp.acquired == 1 and fp.released_count == 1, "health must not hold a connection"

    asyncio.run(_run())
    monkeypatch.setattr(db_pool, "_pool", None)


def test_check_pool_health_fails_open_when_uninitialised_or_query_error(monkeypatch):
    async def _run():
        # No pool yet (startup) → healthy, never 503 a booting service.
        monkeypatch.setattr(db_pool, "_pool", None)
        healthy, detail = await db_pool.check_pool_health(timeout_s=0.05)
        assert healthy is True

        # Acquire works but the query errors → NOT exhaustion → fail open.
        fp = _FakePool()

        async def _boom(sql, *a):
            raise RuntimeError("transient")

        fp.conn.fetchval = _boom
        monkeypatch.setattr(db_pool, "_pool", fp)
        healthy, detail = await db_pool.check_pool_health(timeout_s=0.5)
        assert healthy is True
        assert fp.released_count == 1

    asyncio.run(_run())
    monkeypatch.setattr(db_pool, "_pool", None)


def test_health_endpoint_returns_503_on_exhaustion(monkeypatch):
    """/health must go 503 with a pool-exhausted detail when the pool check fails."""
    main = pytest.importorskip("main")
    from fastapi.testclient import TestClient

    async def _exhausted(timeout_s=2.0):
        return False, "db pool exhausted — possible connection leak (acquire timed out after 2.0s)"

    monkeypatch.setattr(db_pool, "check_pool_health", _exhausted)
    monkeypatch.setitem(main._pool_health_cache, "checked_at", 0.0)

    client = TestClient(main.app)  # no `with` → lifespan not started
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert "db pool exhausted" in body["db_pool"]["detail"]

    # ...and back to 200 when the pool is healthy again (cache invalidated).
    async def _ok(timeout_s=2.0):
        return True, "ok"

    monkeypatch.setattr(db_pool, "check_pool_health", _ok)
    monkeypatch.setitem(main._pool_health_cache, "checked_at", 0.0)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["db_pool"]["healthy"] is True
