"""The routines write gate on REAL PostgreSQL: the per-member advisory locks, the real transaction and the durable re-read of the
forget marks inside it (``routines_store._guarded_write`` / ``_guarded_erase``), which the ci_safe SQLite fixture cannot show (it
has no ``transaction``, so ``_txn`` runs unwrapped there). Opt-in: set ``ZOE_TEST_PG_URL`` to a PostgreSQL database you may create a
schema in (CI's service container is picked up via ``POSTGRES_URL`` when ``CI`` is set). Not ``ci_safe`` (the slim lane has no
database); skips cleanly without one, and is REQUIRED (an unset URL fails, never skips) under ``ZOE_REQUIRE_PG_TESTS``.

    ZOE_TEST_PG_URL=postgresql://... python3 -B -m pytest -p no:cacheprovider -q services/zoe-data/tests/test_routines_gate_postgres.py

Run from the repo root (the root pytest.ini carries the asyncio mode). Each test runs in its OWN new schema holding only the tables
the gate reads and writes, created from the migrations' own CREATE TABLE text (alembic 0081: routines, routine_steps,
routine_days, routine_day_steps; 0038: memory_forgotten, which also holds the erase watermark row), dropped with CASCADE at
teardown. Real code over a real ``asyncpg`` pool, each connection wrapped in ``db_pool.AsyncpgCompat`` as production wraps it.

TWO PROCESSES. The writer and the eraser run as two tasks on two pooled connections, with this process's in-memory erase gate
(``user_erase_gate``) made blind - no lock, no generation - which is exactly what a second process looks like to it. Only the
advisory locks and the durable marks re-read under them can keep a forget. A hook holds one side INSIDE its transaction until a
third connection sees the other really blocked on an advisory lock in ``pg_stat_activity``. Synthetic members and text only."""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import uuid
from pathlib import Path

import pytest

URL = os.environ.get("ZOE_TEST_PG_URL") or (os.environ.get("POSTGRES_URL") if os.environ.get("CI") else None)
REQUIRED = bool(os.environ.get("ZOE_REQUIRE_PG_TESTS"))
if REQUIRED:
    assert URL, "ZOE_REQUIRE_PG_TESTS is set but no scratch PostgreSQL URL (ZOE_TEST_PG_URL) was supplied"
    import asyncpg
else:
    asyncpg = pytest.importorskip("asyncpg")
pytestmark = pytest.mark.skipif(not URL, reason="no scratch PostgreSQL (ZOE_TEST_PG_URL)")

import db_pool  # noqa: E402
import erase_watermark  # noqa: E402
import memory_forgotten  # noqa: E402
import memory_tombstones  # noqa: E402
import routines_store as rs  # noqa: E402
import user_erase_gate  # noqa: E402

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
#: (migration file, table) in creation order: routines first (the FK parent of the other three)
_TABLES = (("0081_routines.py", "routines"), ("0081_routines.py", "routine_steps"), ("0081_routines.py", "routine_days"),
           ("0081_routines.py", "routine_day_steps"), ("0038_memory_forgotten.py", "memory_forgotten"))
ALEX, SAM = "m-alex", "m-sam"
SALT = "routines-pg-test-ledger-secret-0123456789"


