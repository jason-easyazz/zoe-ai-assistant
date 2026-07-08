"""Phase 2b of contacts-from-known-people (ADR-contacts-from-known-people.md).

`contact_backfill.backfill_contacts` reads a user's `person`-type MemPalace
memories, extracts distinct name (+ relationship), dedups against existing
contacts, and emits `person_create` **pending suggestions** (proposals — never a
silent contact write). Proves: flag-off = byte-for-byte no-op; flag-on emits a
proposal per new name with the parsed relationship; existing contacts are skipped
(dedup); pronoun/junk names are rejected.

Slim-dep: an in-memory SQLite `people` table + a fake memory source
(monkeypatched onto the module's memory-service getter) + a fake
`store_suggestions` that records what would be stored. No pool, no Chroma.
"""
import re
import types

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import contact_backfill as cb

USER = "demo_backfill_user"  # a DEMO user — never a real person


class _Ref:
    """Stand-in for MemoryService's MemoryRef (only .text / .metadata used)."""

    def __init__(self, text, memory_type="person", entity_type="person", tags="person"):
        self.text = text
        self.metadata = {
            "memory_type": memory_type,
            "entity_type": entity_type,
            "tags": tags,
        }


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _DB:
    """asyncpg-ish shim over aiosqlite: $N→? rewrite, execute() returns a cursor."""

    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql):
        return re.sub(r"\$(\d+)", "?", sql)

    async def execute(self, sql, *args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = tuple(args[0])
        cur = await self._db.execute(self._tr(sql), args)
        rows = await cur.fetchall()
        return _Cursor(rows)


async def _open(existing=()):
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT,
            circle TEXT, context TEXT, visibility TEXT,
            is_partial INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0)"""
    )
    for i, nm in enumerate(existing):
        await db.execute(
            "INSERT INTO people (id, user_id, name, deleted) VALUES (?,?,?,0)",
            (f"p{i}", USER, nm),
        )
    await db.commit()
    return db


def _fake_memory_source(monkeypatch, refs):
    """Point contact_backfill.get_memory_service at a stub returning `refs`."""

    class _Svc:
        async def load_for_prompt(self, user_id, *, limit=200):
            return list(refs)

    def _get():
        return _Svc()

    # get_memory_service / is_guest_memory_user are imported lazily from
    # memory_service inside backfill_contacts, so patch them on that module.
    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", _get)
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: not u)


def _capture_store(monkeypatch):
    """Replace pending_suggestions.store_suggestions with a recorder."""
    stored: list[dict] = []

    async def _store(user_id, session_id, suggestions):
        stored.extend(suggestions)
        return len(suggestions)

    import pending_suggestions

    monkeypatch.setattr(pending_suggestions, "store_suggestions", _store)
    return stored


def _use_db(monkeypatch, db):
    """Make backfill's `_ensure_db(None)` yield our sqlite shim (no pool)."""
    import person_extractor

    async def _ensure(db_arg):
        return (db_arg if db_arg is not None else _DB(db)), False

    monkeypatch.setattr(person_extractor, "_ensure_db", _ensure)


_MEMS = [
    _Ref("Janice is Jason's mother."),
    _Ref("Niel (father)"),
    _Ref("Jason's sister is Karen."),
    _Ref("Julie loves gardening."),
    _Ref("She is a lovely person."),  # pronoun — must be rejected
]


@pytest.mark.asyncio
async def test_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv("ZOE_CONTACT_BACKFILL_ENABLED", raising=False)
    # No memory source / db patched: prove it never touches them when off.
    res = await cb.backfill_contacts(USER)
    assert res == {"enabled": False, "proposed": 0, "skipped_existing": 0, "candidates": 0}


@pytest.mark.asyncio
async def test_flag_on_emits_person_create_for_new_names(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open()
    try:
        _fake_memory_source(monkeypatch, _MEMS)
        _use_db(monkeypatch, db)
        stored = _capture_store(monkeypatch)

        res = await cb.backfill_contacts(USER)

        names = {s["pre_filled_slots"]["name"] for s in stored}
        assert names == {"Janice", "Niel", "Karen", "Julie"}
        assert res["proposed"] == 4
        assert all(s["action_type"] == "person_create" for s in stored)
        # Relationships parsed where present.
        rel = {s["pre_filled_slots"]["name"]: s["pre_filled_slots"].get("relationship")
               for s in stored}
        assert rel["Janice"] == "mother"
        assert rel["Niel"] == "father"
        assert rel["Karen"] == "sister"
        assert rel["Julie"] is None  # bare-name mention, no relationship stated
        # Proposal shape: an offer_phrase the UI can render.
        assert all(s["offer_phrase"].startswith("Add ") for s in stored)
        # Pronoun "She" rejected — never proposed.
        assert "She" not in names
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_existing_contacts_are_skipped(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open(existing=["Janice", "Karen"])
    try:
        _fake_memory_source(monkeypatch, _MEMS)
        _use_db(monkeypatch, db)
        stored = _capture_store(monkeypatch)

        res = await cb.backfill_contacts(USER)

        names = {s["pre_filled_slots"]["name"] for s in stored}
        # Janice + Karen already exist → only the two new people proposed.
        assert names == {"Niel", "Julie"}
        assert res["skipped_existing"] == 2
        assert res["proposed"] == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_non_person_memories_ignored(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open()
    try:
        # A fact-type memory naming someone should not feed the person backfill.
        mems = [_Ref("Karen loves tea.", memory_type="fact", entity_type="", tags="")]
        _fake_memory_source(monkeypatch, mems)
        _use_db(monkeypatch, db)
        stored = _capture_store(monkeypatch)

        res = await cb.backfill_contacts(USER)
        assert stored == []
        assert res["candidates"] == 0 and res["proposed"] == 0
    finally:
        await db.close()


def test_extract_people_parses_relationships():
    assert ("Janice", "mother") in cb._extract_people("Janice is Jason's mother.")
    assert ("Niel", "father") in cb._extract_people("Niel (father)")
    assert ("Karen", "sister") in cb._extract_people("Jason's sister is Karen.")
    assert ("Bob", "friend") in cb._extract_people("my friend Bob came over")
    # Pronoun rejected by the shared _looks_like_person_name guard — "She" is
    # never emitted as a person (Tom is only the anchor, not a subject here).
    assert all(n != "She" for n, _ in cb._extract_people("She is Tom's sister."))
