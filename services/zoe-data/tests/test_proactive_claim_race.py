"""Regression tests for proactive claim_pending atomicity (P1 race).

The old claim_pending did SELECT(claimed=0) → INSERT session/message →
UPDATE(claimed=1) with no atomicity (db.commit() is a no-op on the asyncpg
compat shim without an explicit transaction). Two concurrent taps could both
pass the SELECT and each create a chat session → duplicate sessions, and a
partial insert failure orphaned a session/message.

These pin the fixed behaviour:
- a single atomic `UPDATE ... WHERE claimed=0 RETURNING *` picks exactly one
  winner, so concurrent claims yield exactly one session;
- the session+message inserts run in one transaction and, on failure, the claim
  is released (compensated) and nothing is left behind;
- an expired pending row is never claimed and its claim is released.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

import proactive.session_utils as su

pytestmark = pytest.mark.ci_safe


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    """Mirror db_pool._ExecResult: awaitable AND async context manager."""

    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class _Tx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeDB:
    """Minimal asyncpg-compat fake over a shared in-memory store."""

    def __init__(self, store, *, fail_session_insert=False):
        self.store = store
        self.fail_session_insert = fail_session_insert

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(sql, params))

    async def _do(self, sql, params):
        # Yield once so concurrent claims interleave BEFORE the conditional
        # claim runs — exposing any non-atomic SELECT-then-update logic.
        await asyncio.sleep(0)
        u = " ".join(sql.split()).upper()
        if u.startswith("UPDATE PROACTIVE_PENDING SET CLAIMED = 1"):
            # Atomic check-and-set: NO await between read and write.
            row = self.store["pending"].get(params[0])
            if row is not None and row["claimed"] == 0:
                row["claimed"] = 1
                return _Cursor([dict(row)])
            return _Cursor([])
        if u.startswith("UPDATE PROACTIVE_PENDING SET CLAIMED = 0"):
            row = self.store["pending"].get(params[0])
            if row is not None:
                row["claimed"] = 0
            return _Cursor([])
        if u.startswith("DELETE FROM PROACTIVE_PENDING"):
            self.store["pending"].pop(params[0], None)
            return _Cursor([])
        if u.startswith("INSERT INTO CHAT_SESSIONS"):
            if self.fail_session_insert:
                raise RuntimeError("simulated session insert failure")
            self.store["sessions"].append(params)
            return _Cursor([])
        if u.startswith("INSERT INTO CHAT_MESSAGES"):
            self.store["messages"].append(params)
            return _Cursor([])
        return _Cursor([])

    async def commit(self):
        pass

    def transaction(self):
        return _Tx(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _patch_db(monkeypatch, store, **kw):
    @contextlib.asynccontextmanager
    async def fake_compat_db():
        # Fresh wrapper per call (separate "connection"), shared store.
        yield FakeDB(store, **kw)

    monkeypatch.setattr(su, "_get_compat_db", fake_compat_db)


@pytest.mark.asyncio
async def test_concurrent_claim_yields_single_session(monkeypatch):
    store = {
        "pending": {"p1": {"id": "p1", "user_id": "u1", "message": "hi", "claimed": 0, "expires_at": _future()}},
        "sessions": [],
        "messages": [],
    }
    _patch_db(monkeypatch, store)

    results = await asyncio.gather(su.claim_pending("p1"), su.claim_pending("p1"))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, "exactly one concurrent tap should win the claim"
    assert len(store["sessions"]) == 1, "no duplicate chat sessions"
    assert len(store["messages"]) == 1
    assert store["pending"]["p1"]["claimed"] == 1


@pytest.mark.asyncio
async def test_claim_releases_on_insert_failure(monkeypatch):
    store = {
        "pending": {"p1": {"id": "p1", "user_id": "u1", "message": "hi", "claimed": 0, "expires_at": _future()}},
        "sessions": [],
        "messages": [],
    }
    _patch_db(monkeypatch, store, fail_session_insert=True)

    result = await su.claim_pending("p1")

    assert result is None
    assert store["sessions"] == [] and store["messages"] == []
    # Compensated: claim released so the notification can be retried.
    assert store["pending"]["p1"]["claimed"] == 0


@pytest.mark.asyncio
async def test_expired_pending_is_not_claimed(monkeypatch):
    store = {
        "pending": {"p1": {"id": "p1", "user_id": "u1", "message": "hi", "claimed": 0, "expires_at": _past()}},
        "sessions": [],
        "messages": [],
    }
    _patch_db(monkeypatch, store)

    result = await su.claim_pending("p1")

    assert result is None
    assert store["sessions"] == []
    # Expired rows are DELETED (not released), so they can't get stuck at
    # claimed=1 outside the cleanup loop's `claimed = 0` reaping filter.
    assert "p1" not in store["pending"]


@pytest.mark.asyncio
async def test_already_claimed_returns_none(monkeypatch):
    store = {
        "pending": {"p1": {"id": "p1", "user_id": "u1", "message": "hi", "claimed": 1, "expires_at": _future()}},
        "sessions": [],
        "messages": [],
    }
    _patch_db(monkeypatch, store)

    assert await su.claim_pending("p1") is None
    assert store["sessions"] == []
