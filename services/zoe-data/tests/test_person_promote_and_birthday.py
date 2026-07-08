"""Phase 3 of contacts-from-known-people (ADR-contacts-from-known-people.md).

Two flag-gated, dark-by-default behaviours:

- **Part A — promote-on-confirm** (`pending_suggestions._execute_action`): accepting
  a `person_create` for a name that already exists as a bare ``is_partial=1`` stub
  promotes the stub in place (``is_partial=0``, fills a missing relationship) instead
  of minting a duplicate. A full (``is_partial=0``) contact is left untouched.
- **Part B — birthday capture** (`person_extractor.process_text`): with
  ``ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED`` ON, a birthday mentioned for a not-yet-contact
  creates a stub so the date can land. With the flag OFF it is a byte-for-byte no-op —
  no person row, no date row (today's behaviour).

Slim: exercises the functions directly over in-memory SQLite via a tiny asyncpg-style
($N/fetchrow) shim; MemPalace ingest is stubbed so no model/DB deps are needed.
"""
import re

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import pending_suggestions as ps
import person_extractor as pe

USER = "demo_phase3_user"  # a DEMO user — never a real person


# ── Part A — promote-on-confirm ──────────────────────────────────────────────


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


async def _open_people():
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


async def _insert_person(db, *, name, is_partial, relationship=None):
    pid = f"pid-{name.lower()}"
    await db.execute(
        "INSERT INTO people (id, user_id, name, relationship, is_partial, deleted)"
        " VALUES (?,?,?,?,?,0)",
        (pid, USER, name, relationship, is_partial),
    )
    await db.commit()
    return pid


async def _person(db, name):
    async with db.execute(
        "SELECT is_partial, relationship FROM people WHERE user_id=? AND lower(name)=lower(?)",
        (USER, name),
    ) as c:
        return dict(await c.fetchone())


@pytest.mark.asyncio
async def test_promotes_partial_stub_and_fills_relationship(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open_people()
    try:
        await _insert_person(db, name="Niel", is_partial=1)  # bare stub, no relationship
        res = await ps._execute_action(_Conn(db), "person_create",
                                       {"name": "Niel", "relationship": "father"}, USER)
        assert res["created"] is False and res["promoted"] is True
        assert res["relationship"] == "father"
        row = await _person(db, "Niel")
        assert row["is_partial"] == 0          # promoted to a full, recall-visible contact
        assert row["relationship"] == "father"  # missing relationship filled from the slot
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_promotes_partial_but_does_not_overwrite_existing_relationship(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open_people()
    try:
        await _insert_person(db, name="Karen", is_partial=1, relationship="sister")
        res = await ps._execute_action(_Conn(db), "person_create",
                                       {"name": "Karen", "relationship": "aunt"}, USER)
        assert res["promoted"] is True
        row = await _person(db, "Karen")
        assert row["is_partial"] == 0
        assert row["relationship"] == "sister"  # existing relationship preserved, not clobbered
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_full_contact_dedup_is_unchanged(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    db = await _open_people()
    try:
        await _insert_person(db, name="Julie", is_partial=0, relationship="wife")
        res = await ps._execute_action(_Conn(db), "person_create",
                                       {"name": "julie", "relationship": "friend"}, USER)
        assert res["created"] is False
        assert "promoted" not in res       # a full contact is not re-promoted
        row = await _person(db, "Julie")
        assert row["is_partial"] == 0
        assert row["relationship"] == "wife"  # untouched
    finally:
        await db.close()


# ── Part B — birthday capture ────────────────────────────────────────────────


async def _open_extractor_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT,
            circle TEXT, context TEXT, visibility TEXT,
            is_partial INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
            notification_count INTEGER DEFAULT 0, last_contacted_at TEXT)"""
    )
    await db.execute(
        """CREATE TABLE person_important_dates (
            id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, label TEXT,
            date_type TEXT, month INTEGER, day INTEGER, year INTEGER, mem_id TEXT)"""
    )
    await db.execute(
        """CREATE TABLE person_activities (
            id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, activity_type TEXT,
            description TEXT, source TEXT, venue TEXT, session_id TEXT, mem_id TEXT)"""
    )
    await db.commit()
    return db


async def _counts(db):
    async with db.execute("SELECT COUNT(*) FROM people WHERE user_id=?", (USER,)) as c:
        people = (await c.fetchone())[0]
    async with db.execute("SELECT month, day FROM person_important_dates WHERE user_id=?", (USER,)) as c:
        dates = [dict(r) for r in await c.fetchall()]
    return people, dates


_BDAY_TEXT = "Niel's birthday is 15 March"


@pytest.mark.asyncio
async def test_birthday_capture_on_creates_person_and_date(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED", "1")
    monkeypatch.setattr(pe, "_ingest_to_mempalace", _stub_ingest)
    db = await _open_extractor_db()
    try:
        await pe.process_text(_BDAY_TEXT, user_id=USER, db=db)
        people, dates = await _counts(db)
        assert people == 1                     # a stub was minted for the not-yet-contact
        assert len(dates) == 1                  # the birthday landed
        assert (dates[0]["month"], dates[0]["day"]) == (3, 15)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_birthday_capture_off_is_noop(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED", raising=False)
    monkeypatch.setattr(pe, "_ingest_to_mempalace", _stub_ingest)
    db = await _open_extractor_db()
    try:
        await pe.process_text(_BDAY_TEXT, user_id=USER, db=db)
        people, dates = await _counts(db)
        assert people == 0 and dates == []      # byte-for-byte no-op: nothing written
    finally:
        await db.close()


async def _stub_ingest(*_args, **_kwargs):
    """Keep the test hermetic — no MemPalace/model dependency for a ci_safe run."""
    return None
