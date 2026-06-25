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
