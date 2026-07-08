"""Phase 1 of contacts-from-known-people (ADR-contacts-from-known-people.md).

The `person_create` suggestion action turns a known-but-not-a-contact person into
a full, editable `people` row (is_partial=0). Proves: flag-gated fail-closed,
pronoun/junk rejection, case-insensitive dedup, and that the created row is full
(not a bare stub). Slim: exercises `_execute_action` directly over an in-memory
SQLite DB via a tiny asyncpg-style ($N/fetchrow) shim.
"""
import re

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import pending_suggestions as ps

USER = "demo_suggest_user"  # a DEMO user — never a real person


class _Conn:
    """Minimal asyncpg-style shim over aiosqlite: $N→? rewrite + fetchrow/execute."""

    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql: str) -> str:
        return re.sub(r"\$(\d+)", "?", sql)

    async def execute(self, sql, *args):
        await self._db.execute(self._tr(sql), args)
        await self._db.commit()

    async def fetchrow(self, sql, *args):
        async with self._db.execute(self._tr(sql), args) as c:
            return await c.fetchone()


async def _open():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT,
            circle TEXT, context TEXT, visibility TEXT,
            is_partial INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0)"""
    )
    await db.commit()
    return db


async def _rows(db):
    async with db.execute(
        "SELECT name, relationship, circle, is_partial, visibility FROM people WHERE user_id=? AND deleted=0",
        (USER,),
    ) as c:
        return [dict(r) for r in await c.fetchall()]


@pytest.mark.asyncio
async def test_creates_full_editable_contact(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open()
    try:
        res = await ps._execute_action(_Conn(db), "person_create",
                                       {"name": "Janice", "relationship": "mother"}, USER)
        assert res["created"] is True and res["name"] == "Janice"
        rows = await _rows(db)
        assert len(rows) == 1
        assert rows[0]["relationship"] == "mother"
        assert rows[0]["is_partial"] == 0  # a FULL contact, not a bare stub — editable
        assert rows[0]["circle"] is None  # not the bogus "circle" literal
        assert rows[0]["visibility"] == "personal"  # private by default — not auto-shared
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_slots_override_circle_and_visibility(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open()
    try:
        await ps._execute_action(_Conn(db), "person_create",
                                 {"name": "Bob", "circle": "work", "visibility": "family"}, USER)
        row = (await _rows(db))[0]
        assert row["circle"] == "work" and row["visibility"] == "family"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dedups_case_insensitive(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open()
    try:
        await ps._execute_action(_Conn(db), "person_create", {"name": "Karen"}, USER)
        res = await ps._execute_action(_Conn(db), "person_create", {"name": "karen"}, USER)
        assert res["created"] is False
        assert len(await _rows(db)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejects_pronoun_name(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open()
    try:
        with pytest.raises(ValueError, match="invalid_person_name"):
            await ps._execute_action(_Conn(db), "person_create", {"name": "She"}, USER)
        assert await _rows(db) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fails_closed_when_flag_off(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_SUGGEST_ENABLED", raising=False)
    db = await _open()
    try:
        with pytest.raises(ValueError, match="unsupported_action:person_create"):
            await ps._execute_action(_Conn(db), "person_create", {"name": "Julie"}, USER)
        assert await _rows(db) == []
    finally:
        await db.close()
