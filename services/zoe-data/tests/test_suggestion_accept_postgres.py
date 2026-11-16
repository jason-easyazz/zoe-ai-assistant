"""Accepting a pending suggestion on REAL PostgreSQL: the atomic claim, its row lock, and the real rollback (which the fakes in
``test_reminders_one_path.py`` only model). Opt-in: set ``ZOE_TEST_PG_URL`` to a PostgreSQL database you may create a schema in
(CI's service container is picked up via ``POSTGRES_URL`` when ``CI`` is set). Not ``ci_safe`` (the slim lane has no database);
skips cleanly without one, and is REQUIRED (an unset URL fails, never skips) under ``ZOE_REQUIRE_PG_TESTS``.

    ZOE_TEST_PG_URL=postgresql://... python3 -B -m pytest -p no:cacheprovider -q services/zoe-data/tests/test_suggestion_accept_postgres.py

Run from the repo root (the root pytest.ini carries the asyncio mode). Each test runs in its OWN new schema holding only the tables
the accept path writes, created from the migrations' own CREATE TABLE text (alembic 0001: users, reminders, notifications; 0008:
pending_suggestions; 0012: reminders.schedule_generation), dropped with CASCADE at teardown, so the database's other contents and
its migration state are never read or changed. Real code: ``pending_suggestions.execute_suggestion`` over a real ``asyncpg`` pool
(its connections are the raw ones production hands it, wrapped in ``db_pool.AsyncpgCompat`` for the reminder store). Only the
role matrix, the panel broadcast and the scheduler seam are replaced (recorded). Synthetic member and text only."""
from __future__ import annotations

import asyncio
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

import pending_suggestions  # noqa: E402

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
#: (migration file, table) in creation order: users first (the FK parent)
_TABLES = (("0001_initial_schema.py", "users"), ("0001_initial_schema.py", "reminders"),
           ("0001_initial_schema.py", "notifications"), ("0008_pending_suggestions.py", "pending_suggestions"),
           ("0018_app_settings.py", "app_settings"), ("0038_memory_forgotten.py", "memory_forgotten"))   # 0018's first CREATE is its PostgreSQL branch
_ALTER = ("0012_proactive_scheduled_retry.py", r"ALTER TABLE reminders ADD COLUMN IF NOT EXISTS schedule_generation [^\"]+")
GOOD = '{"title": "book the vet", "date": "2099-06-16", "time": "15:00"}'


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


class World:
    def __init__(self, schema, pool, admin):
        self.schema, self.pool, self.admin = schema, pool, admin
        self.uid = "pgtest-" + uuid.uuid4().hex[:10]
        self.scheduled, self.announced = [], []

    async def suggest(self, sid="sug-1", action="reminder_create", slots=GOOD):
        await self.admin.execute(
            "INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, offer_phrase, pre_filled_slots, "
            "created_at) VALUES ($1, $2, 's', $3, 'd', 'o', $4, 'now')", sid, self.uid, action, slots)

    async def resolved(self, sid="sug-1"):
        return await self.admin.fetchval("SELECT resolved FROM pending_suggestions WHERE id = $1", sid)

    async def reminders(self):
        return await self.admin.fetch("SELECT id, user_id, title, due_date, due_time FROM reminders WHERE user_id = $1", self.uid)

    def accept(self, sid="sug-1"):
        return pending_suggestions.execute_suggestion(sid, self.uid)

    async def blocked_claims(self):
        """Backends waiting on a row lock inside the claiming UPDATE: the loser of a race, really blocked in PostgreSQL."""
        return await self.admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' "
            "AND query ILIKE '%UPDATE pending_suggestions SET resolved = 1%'")

    async def wait_for_a_blocked_claim(self, seconds=15):
        for _ in range(int(seconds * 50)):
            if await self.blocked_claims():
                return
            await asyncio.sleep(0.02)
        raise AssertionError("no accept ever blocked on the suggestion's row")


@pytest.fixture
async def world(monkeypatch):
    schema = "sa_" + uuid.uuid4().hex[:12]
    root = await asyncpg.connect(URL)
    await root.execute(f'CREATE SCHEMA "{schema}"')
    admin = await asyncpg.connect(URL, server_settings={"search_path": schema})
    for f, t in _TABLES:
        await admin.execute(_create_sql(f, t))
    alter = re.findall(_ALTER[1], (_VERSIONS / _ALTER[0]).read_text(encoding="utf-8"))
    assert alter, _ALTER
    await admin.execute(alter[0].strip())
    pool = await asyncpg.create_pool(URL, min_size=3, max_size=6, server_settings={"search_path": schema})
    w = World(schema, pool, admin)
    await admin.execute("INSERT INTO users (id, name) VALUES ($1, 'synthetic')", w.uid)

    async def allow(*_a, **_k):
        return None

    async def schedule(_db, row, **_k):
        w.scheduled.append(row["id"])

    async def announce(*a, **_k):
        w.announced.append(a)

    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: pool)
    monkeypatch.setattr("reminder_service.require_feature_access", allow)           # the role matrix is not under test
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", announce)
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", schedule)   # the scheduler seam, recorded
    monkeypatch.setattr("anticipation.mode", lambda: "off")
    try:
        yield w
    finally:
        await pool.close()
        await admin.close()
        await root.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await root.close()