def _create_sql(file: str, table: str) -> str:
    """The migration's own ``CREATE TABLE IF NOT EXISTS <table> (...)`` statement, by matching parentheses."""
    text = (_VERSIONS / file).read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\(", text)
    assert m, f"{file}: no CREATE TABLE for {table}"
    depth, i = 1, m.end()
    while depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    sql = text[m.start():i]
    assert not re.search(r"\{\w+\}", sql), f"{table}: an f-string placeholder cannot be taken as SQL"
    return sql


class Recording(db_pool.AsyncpgCompat):
    """``AsyncpgCompat`` that also records which advisory lock keys each connection takes, in order."""

    locks: dict = {}

    def execute(self, sql, *params):
        if "pg_advisory_xact_lock" in sql:
            key = params[0][0] if len(params) == 1 and isinstance(params[0], (list, tuple)) else params[0]
            Recording.locks.setdefault(id(self), []).append(key)
        return super().execute(sql, *params)


class World:
    def __init__(self, schema, pool, admin):
        self.schema, self.pool, self.admin = schema, pool, admin

    async def blocked_on_advisory(self) -> int:
        """Backends of THIS test's pool really waiting on an advisory lock."""
        return await self.admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND application_name = $1 "
            "AND wait_event_type = 'Lock' AND wait_event = 'advisory'", self.schema)

    async def wait_for_a_blocked_waiter(self, seconds=15):
        for _ in range(int(seconds * 50)):
            if await self.blocked_on_advisory():
                return
            await asyncio.sleep(0.02)
        raise AssertionError("nobody ever blocked on the routines advisory lock")

    async def texts(self, table="routine_steps"):
        return sorted(r["text"] for r in await self.admin.fetch(f"SELECT text FROM {table}"))

    async def forget_from_another_process(self, member, name):
        """What the forget intent writes BEFORE the cascade runs, committed by another process (this one's cache is not told)."""
        await self.admin.execute(
            "INSERT INTO memory_forgotten (user_id, key_hash, scope, actor, forgotten_at, shield_until) VALUES ($1, $2, 'entity', $1, "
            "'2026-11-12T07:00:00Z', $3)", member, memory_forgotten.key_hash(member, name), memory_forgotten.PERMANENT_UNTIL)


@pytest.fixture
async def world(monkeypatch):
    schema = "rg_" + uuid.uuid4().hex[:12]
    root = await asyncpg.connect(URL)
    await root.execute(f'CREATE SCHEMA "{schema}"')
    admin = await asyncpg.connect(URL, server_settings={"search_path": schema})
    for f, t in _TABLES:
        await admin.execute(_create_sql(f, t))
    pool = await asyncpg.create_pool(URL, min_size=3, max_size=8,
                                     server_settings={"search_path": schema, "application_name": schema})

    @contextlib.asynccontextmanager
    async def ctx():
        async with pool.acquire() as conn:
            yield Recording(conn)

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)              # db_compat, memory_forgotten and erase_watermark all use it
    monkeypatch.setenv(memory_forgotten.SALT_ENV, SALT)
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    memory_forgotten.set_backend(None)                             # the real Postgres ledger
    erase_watermark.set_backend(erase_watermark.SqlBackend())      # the real watermark row
    memory_tombstones.clear_all()
    rs.reset_walks() if hasattr(rs, "reset_walks") else None
    Recording.locks = {}

    @contextlib.asynccontextmanager
    async def writing(_m, _g):                                     # this process's gate is blind: another process
        yield True

    @contextlib.asynccontextmanager
    async def erasing(_m, *, bump=True):
        yield

    monkeypatch.setattr(user_erase_gate, "writing", writing)
    monkeypatch.setattr(user_erase_gate, "erasing", erasing)
    monkeypatch.setattr(user_erase_gate, "generation", lambda _m: 0)
    monkeypatch.setattr(user_erase_gate, "in_progress", lambda _m: False)
    monkeypatch.setattr(user_erase_gate, "snapshot", dict)
    try:
        yield World(schema, pool, admin)
    finally:
        memory_forgotten.set_backend(None)
        erase_watermark.set_backend(None)
        await pool.close()
        await admin.close()
        await root.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await root.close()


def _hold_writer(world, monkeypatch):
    """The FIRST writer to pass its durable re-check (it holds the advisory locks, inside its transaction) is held there until another
    connection is really blocked on one of those locks. Returns an event set the moment it is holding."""
    real, holding = rs._durable_recheck, asyncio.Event()

    async def held(w, seen, texts):
        await real(w, seen, texts)
        if not holding.is_set():
            holding.set()
            await world.wait_for_a_blocked_waiter()
    monkeypatch.setattr(rs, "_durable_recheck", held)
    return holding


def _hold_eraser(world, monkeypatch, marker):
    """The eraser is held right after its scan (``marker``: the scan's SQL), inside its locked transaction, until a writer blocks."""
    real, holding = rs._rows, asyncio.Event()

    async def rows(db, sql, params=()):
        out = await real(db, sql, params)
        if sql.startswith(marker) and not holding.is_set():
            holding.set()
            await world.wait_for_a_blocked_waiter()
        return out
    monkeypatch.setattr(rs, "_rows", rows)
    return holding


_ENTITY_SCAN = "SELECT id, name FROM routines WHERE user_id"
_MEMBER_SCAN = "SELECT id FROM routines WHERE user_id"
QUINN = re.compile(r"\bquinn\b", re.I)


