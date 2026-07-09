"""Phase 2b.1 backfill-delivery (ADR-contacts-from-known-people.md).

`list_pending_contacts` is a user-scoped, SESSION-AGNOSTIC list of un-resolved
`person_create` proposals. Backfill stores proposals under a static `'backfill'`
session, but the live `list_active`/`load_for_prompt` paths filter by session_id,
so those proposals never surface in a per-conversation chat. Proves the parallel
review path returns proposals regardless of session, excludes resolved rows,
excludes other action_types, and is user-scoped. Exercises the function directly
over an in-memory SQLite DB via a tiny asyncpg-style ($N/fetch) shim.
"""
import re
import uuid

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import pending_suggestions as ps

USER = "demo_pending_user"  # a DEMO user — never a real person
OTHER = "demo_other_user"


class _Db:
    """Minimal asyncpg-style shim over aiosqlite: $N→? rewrite + fetch/execute."""

    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql: str) -> str:
        return re.sub(r"\$(\d+)", "?", sql)

    async def fetch(self, sql, *args):
        async with self._db.execute(self._tr(sql), args) as c:
            return await c.fetchall()

    async def execute(self, sql, *args):
        await self._db.execute(self._tr(sql), args)
        await self._db.commit()


class _Ctx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return _Db(self._db)

    async def __aexit__(self, *_a):
        return False


async def _open():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE pending_suggestions (
            id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, action_type TEXT,
            description TEXT, list_type TEXT, when_hint TEXT, amount_hint TEXT,
            offer_phrase TEXT, pre_filled_slots TEXT, created_at TEXT,
            turns_elapsed INTEGER DEFAULT 0, expire_after_turns INTEGER DEFAULT 2,
            resolved INTEGER DEFAULT 0)"""
    )
    await db.commit()
    return db


async def _insert(db, *, user_id, session_id, action_type, slots, resolved=0,
                  offer="Save this?", created_at="2026-07-09T00:00:00"):
    import json
    await db.execute(
        """INSERT INTO pending_suggestions
           (id, user_id, session_id, action_type, offer_phrase, pre_filled_slots,
            created_at, resolved)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, session_id, action_type, offer,
         json.dumps(slots), created_at, resolved),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_returns_person_create_regardless_of_session(monkeypatch):
    db = await _open()
    monkeypatch.setattr(ps, "get_db_ctx", lambda: _Ctx(db))
    try:
        await _insert(db, user_id=USER, session_id="backfill",
                      action_type="person_create", slots={"name": "Janice", "relationship": "mother"},
                      offer="Add Janice?", created_at="2026-07-09T00:00:00")
        await _insert(db, user_id=USER, session_id="live-conv-abc",
                      action_type="person_create", slots={"name": "Bob"},
                      offer="Add Bob?", created_at="2026-07-09T00:00:01")
        out = await ps.list_pending_contacts(USER)
        names = {r["name"] for r in out}
        assert names == {"Janice", "Bob"}  # both sessions surfaced
        janice = next(r for r in out if r["name"] == "Janice")
        assert janice["relationship"] == "mother"
        assert janice["offer_phrase"] == "Add Janice?"
        assert janice["id"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_excludes_resolved(monkeypatch):
    db = await _open()
    monkeypatch.setattr(ps, "get_db_ctx", lambda: _Ctx(db))
    try:
        await _insert(db, user_id=USER, session_id="backfill",
                      action_type="person_create", slots={"name": "Karen"}, resolved=1)
        out = await ps.list_pending_contacts(USER)
        assert out == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_excludes_other_action_types(monkeypatch):
    db = await _open()
    monkeypatch.setattr(ps, "get_db_ctx", lambda: _Ctx(db))
    try:
        await _insert(db, user_id=USER, session_id="backfill",
                      action_type="list_add", slots={"item": "milk"})
        await _insert(db, user_id=USER, session_id="backfill",
                      action_type="person_create", slots={"name": "Julie"})
        out = await ps.list_pending_contacts(USER)
        assert [r["name"] for r in out] == ["Julie"]  # list_add excluded
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_user_scoped(monkeypatch):
    db = await _open()
    monkeypatch.setattr(ps, "get_db_ctx", lambda: _Ctx(db))
    try:
        await _insert(db, user_id=OTHER, session_id="backfill",
                      action_type="person_create", slots={"name": "Someone Else"})
        await _insert(db, user_id=USER, session_id="backfill",
                      action_type="person_create", slots={"name": "Mine"})
        out = await ps.list_pending_contacts(USER)
        assert [r["name"] for r in out] == ["Mine"]  # other user's row excluded
    finally:
        await db.close()
