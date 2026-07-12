"""QA review F5a: contact offers must age per USER TURN, never per packet build.

Old behaviour: `surface_pending_contacts_for_prompt` incremented `turns_elapsed`
on every for-prompt fold with `expire_after_turns=2`, so two packet builds
(including background/system ones the user never saw) silently expired an offer.

New contract proved here, over an in-memory SQLite shim (no pool, no Chroma):
- store_suggestions writes expire_after_turns=6 and drops junk names (User/Zoe);
- surfacing is non-destructive: it only marks the offer surfaced (0 -> 1) and
  repeated surfacing never advances the counter;
- age_person_offers_on_user_turn advances ONLY surfaced offers, one per call,
  and resolves an offer past its expire_after_turns;
- surfaced_person_offers lists surfaced un-resolved offers oldest-first;
  mark_resolved reports honestly whether a row actually flipped.
"""
import re
from contextlib import asynccontextmanager

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import pending_suggestions as ps

USER = "demo_offer_aging_user"  # a DEMO user — never a real person
SESSION = "sess-1"


class _DB:
    """asyncpg-ish shim over aiosqlite: $N→? rewrite, fetch/fetchrow/execute."""

    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql: str) -> str:
        return re.sub(r"\$(\d+)", "?", sql)

    async def execute(self, sql, *args):
        await self._db.execute(self._tr(sql), args)
        await self._db.commit()

    async def fetch(self, sql, *args):
        async with self._db.execute(self._tr(sql), args) as c:
            rows = await c.fetchall()
        await self._db.commit()  # UPDATE ... RETURNING must persist
        return rows

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None


async def _open():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
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


def _wire(monkeypatch, db):
    @asynccontextmanager
    async def _ctx():
        yield _DB(db)

    monkeypatch.setattr(ps, "get_db_ctx", _ctx)


async def _offer_rows(db):
    async with db.execute(
        "SELECT id, turns_elapsed, expire_after_turns, resolved, pre_filled_slots"
        " FROM pending_suggestions WHERE user_id=? ORDER BY created_at",
        (USER,),
    ) as c:
        return [dict(r) for r in await c.fetchall()]


def _person(name, rel=None, phrase=None):
    slots = {"name": name}
    if rel:
        slots["relationship"] = rel
    return {
        "action_type": "person_create",
        "description": f"Add {name} to contacts",
        "offer_phrase": phrase or f"Add {name} to your contacts?",
        "pre_filled_slots": slots,
    }


@pytest.mark.asyncio
async def test_store_uses_six_turn_expiry_and_drops_junk_names(monkeypatch):
    db = await _open()
    try:
        _wire(monkeypatch, db)
        n = await ps.store_suggestions(
            USER, SESSION, [_person("Caitlin", "friend"), _person("User"), _person("zoe")]
        )
        assert n == 1  # the junk literal names never stored
        rows = await _offer_rows(db)
        assert len(rows) == 1
        assert rows[0]["expire_after_turns"] == 6
        assert rows[0]["turns_elapsed"] == 0  # not yet surfaced
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_surfacing_is_non_destructive(monkeypatch):
    db = await _open()
    try:
        _wire(monkeypatch, db)
        await ps.store_suggestions(USER, SESSION, [_person("Caitlin", "friend")])

        # Ten packet builds — the old code would have expired the offer at two.
        for _ in range(10):
            out = await ps.surface_pending_contacts_for_prompt(USER)
            assert [o["name"] for o in out] == ["Caitlin"]
            assert out[0]["offer_phrase"] == "Add Caitlin to your contacts?"

        rows = await _offer_rows(db)
        assert rows[0]["resolved"] == 0
        assert rows[0]["turns_elapsed"] == 1  # marked surfaced once, never aged
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ages_only_surfaced_offers_and_expires_past_limit(monkeypatch):
    db = await _open()
    try:
        _wire(monkeypatch, db)
        await ps.store_suggestions(USER, SESSION, [_person("Caitlin"), _person("Delia")])

        # Un-surfaced offers never age.
        assert await ps.age_person_offers_on_user_turn(USER) == 0
        rows = await _offer_rows(db)
        assert all(r["turns_elapsed"] == 0 for r in rows)

        # Surface both, then age. Offer survives >= 4 user turns un-acted.
        await ps.surface_pending_contacts_for_prompt(USER)
        for expected in (2, 3, 4, 5, 6):
            assert await ps.age_person_offers_on_user_turn(USER) == 0
            rows = await _offer_rows(db)
            assert all(r["turns_elapsed"] == expected and r["resolved"] == 0 for r in rows)

        # Next turn crosses expire_after_turns=6 → both resolve.
        assert await ps.age_person_offers_on_user_turn(USER) == 2
        rows = await _offer_rows(db)
        assert all(r["resolved"] == 1 for r in rows)
        assert await ps.surface_pending_contacts_for_prompt(USER) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_surfaced_person_offers(monkeypatch):
    db = await _open()
    try:
        _wire(monkeypatch, db)
        await ps.store_suggestions(USER, SESSION, [_person("Caitlin", "friend")])

        # Not surfaced yet → a bare "yes" has nothing to bind to.
        assert await ps.surfaced_person_offers(USER) == []

        await ps.surface_pending_contacts_for_prompt(USER)
        offers = await ps.surfaced_person_offers(USER)
        assert len(offers) == 1
        assert offers[0]["name"] == "Caitlin"
        assert offers[0]["relationship"] == "friend"
        assert offers[0]["id"]

        # mark_resolved is honest: True on the real flip, False on a repeat
        # (already resolved) or a foreign/missing id.
        assert await ps.mark_resolved(offers[0]["id"], USER) is True
        assert await ps.mark_resolved(offers[0]["id"], USER) is False
        assert await ps.mark_resolved("no-such-id", USER) is False
        assert await ps.surfaced_person_offers(USER) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_no_duplicate_offer_for_same_name(monkeypatch):
    # Observed live: the LLM detector re-proposed the same person on a later
    # turn, minting a second un-resolved offer. The store choke point must
    # drop a person_create proposal whose name already has a live offer.
    db = await _open()
    try:
        _wire(monkeypatch, db)
        assert await ps.store_suggestions(USER, SESSION, [_person("Caitlin", "friend")]) == 1
        assert await ps.store_suggestions(
            USER, "other-session", [_person("caitlin", phrase="Should I add Caitlin?")]
        ) == 0
        rows = await _offer_rows(db)
        assert len(rows) == 1
        # A RESOLVED offer no longer blocks a fresh proposal.
        await db.execute("UPDATE pending_suggestions SET resolved=1")
        await db.commit()
        assert await ps.store_suggestions(USER, SESSION, [_person("Caitlin", "friend")]) == 1
    finally:
        await db.close()