# (a) the writer holds the lock; the eraser waits, then erases what the writer wrote
async def test_a_writer_holding_the_lock_lands_first_and_the_entity_erase_then_removes_it(world, monkeypatch):
    r = await rs.create_routine(ALEX, "Morning", "waking", steps=("bins",))
    holding = _hold_writer(world, monkeypatch)
    writer = asyncio.ensure_future(rs.add_step(ALEX, r["id"], "call Quinn"))
    await asyncio.wait_for(holding.wait(), 15)

    async def forget():
        await world.forget_from_another_process(ALEX, "Quinn")
        return await rs.erase_entity(ALEX, QUINN, strict=True)
    added, removed = await asyncio.wait_for(asyncio.gather(writer, forget()), 60)
    assert added["text"] == "call Quinn" and removed == 1
    assert await world.texts() == ["bins"]


# (b) the eraser has recorded the forget and holds the lock; the writer waits, then the re-read ledger refuses it
async def test_an_eraser_holding_the_lock_makes_the_waiting_writer_re_read_the_ledger_and_refuse(world, monkeypatch):
    r = await rs.create_routine(ALEX, "Morning", "waking", steps=("bins",))
    assert await memory_forgotten.active_hashes(ALEX) == frozenset()    # this process's cached view: nothing forgotten
    holding = _hold_eraser(world, monkeypatch, _ENTITY_SCAN)
    await world.forget_from_another_process(ALEX, "Quinn")
    eraser = asyncio.ensure_future(rs.erase_entity(ALEX, QUINN, strict=True))
    await asyncio.wait_for(holding.wait(), 15)
    with pytest.raises(rs.StoreError) as e:
        await asyncio.wait_for(rs.add_step(ALEX, r["id"], "call Quinn"), 60)
    assert e.value.code == "names_forgotten"
    await asyncio.wait_for(eraser, 60)
    assert await memory_forgotten.active_hashes(ALEX) == frozenset()    # the fast path really was stale: the re-read decided
    assert await world.texts() == ["bins"]


# (c) two writers on one member serialise
async def test_two_writers_on_one_member_serialise(world, monkeypatch):
    r = await rs.create_routine(ALEX, "Morning", "waking", steps=("bins",))
    holding = _hold_writer(world, monkeypatch)
    first = asyncio.ensure_future(rs.add_step(ALEX, r["id"], "water the plants"))
    await asyncio.wait_for(holding.wait(), 15)
    await asyncio.wait_for(asyncio.gather(first, rs.add_step(ALEX, r["id"], "feed the cat")), 60)
    positions = [p["position"] for p in await world.admin.fetch("SELECT position FROM routine_steps ORDER BY position")]
    assert positions == [1, 2, 3]                     # MAX(position) + 1 read under the lock: never two lines at one position


# (e-a) the member-erase version of (a): the write lands first and the member erase that follows removes it
async def test_a_write_holding_the_lock_lands_first_and_the_member_erase_then_removes_it(world, monkeypatch):
    holding = _hold_writer(world, monkeypatch)
    writer = asyncio.ensure_future(rs.create_routine(SAM, "Morning", "waking", steps=("stretch",)))
    await asyncio.wait_for(holding.wait(), 15)

    async def delete_sam():
        await erase_watermark.record(SAM)            # delete_user's order: the durable mark, then the erasers
        return await rs.forget_user(SAM)
    made, removed = await asyncio.wait_for(asyncio.gather(writer, delete_sam()), 60)
    assert removed == 1 and (made is None or made["user_id"] == SAM)   # the read-back after commit may already find it erased
    for table in ("routines", "routine_steps"):
        assert await world.admin.fetchval(f"SELECT count(*) FROM {table}") == 0