def _hold_the_winner(world, *, then_raise=False):
    """Wrap ``_execute_action``: the FIRST caller (the one that won the claim) runs the real action, then is held INSIDE its
    transaction until another accept is really blocked on the row; with ``then_raise`` it then fails (the rollback case)."""
    real, calls = pending_suggestions._execute_action, {"n": 0}

    async def held(conn, action, slots, user_id, after_commit=None):
        calls["n"] += 1
        out = await real(conn, action, slots, user_id, after_commit)
        if calls["n"] == 1:
            await world.wait_for_a_blocked_claim()
            if then_raise:
                raise RuntimeError("induced failure after the reminder insert")
        return out

    return held, calls


async def test_two_concurrent_accepts_make_one_reminder(world, monkeypatch):
    await world.suggest()
    held, calls = _hold_the_winner(world)
    monkeypatch.setattr(pending_suggestions, "_execute_action", held)
    a, b = await asyncio.wait_for(asyncio.gather(world.accept(), world.accept()), timeout=60)
    assert sorted([a["ok"], b["ok"]]) == [False, True]
    assert (a if not a["ok"] else b) == {"ok": False, "error": "not_found"}
    assert len(await world.reminders()) == 1 and await world.resolved() == 1
    assert calls["n"] == 1 and len(world.scheduled) == 1 and len(world.announced) == 1   # the loser never reached the action


async def test_a_winner_that_fails_hands_the_offer_to_the_waiting_accept(world, monkeypatch):
    await world.suggest()
    held, _ = _hold_the_winner(world, then_raise=True)
    monkeypatch.setattr(pending_suggestions, "_execute_action", held)
    a, b = await asyncio.wait_for(asyncio.gather(world.accept(), world.accept()), timeout=60)
    assert sorted([a["ok"], b["ok"]]) == [False, True]            # the winner rolled back; the waiter claimed and succeeded
    assert len(await world.reminders()) == 1 and await world.resolved() == 1
    assert len(world.scheduled) == 1                              # the rolled-back reminder was never scheduled


async def test_a_failure_after_the_insert_rolls_back_for_real_and_the_retry_makes_one(world, monkeypatch):
    await world.suggest()
    real = pending_suggestions._execute_action

    async def abort_after_insert(conn, action, slots, user_id, after_commit=None):
        out = await real(conn, action, slots, user_id, after_commit)
        assert await conn.fetchval("SELECT count(*) FROM reminders WHERE user_id = $1", user_id) == 1   # visible inside the txn
        await conn.execute("SELECT 1/0")      # an error that ABORTS the transaction: the explicit undo cannot even run, only ROLLBACK
        return out

    monkeypatch.setattr(pending_suggestions, "_execute_action", abort_after_insert)
    out = await world.accept()
    assert out["ok"] is False
    assert await world.reminders() == [] and await world.resolved() == 0 and world.scheduled == [] and world.announced == []
    monkeypatch.setattr(pending_suggestions, "_execute_action", real)
    out = await world.accept()
    assert out["ok"] is True and len(await world.reminders()) == 1 and await world.resolved() == 1 and len(world.scheduled) == 1


@pytest.mark.parametrize("exit_name", ["unknown_action", "needs_time", "unreadable_time", "past_day", "dispute_not_applied",
                                       "forget_alias_not_applied", "action_raises"])
