"""Regression: people_create must create a contact for an acting identity that
has no `users` row yet (the flue/intent-dispatch path, unlike the chat path,
never runs _ensure_user_and_chat_session).

Before the fix, `_execute_people_create_direct` INSERTed into `people` without
ensuring the user existed, so `people_user_id_fkey` rejected it; the handler
swallowed the error, returned None, and fell back to the mcporter path (which
persists nothing) — the live "I tried to add her but nothing happened" bug.
The fix upserts the `users` row first, mirroring the chat path.
"""
import contextlib

import pytest

pytestmark = pytest.mark.ci_safe

import aiosqlite

import database
import intent_router
from intent_router import Intent


class _Shim:
    """Minimal AsyncpgCompat-shaped wrapper: db.execute(sql, params_tuple)."""
    def __init__(self, conn):
        self._c = conn

    async def execute(self, sql, params=()):
        return await self._c.execute(sql, params)

    async def commit(self):
        await self._c.commit()


async def _open():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")  # enforce the FK, like prod
    await conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    await conn.execute(
        "CREATE TABLE people (id TEXT PRIMARY KEY,"
        " user_id TEXT NOT NULL REFERENCES users(id),"  # people_user_id_fkey
        " name TEXT, relationship TEXT, birthday TEXT, phone TEXT, email TEXT,"
        " notes TEXT, visibility TEXT, circle TEXT, context TEXT,"
        " deleted INTEGER DEFAULT 0, is_partial INTEGER DEFAULT 0)"
    )
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_people_create_ensures_user_then_persists(monkeypatch):
    conn = await _open()
    try:
        @contextlib.asynccontextmanager
        async def fake_ctx():
            yield _Shim(conn)

        monkeypatch.setattr(database, "get_db_ctx", fake_ctx)

        async def _noop(*a, **k):
            return None
        monkeypatch.setattr(intent_router, "_notify_ui_channel", _noop)

        # 'newbie' is an authed identity with NO users row (the jason case).
        res = await intent_router._execute_people_create_direct(
            Intent("people_create", {"name": "Priya Sharma", "relationship": "colleague"}),
            "newbie",
        )
        # Direct handler must SUCCEED (a truthy string) — not return None and
        # fall back to the mcporter path that persists nothing.
        assert res and "Priya Sharma" in res

        async with conn.execute(
            "SELECT name, relationship FROM people WHERE user_id='newbie' AND deleted=0"
        ) as c:
            rows = [dict(r) for r in await c.fetchall()]
        assert len(rows) == 1, "contact must persist under the acting user"
        assert rows[0]["name"] == "Priya Sharma" and rows[0]["relationship"] == "colleague"

        async with conn.execute("SELECT role FROM users WHERE id='newbie'") as c:
            urow = await c.fetchone()
        assert urow is not None, "acting user row auto-ensured (satisfies the FK)"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_people_create_works_when_user_already_exists(monkeypatch):
    conn = await _open()
    try:
        await conn.execute("INSERT INTO users (id, name, role) VALUES ('existing','existing','member')")
        await conn.commit()

        @contextlib.asynccontextmanager
        async def fake_ctx():
            yield _Shim(conn)
        monkeypatch.setattr(database, "get_db_ctx", fake_ctx)

        async def _noop(*a, **k):
            return None
        monkeypatch.setattr(intent_router, "_notify_ui_channel", _noop)

        res = await intent_router._execute_people_create_direct(
            Intent("people_create", {"name": "Bob"}), "existing",
        )
        assert res and "Bob" in res
        async with conn.execute("SELECT COUNT(*) FROM people WHERE user_id='existing'") as c:
            assert (await c.fetchone())[0] == 1
        # no duplicate user row from the ON CONFLICT DO NOTHING upsert
        async with conn.execute("SELECT COUNT(*) FROM users WHERE id='existing'") as c:
            assert (await c.fetchone())[0] == 1
    finally:
        await conn.close()
