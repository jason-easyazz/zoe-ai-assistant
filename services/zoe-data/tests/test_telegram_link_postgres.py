"""The messaging-link guarantees on REAL PostgreSQL (alembic 0084): durable single use, one holder per messaging id, no moved
link, and a redemption racing the member erase. The ci_safe fakes enforce these themselves; this file shows the database does.

Opt-in, against a THROWAWAY database only (its name must contain "test" or "scratch"; anything else is refused):

    TELEGRAM_LINK_TEST_PG_URL=postgresql://... python3 -B -m pytest -p no:cacheprovider -q services/zoe-data/tests/test_telegram_link_postgres.py

Run it FROM THE REPO ROOT with the system python3: it relies on the root pytest.ini (pythonpath, asyncio mode).

Not ``ci_safe`` (the slim lane has no database); without the variable every test skips. Each test runs in its OWN new schema
holding only the tables the link code touches (``users``, ``user_preferences``, ``memory_forgotten``, then migration 0084's own
statements), dropped at teardown, so the database's other contents and its migration state are never read or changed. Real code
paths throughout: ``telegram_link.link`` / ``forget_user``, ``user_prefs.set_prefs``, ``erase_watermark.record``, over
``db_pool.AsyncpgCompat`` connections. Synthetic member ids and messaging ids only.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

URL = os.environ.get("TELEGRAM_LINK_TEST_PG_URL")
asyncpg = pytest.importorskip("asyncpg")
pytestmark = pytest.mark.skipif(not URL, reason="no throwaway PostgreSQL (TELEGRAM_LINK_TEST_PG_URL)")

import db_pool  # noqa: E402
import erase_watermark  # noqa: E402
import telegram_link as tl  # noqa: E402

SVC = Path(__file__).resolve().parents[1]
_MIG = SVC / "alembic" / "versions" / "0084_telegram_link_ledger.py"


def _migration():
    spec = importlib.util.spec_from_file_location("mig_0084_pg", _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _throwaway_or_refuse(url: str) -> None:
    name = (urlparse(url).path or "").lstrip("/").lower()
    if "test" not in name and "scratch" not in name:
        pytest.fail(f"refusing to run: database name {name!r} contains neither 'test' nor 'scratch' (throwaway databases only)")


_BASE = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)",
    """CREATE TABLE user_preferences (
           user_id TEXT PRIMARY KEY REFERENCES users(id),
           prefs TEXT NOT NULL DEFAULT '{}',
           updated_at TEXT NOT NULL DEFAULT NOW()::TEXT)""",
    """CREATE TABLE memory_forgotten (
           user_id TEXT NOT NULL, key_hash TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'entity', actor TEXT NOT NULL DEFAULT '',
           forgotten_at TEXT NOT NULL, shield_until TEXT NOT NULL, PRIMARY KEY (user_id, key_hash))""",
)


class World:
    def __init__(self, schema: str):
        self.schema = schema
        self.raws: list = []
        self.members = ["m-" + uuid.uuid4().hex[:8] for _ in range(2)]

    async def raw(self):
        c = await asyncpg.connect(URL, server_settings={"search_path": self.schema})
        self.raws.append(c)
        return c

    async def db(self):
        return db_pool.AsyncpgCompat(await self.raw())

    @contextlib.asynccontextmanager
    async def ctx(self):
        """``db_pool.get_db_ctx`` for code that opens its own connection (``forget_user``, the watermark store)."""
        yield await self.db()

    async def apply_0084(self):
        c = await self.raw()
        async with c.transaction():
            for sql in _migration().statements():
                await c.execute(sql)

    async def tid_of(self, uid: str):
        c = await self.raw()
        return await c.fetchval("SELECT prefs::jsonb ->> 'telegram_id' FROM user_preferences WHERE user_id = $1", uid)

    async def holders(self, tid: str) -> list:
        c = await self.raw()
        rows = await c.fetch("SELECT user_id FROM user_preferences WHERE prefs::jsonb ->> 'telegram_id' = $1", tid)
        return sorted(r["user_id"] for r in rows)


@pytest.fixture
async def world(monkeypatch):
    _throwaway_or_refuse(URL)
    w = World("tl_" + uuid.uuid4().hex[:12])
    admin = await asyncpg.connect(URL)
    await admin.execute(f'CREATE SCHEMA "{w.schema}"')
    c = await w.raw()
    for sql in _BASE:
        await c.execute(sql)
    for uid in w.members:
        await c.execute("INSERT INTO users (id, name) VALUES ($1, 'synthetic')", uid)
    monkeypatch.setattr(db_pool, "get_db_ctx", w.ctx)
    monkeypatch.setattr(erase_watermark, "_backend", erase_watermark.SqlBackend(ctx=w.ctx))
    try:
        yield w
    finally:
        for raw in w.raws:
            await raw.close()
        await admin.execute(f'DROP SCHEMA "{w.schema}" CASCADE')
        await admin.close()


async def _link(w: World, uid: str, tid: str, token: str):
    user_id, token_id, exp = tl.decode_link_token(token)
    assert user_id == uid
    return await tl.link(await w.db(), uid, tid, token_id=token_id, expires_at=exp)


# (a) the migration: existing duplicate holders are cleared, the index exists, the database refuses a second holder
async def test_migration_clears_duplicate_holders_and_the_index_refuses_a_second(world):
    a, b = world.members
    c = await world.raw()
    await c.execute("INSERT INTO users (id, name) VALUES ('m-solo', 'synthetic')")
    for uid, prefs in ((a, '{"telegram_id": "70001", "theme": "dark"}'), (b, '{"telegram_id": "70001"}'),
                       ("m-solo", '{"telegram_id": "70002"}')):
        await c.execute("INSERT INTO user_preferences (user_id, prefs) VALUES ($1, $2)", uid, prefs)
    await world.apply_0084()
    assert await world.holders("70001") == []
    assert await world.holders("70002") == ["m-solo"]          # an unambiguous link is untouched
    assert await c.fetchval("SELECT prefs::jsonb ->> 'theme' FROM user_preferences WHERE user_id = $1", a) == "dark"
    assert await c.fetchval("SELECT 1 FROM pg_indexes WHERE schemaname = $1 AND indexname = $2",
                            world.schema, tl.TID_INDEX) == 1
    await world.apply_0084()                                     # a rerun is a no-op
    await c.execute("UPDATE user_preferences SET prefs = '{\"telegram_id\": \"70003\"}' WHERE user_id = $1", a)
    with pytest.raises(asyncpg.UniqueViolationError):
        await c.execute("UPDATE user_preferences SET prefs = '{\"telegram_id\": \"70003\"}' WHERE user_id = $1", b)


# (b) single use is durable: a second redemption on a fresh connection is refused
async def test_a_redeemed_token_is_spent_on_a_fresh_connection(world):
    a, _ = world.members
    await world.apply_0084()
    token = tl.make_link_token(a)
    assert await _link(world, a, "70011", token) == tl.LINKED
    assert await _link(world, a, "70011", token) == tl.TOKEN_SPENT
    assert await _link(world, a, "70012", token) == tl.TOKEN_SPENT
    assert await world.tid_of(a) == "70011"


# (c) two tokens racing for one messaging id: the database leaves one holder (the in-code holder check is held open so only
# the unique index can decide)
async def test_two_tokens_racing_for_one_id_leave_one_holder(world, monkeypatch):
    a, b = world.members
    await world.apply_0084()

    async def slow_no_holders(db, tid):
        await asyncio.sleep(0.3)
        return []

    monkeypatch.setattr(tl, "_holders", slow_no_holders)
    outcomes = await asyncio.gather(_link(world, a, "70021", tl.make_link_token(a)), _link(world, b, "70021", tl.make_link_token(b)))
    assert sorted(outcomes) == sorted([tl.LINKED, tl.HELD_BY_OTHER]), outcomes
    assert len(await world.holders("70021")) == 1
    c = await world.raw()
    assert await c.fetchval(f"SELECT COUNT(*) FROM {tl.REDEMPTIONS}") == 1      # the refused redemption rolled back


# (d) one token redeemed twice at once on two connections: one success
async def test_one_token_redeemed_twice_at_once_succeeds_once(world):
    a, _ = world.members
    await world.apply_0084()
    token = tl.make_link_token(a)
    outcomes = await asyncio.gather(_link(world, a, "70031", token), _link(world, a, "70032", token))
    assert sorted(outcomes) == sorted([tl.LINKED, tl.TOKEN_SPENT]), outcomes
    assert await world.tid_of(a) in ("70031", "70032")
    assert len(await world.holders("70031")) + len(await world.holders("70032")) == 1


# (e) a redemption racing the member erase (the watermark, then forget_user, as MemoryService.delete_user orders them)
@pytest.mark.parametrize("erase_first", [False, True])
async def test_a_redemption_racing_the_member_erase_leaves_no_link(world, erase_first):
    a, _ = world.members
    await world.apply_0084()
    before = [tl.make_link_token(a), tl.make_link_token(a, ttl=599)]

    async def erase():
        await erase_watermark.record(a, actor="test")
        await tl.forget_user(a)

    redeem = _link(world, a, "70041", before[0])
    results = await asyncio.gather(*((erase(), redeem) if erase_first else (redeem, erase())))
    assert set(results) - {None} <= {tl.LINKED, tl.MEMBER_GONE}, results
    assert await world.tid_of(a) is None and await world.holders("70041") == []
    assert await _link(world, a, "70042", before[1]) == tl.MEMBER_GONE      # issued before the erase: void after it
    assert await world.tid_of(a) is None
    c = await world.raw()
    assert await c.fetchval(f"SELECT COUNT(*) FROM {tl.REDEMPTIONS} WHERE user_id = $1", a) == 0


# (f) a redemption cannot move an existing link
async def test_a_redemption_cannot_move_an_existing_link(world):
    a, _ = world.members
    await world.apply_0084()
    assert await _link(world, a, "70051", tl.make_link_token(a)) == tl.LINKED
    second = tl.make_link_token(a, ttl=598)
    assert await _link(world, a, "70052", second) == tl.MEMBER_LINKED
    assert await world.tid_of(a) == "70051" and await world.holders("70052") == []
    await tl.unlink(await world.db(), a, by="self")                           # the explicit path: unlink first
    assert await _link(world, a, "70052", second) == tl.LINKED