async def test_every_non_success_exit_leaves_the_suggestion_open(world, monkeypatch, exit_name):
    import memory_disputes
    import memory_forget_alias

    action, slots = "reminder_create", '{"title": "book the vet"}'
    if exit_name == "unknown_action":
        action = "no_such_action"
    elif exit_name == "unreadable_time":
        slots = '{"title": "t", "date": "2099-06-16", "time": "zxqv blorp"}'
    elif exit_name == "past_day":
        slots = '{"title": "t", "date": "2001-01-01", "time": "9am"}'
    elif exit_name == "dispute_not_applied":
        action = "memory_dispute"
    elif exit_name == "forget_alias_not_applied":
        action = "forget_alias"

    async def no(*_a, **_k):
        return False

    async def boom(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(memory_disputes, "resolve", no)
    monkeypatch.setattr(memory_forget_alias, "forget_confirmed", no)
    if exit_name == "action_raises":
        monkeypatch.setattr(pending_suggestions, "_execute_action", boom)
    await world.suggest(action=action, slots=slots)
    out = await world.accept()
    assert out["ok"] is False
    assert await world.resolved() == 0 and await world.reminders() == [] and world.scheduled == []


async def test_two_concurrent_nudge_claims_on_two_pooled_connections_claim_once(world, monkeypatch):
    """The once-a-day nudge claim (``nudge_claims``: an ``app_settings`` row with a unique key) on real PostgreSQL: of two ticks racing
    for the same (member, person, day) exactly one wins, a released claim can be taken again, the prune and the member erase work."""
    import contextlib
    from datetime import datetime, timedelta, timezone

    import db_pool
    import nudge_claims

    @contextlib.asynccontextmanager
    async def pooled():
        async with world.pool.acquire() as conn:     # a DIFFERENT pooled connection per call, as two processes would have
            yield db_pool.AsyncpgCompat(conn)

    import user_erase_gate as erase_gate

    monkeypatch.setattr(nudge_claims, "_db", pooled)
    erase_gate.reset()
    started = datetime.now(timezone.utc) - timedelta(seconds=1)
    gen = erase_gate.generation(world.uid)

    async def take(key, at):
        return await nudge_claims.claim(key, user_id=world.uid, gen=gen, started_at=started, now=at)

    now = datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)
    for n in range(10):                              # many rounds: one race can be won by luck, ten cannot
        key = nudge_claims.key_for("people_health", world.uid, f"p{n}", "2026-06-10")
        a, b = await asyncio.gather(take(key, now), take(key, now))
        assert sorted([a, b]) == [False, True], (n, a, b)
    key = nudge_claims.key_for("people_health", world.uid, "p0", "2026-06-10")
    await nudge_claims.release(key)
    assert await take(key, now)                                  # released: a later tick may take it
    old = nudge_claims.key_for("people_health", world.uid, "p0", "2026-05-01")
    assert await take(old, now - timedelta(days=30))
    assert await take(nudge_claims.key_for("people_health", world.uid, "p1", "2026-06-11"), now + timedelta(days=1))
    assert await world.admin.fetchval("SELECT count(*) FROM app_settings WHERE key = $1", old) == 0   # pruned: older than a week
    other = nudge_claims.key_for("people_health", "someone-else", "p0", "2026-06-10")
    assert await nudge_claims.claim(other, user_id="someone-else", gen=0, started_at=started, now=now)
    removed = await nudge_claims.forget_user(world.uid)
    assert removed >= 10 and await world.admin.fetchval("SELECT count(*) FROM app_settings") == 1   # only the other member's key is left


async def test_a_member_erase_between_the_read_and_the_claim_wins_on_real_postgres(world, monkeypatch):
    """The erase-vs-claim race on two real connections: the claim's work began at ``started``; the durable mark (written first thing in
    ``delete_user``) lands afterwards, from ANOTHER connection, before the claim's single conditional INSERT runs; and the mirror: a
    claim that landed first is deleted by the member erase (taken as an eraser through the gate)."""
    import contextlib
    from datetime import datetime, timedelta, timezone

    import db_pool
    import erase_watermark
    import nudge_claims
    import user_erase_gate as erase_gate

    @contextlib.asynccontextmanager
    async def pooled():
        async with world.pool.acquire() as conn:
            yield db_pool.AsyncpgCompat(conn)

    monkeypatch.setattr(nudge_claims, "_db", pooled)
    erase_gate.reset()
    started = erase_watermark._now()
    gen = erase_gate.generation(world.uid)
    key = nudge_claims.key_for("people_health", world.uid, "p1", "2026-06-10")
    # the delete's durable mark, from a second connection, AFTER the work began (no in-memory gate bump: another process)
    mark = (started + timedelta(milliseconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    await world.admin.execute(
        "INSERT INTO memory_forgotten (user_id, key_hash, scope, actor, forgotten_at, shield_until) VALUES ($1, $2, 'erase', '', $3, $4)",
        world.uid, erase_watermark.KEY, mark, erase_watermark.NEVER)
    assert not await nudge_claims.claim(key, user_id=world.uid, gen=gen, started_at=started)
    assert await world.admin.fetchval("SELECT count(*) FROM app_settings") == 0                 # no claim row holding the member's id
    # work that began AFTER the erase is a new world: it claims; then the mirror, the member erase removes it
    later = erase_watermark._now() + timedelta(seconds=1)
    assert await nudge_claims.claim(key, user_id=world.uid, gen=gen, started_at=later)
    assert await world.admin.fetchval("SELECT count(*) FROM app_settings") == 1
    with erase_gate.erase_span(world.uid):
        assert await nudge_claims.forget_user(world.uid) == 1
    assert await world.admin.fetchval("SELECT count(*) FROM app_settings") == 0
    # the gate's generation: work that began before an erase ran in this process is refused without touching the table
    assert not await nudge_claims.claim(key, user_id=world.uid, gen=gen, started_at=later)
    assert await world.admin.fetchval("SELECT count(*) FROM app_settings") == 0
