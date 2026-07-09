"""Regression: store_suggestions must persist for an acting identity that has no
`users` row yet. pending_suggestions.user_id FKs to users(id); the voice/tool
paths (propose-on-mention, backfill) don't run the chat path's user-ensure, so
without this the INSERT FK-failed, was swallowed (store returns 0), and the
contact offer never appeared — the live propose-on-mention failure.
"""
import contextlib
import re

import pytest

pytestmark = pytest.mark.ci_safe

import aiosqlite

import pending_suggestions as ps


class _Shim:
    """AsyncpgCompat-shaped: $N params, execute + fetch (varargs)."""
    def __init__(self, conn):
        self._c = conn

    @staticmethod
    def _q(sql):
        return re.sub(r"\$\d+", "?", sql)

    async def execute(self, sql, *params):
        return await self._c.execute(self._q(sql), params)

    async def fetch(self, sql, *params):
        async with self._c.execute(self._q(sql), params) as cur:
            return list(await cur.fetchall())


async def _open():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")  # enforce the FK, like prod
    await db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    await db.execute(
        "CREATE TABLE pending_suggestions ("
        " id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),"
        " session_id TEXT, action_type TEXT, description TEXT, list_type TEXT,"
        " when_hint TEXT, amount_hint TEXT, offer_phrase TEXT, pre_filled_slots TEXT,"
        " created_at TEXT, turns_elapsed INTEGER, expire_after_turns INTEGER, resolved INTEGER)"
    )
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_store_ensures_user_then_persists(monkeypatch):
    conn = await _open()
    try:
        @contextlib.asynccontextmanager
        async def fake_ctx():
            yield _Shim(conn)
        monkeypatch.setattr(ps, "get_db_ctx", fake_ctx)

        prop = [{
            "action_type": "person_create", "description": "x", "offer_phrase": "add?",
            "pre_filled_slots": {"name": "Yolanda", "relationship": "aunt"},
        }]
        # 'newbie' has NO users row → before the fix this FK-failed → 0
        n = await ps.store_suggestions("newbie", "s", prop)
        assert n == 1, "proposal must persist even when the user had no users row"

        async with conn.execute("SELECT role FROM users WHERE id='newbie'") as c:
            assert await c.fetchone() is not None, "acting user row auto-ensured"

        pend = await ps.list_pending_contacts("newbie")
        assert len(pend) == 1 and pend[0]["name"] == "Yolanda"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_store_noop_for_guest(monkeypatch):
    conn = await _open()
    try:
        @contextlib.asynccontextmanager
        async def fake_ctx():
            yield _Shim(conn)
        monkeypatch.setattr(ps, "get_db_ctx", fake_ctx)
        assert await ps.store_suggestions("guest", "s", [{"action_type": "person_create"}]) == 0
    finally:
        await conn.close()
