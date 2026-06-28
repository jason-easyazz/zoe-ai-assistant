"""Regression tests for the get_db / get_db_ctx connection-release teardown race.

When a request is cancelled mid-query, asyncpg's pool.release() fails its reset
with InterfaceError('another operation is in progress'). asyncpg terminates the
connection and frees the holder internally, so the pool is fine — but the
re-raised exception used to bubble out of the dependency teardown as log spam.
These tests pin that the release race is swallowed and the connection is still
acquired/released exactly once.
"""
import asyncio

import asyncpg
import pytest

import db_pool


def _run(coro):
    return asyncio.run(coro)


class _FakePool:
    """Minimal asyncpg.Pool stand-in: records acquire/release and can fail release."""

    def __init__(self, fail_release: bool):
        self._fail_release = fail_release
        self.acquired = 0
        self.released = 0
        self.conn = object()

    async def acquire(self):
        self.acquired += 1
        return self.conn

    async def release(self, conn):
        self.released += 1
        assert conn is self.conn
        if self._fail_release:
            raise asyncpg.InterfaceError("cannot perform operation: another operation is in progress")


def test_get_db_swallows_release_race(monkeypatch):
    pool = _FakePool(fail_release=True)
    monkeypatch.setattr(db_pool, "get_pool", lambda: pool)

    async def go():
        agen = db_pool.get_db()
        db = await agen.__anext__()
        assert isinstance(db, db_pool.AsyncpgCompat)
        # Closing the generator triggers the finally → release race; must NOT raise.
        await agen.aclose()

    _run(go())  # would raise if the race bubbled out
    assert pool.acquired == 1 and pool.released == 1


def test_get_db_normal_release(monkeypatch):
    pool = _FakePool(fail_release=False)
    monkeypatch.setattr(db_pool, "get_pool", lambda: pool)

    async def go():
        async for db in db_pool.get_db():
            assert isinstance(db, db_pool.AsyncpgCompat)

    _run(go())
    assert pool.acquired == 1 and pool.released == 1


def test_get_db_ctx_swallows_release_race(monkeypatch):
    pool = _FakePool(fail_release=True)
    monkeypatch.setattr(db_pool, "get_pool", lambda: pool)

    async def go():
        async with db_pool.get_db_ctx() as db:
            assert isinstance(db, db_pool.AsyncpgCompat)

    _run(go())  # release race inside __aexit__ must be swallowed
    assert pool.acquired == 1 and pool.released == 1


def test_get_db_ctx_propagates_body_error_but_still_releases(monkeypatch):
    pool = _FakePool(fail_release=False)
    monkeypatch.setattr(db_pool, "get_pool", lambda: pool)

    async def go():
        async with db_pool.get_db_ctx():
            raise ValueError("boom")

    with pytest.raises(ValueError):
        _run(go())
    assert pool.released == 1  # released even when the body raised


def test_init_pool_is_idempotent_for_live_pool(monkeypatch):
    class _Pool:
        def __init__(self):
            self.closed = 0

        def is_closing(self):
            return False

        async def close(self):
            self.closed += 1

    created = []

    async def create_pool(*args, **kwargs):
        pool = _Pool()
        created.append((args, kwargs, pool))
        return pool

    monkeypatch.setenv("POSTGRES_URL", "postgresql://zoe:pw@localhost:5432/zoe")
    monkeypatch.setattr(db_pool, "_pool", None)
    monkeypatch.setattr(db_pool, "_pool_loop", None)
    monkeypatch.setattr(db_pool.asyncpg, "create_pool", create_pool)

    async def go():
        first = await db_pool.init_pool()
        second = await db_pool.init_pool()
        return first, second

    first, second = _run(go())
    assert first is second
    assert db_pool.get_pool() is first
    assert len(created) == 1
    assert created[0][2].closed == 0


def test_init_pool_recreates_pool_for_new_event_loop(monkeypatch):
    class _Pool:
        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.closed = 0
            self.terminated = 0

        def is_closing(self):
            return False

        async def close(self):
            self.closed += 1

        def terminate(self):
            self.terminated += 1

    created = []

    async def create_pool(*args, **kwargs):
        pool = _Pool()
        created.append((args, kwargs, pool))
        return pool

    monkeypatch.setenv("POSTGRES_URL", "postgresql://zoe:pw@localhost:5432/zoe")
    monkeypatch.setattr(db_pool, "_pool", None)
    monkeypatch.setattr(db_pool, "_pool_loop", None)
    monkeypatch.setattr(db_pool.asyncpg, "create_pool", create_pool)

    async def init_once():
        return await db_pool.init_pool()

    first = _run(init_once())
    second = _run(init_once())

    assert first is not second
    assert first.loop is not second.loop
    assert len(created) == 2
    assert first.closed == 0
    assert first.terminated == 1
    assert second.closed == 0
    assert second.terminated == 0
    assert db_pool.get_pool() is second
    assert db_pool._pool_loop is second.loop


def test_adapt_params_ignores_question_marks_inside_quoted_literals():
    sql, args = db_pool._adapt_params(
        "SELECT * FROM notes WHERE note = 'huh?' AND label = \"why?\" AND owner = ? AND body LIKE ?",
        ("jason", "%real?%"),
    )

    assert sql == (
        "SELECT * FROM notes WHERE note = 'huh?' AND label = \"why?\" "
        "AND owner = $1 AND body LIKE $2"
    )
    assert args == ["jason", "%real?%"]


def test_adapt_params_keeps_now_inside_literals_but_casts_real_now():
    sql, args = db_pool._adapt_params(
        "INSERT INTO events(note, created_at) VALUES ('NOW()? stays text', NOW())",
        (),
    )

    assert sql == "INSERT INTO events(note, created_at) VALUES ('NOW()? stays text', NOW()::text)"
    assert args == []


def test_adapt_params_handles_doubled_quote_literals():
    sql, args = db_pool._adapt_params(
        "SELECT * FROM notes WHERE note = 'Jason''s huh?' AND id = ?",
        (42,),
    )

    assert sql == "SELECT * FROM notes WHERE note = 'Jason''s huh?' AND id = $1"
    assert args == [42]