# (e-b) the member-erase version of (b): the erase holds the lock; a write that began before its mark waits, then is refused
async def test_a_member_erase_holding_the_lock_refuses_the_waiting_write_that_began_before_it(world, monkeypatch):
    eraser_holding = _hold_eraser(world, monkeypatch, _MEMBER_SCAN)
    real_early = rs._forgotten_hashes

    async def early(member):                           # the write has STARTED; it waits here (before any lock) for the erase
        await asyncio.wait_for(eraser_holding.wait(), 15)
        return await real_early(member)
    monkeypatch.setattr(rs, "_forgotten_hashes", early)
    writer = asyncio.ensure_future(rs.create_routine(SAM, "Morning", "waking", steps=("stretch",)))
    await asyncio.sleep(0.05)

    async def delete_sam():
        await erase_watermark.record(SAM)
        return await rs.forget_user(SAM)
    eraser = asyncio.ensure_future(delete_sam())
    with pytest.raises(rs.StoreError) as e:
        await asyncio.wait_for(writer, 60)
    assert e.value.code == "erased"
    await asyncio.wait_for(eraser, 60)
    assert await world.admin.fetchval("SELECT count(*) FROM routines") == 0


# (d) a tick takes the subject's AND the actor's lock in sorted order; two opposite ticks do not deadlock
async def test_two_opposite_ticks_take_both_locks_in_sorted_order_and_do_not_deadlock(world, monkeypatch):
    house = await rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",))
    yard = await rs.create_routine(SAM, "Yard", "dinner", visibility="family", steps=("water the lawn",))
    a_line = (await rs.day_copy(SAM, house["id"]))["steps"][0]["id"]
    s_line = (await rs.day_copy(ALEX, yard["id"]))["steps"][0]["id"]
    Recording.locks = {}
    holding = _hold_writer(world, monkeypatch)
    first = asyncio.ensure_future(rs.act_tapped(SAM, a_line, "done", verified=True))     # Sam ticks Alex's line
    await asyncio.wait_for(holding.wait(), 15)
    a, b = await asyncio.wait_for(asyncio.gather(first, rs.act_tapped(ALEX, s_line, "done", verified=True)), 60)
    assert a["state"] == b["state"] == "done"
    takes = [keys for keys in Recording.locks.values() if len(keys) == 2]
    assert len(takes) == 2 and all(keys == [f"routines|{ALEX}", f"routines|{SAM}"] for keys in takes)


# (e-a, ticks) the member-erase version of (a): the tick lands first and the member erase that follows blanks the actor
async def test_a_tick_holding_the_lock_lands_first_and_the_actors_member_erase_then_blanks_it(world, monkeypatch):
    house = await rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",))
    line = (await rs.day_copy(SAM, house["id"]))["steps"][0]["id"]
    holding = _hold_writer(world, monkeypatch)
    tick = asyncio.ensure_future(rs.act_tapped(SAM, line, "done", verified=True))
    await asyncio.wait_for(holding.wait(), 15)

    async def delete_sam():
        await erase_watermark.record(SAM)            # delete_user's order: the durable mark, then the erasers
        return await rs.forget_user(SAM)
    await asyncio.wait_for(asyncio.gather(tick, delete_sam()), 60)
    assert await world.admin.fetchval("SELECT count(*) FROM routine_day_steps WHERE actor_user_id = $1", SAM) == 0


# (e-b, ticks) the member-erase version of (b): the erase holds the lock; the tick that began before it is refused by the re-read mark
async def test_a_member_erase_holding_the_lock_refuses_the_waiting_tick_that_began_before_it(world, monkeypatch):
    house = await rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",))
    line = (await rs.day_copy(SAM, house["id"]))["steps"][0]["id"]
    eraser_holding = _hold_eraser(world, monkeypatch, _MEMBER_SCAN)
    go = asyncio.Event()
    real_rows = rs._rows

    async def rows(db, sql, params=()):                # the tick's own first read waits until the erase is holding its lock
        out = await real_rows(db, sql, params)
        if sql.startswith("SELECT ds.state") and not go.is_set():
            await asyncio.wait_for(eraser_holding.wait(), 15)
            go.set()
        return out
    monkeypatch.setattr(rs, "_rows", rows)
    tick = asyncio.ensure_future(rs.act_tapped(SAM, line, "done", verified=True))       # began BEFORE the mark
    await asyncio.sleep(0.05)

    async def delete_sam():
        await erase_watermark.record(SAM)
        return await rs.forget_user(SAM)
    eraser = asyncio.ensure_future(delete_sam())
    with pytest.raises(rs.StoreError) as e:
        await asyncio.wait_for(tick, 60)
    assert e.value.code == "erased"
    await asyncio.wait_for(eraser, 60)
    assert await world.admin.fetchval("SELECT count(*) FROM routine_day_steps WHERE actor_user_id = $1", SAM) == 0
