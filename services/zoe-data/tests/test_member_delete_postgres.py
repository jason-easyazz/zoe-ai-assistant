"""The member delete's store erase (``member_owned_forget.forget_user``) on REAL PostgreSQL: one transaction with a savepoint per
statement, the foreign-key order with the migrations' own constraints, and a run started in another member's session.

Opt-in, against a THROWAWAY database only (its name must contain "test" or "scratch"; anything else is refused):

    MEMBER_DELETE_TEST_PG_URL=postgresql://... python3 -B -m pytest -p no:cacheprovider -q services/zoe-data/tests/test_member_delete_postgres.py

Run it FROM THE REPO ROOT with the system python3: it relies on the root pytest.ini (pythonpath, asyncio mode).

Not ``ci_safe`` (the slim lane has no database); without the variable every test skips. Each test runs in its OWN new schema holding
only the tables the erase touches, created from the migrations' own CREATE TABLE text (alembic 0001, 0006, 0052, plus 0006 / 0012's
ALTER statements), dropped with CASCADE at teardown, so the database's other contents and its migration state are never read or
changed. Real code paths throughout: ``member_owned_forget.forget_user`` over ``db_pool.AsyncpgCompat`` connections, with
``db_pool.get_db_ctx`` handing out fresh connections in the test schema. Synthetic member ids and text only.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

URL = os.environ.get("MEMBER_DELETE_TEST_PG_URL")
asyncpg = pytest.importorskip("asyncpg")
pytestmark = pytest.mark.skipif(not URL, reason="no throwaway PostgreSQL (MEMBER_DELETE_TEST_PG_URL)")

import db_pool  # noqa: E402
import member_owned_forget as mof  # noqa: E402

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: (migration file, table) in creation order (parents before children)
_TABLES = (
    ("0001_initial_schema.py", "users"),
    ("0001_initial_schema.py", "chat_sessions"),
    ("0001_initial_schema.py", "chat_messages"),
    ("0001_initial_schema.py", "chat_ag_ui_runs"),
    ("0001_initial_schema.py", "openclaw_run_state"),
    ("0001_initial_schema.py", "proactive_pending"),
    ("0001_initial_schema.py", "proactive_scheduled"),
    ("0001_initial_schema.py", "reminders"),
    ("0001_initial_schema.py", "notes"),
    ("0001_initial_schema.py", "lists"),
    ("0001_initial_schema.py", "list_items"),
    ("0001_initial_schema.py", "people"),
    ("0006_people_crm_expansion.py", "person_gift_ideas"),
    ("0001_initial_schema.py", "events"),
    ("0052_calendar_data_model.py", "event_attendees"),
    ("0001_initial_schema.py", "push_subscriptions"),
    ("0001_initial_schema.py", "ui_panel_sessions"),
)
#: the migrations' ALTER statements for the columns the erase reads (0012: the claim; 0006: a note's contact link)
_ALTERS = (
    ("0012_proactive_scheduled_retry.py", r"ALTER TABLE proactive_scheduled ADD COLUMN IF NOT EXISTS \w+ [^\"]+"),
    ("0012_proactive_scheduled_retry.py", r"ALTER TABLE reminders ADD COLUMN IF NOT EXISTS \w+ [^\"]+"),
    ("0006_people_crm_expansion.py", r"ALTER TABLE notes ADD COLUMN IF NOT EXISTS person_id [^\"]+"),
)


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


def _schema_sql() -> list:
    out = [_create_sql(f, t) for f, t in _TABLES]
    for f, pattern in _ALTERS:
        found = re.findall(pattern, (_VERSIONS / f).read_text(encoding="utf-8"))
        assert found, f"{f}: {pattern}"
        out.extend(s.strip() for s in found)
    return out


def _throwaway_or_refuse(url: str) -> None:
    name = (urlparse(url).path or "").lstrip("/").lower()
    if "test" not in name and "scratch" not in name:
        pytest.fail(f"refusing to run: database name {name!r} contains neither 'test' nor 'scratch' (throwaway databases only)")


class World:
    def __init__(self, schema: str):
        self.schema = schema
        self.raws: list = []
        self.a, self.b = ("m-" + uuid.uuid4().hex[:8] for _ in range(2))

    async def raw(self):
        c = await asyncpg.connect(URL, server_settings={"search_path": self.schema})
        self.raws.append(c)
        return c

    @contextlib.asynccontextmanager
    async def ctx(self):
        """``db_pool.get_db_ctx`` for ``forget_user`` (``db_compat.get_compat_db`` reads it at call time): a FRESH connection."""
        yield db_pool.AsyncpgCompat(await self.raw())

    async def ids(self, table: str, where: str = "TRUE", *args, col: str = "id") -> set:
        """``col`` of the matching rows (``id`` by default; a table keyed otherwise, e.g. ``ui_panel_sessions.panel_id``, names it)."""
        c = await self.raw()                                      # always a SEPARATE connection: only committed rows are visible
        return {r[0] for r in await c.fetch(f"SELECT {col} FROM {table} WHERE {where}", *args)}

    async def naming(self, uid: str) -> list:
        """Every (table, row) of the test schema with ``uid`` in any column."""
        c = await self.raw()
        out = []
        for (t,) in await c.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = $1", self.schema):
            if t == "users":
                continue
            for row in await c.fetch(f'SELECT * FROM "{t}"'):
                if any(uid in str(v) for v in row.values() if v is not None):
                    out.append((t, dict(row)))
        return out


@pytest.fixture
async def world(monkeypatch, tmp_path):
    _throwaway_or_refuse(URL)
    w = World("md_" + uuid.uuid4().hex[:12])
    admin = await asyncpg.connect(URL)
    await admin.execute(f'CREATE SCHEMA "{w.schema}"')
    c = await w.raw()
    for sql in _schema_sql():
        await c.execute(sql)
    for uid in (w.a, w.b):
        await c.execute("INSERT INTO users (id, name) VALUES ($1, 'synthetic')", uid)
    monkeypatch.setattr(db_pool, "get_db_ctx", w.ctx)
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(tmp_path / "voice-samples"))   # the clip erase never reaches a real corpus
    (tmp_path / "voice-samples").mkdir()
    try:
        yield w
    finally:
        for raw in w.raws:
            await raw.close()
        await admin.execute(f'DROP SCHEMA "{w.schema}" CASCADE')
        await admin.close()


def _meta(uid: str) -> str:
    return json.dumps({"user_id": uid})


async def _seed(w: World) -> None:
    """A and B with rows in every exercised store; a session A owns that B also spoke in; a run A started in B's session that holds
    NO turn of A's; A's family list / contact / event (kept) and private ones (erased); B's note on A's private contact."""
    a, b = w.a, w.b
    c = await w.raw()
    x = c.execute
    for u in (a, b):
        await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ($1, $2, 'own chat')", f"s-{u}", u)
        await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ($1, $2, 'user', 'own words')", f"m-{u}", f"s-{u}")
        await x("INSERT INTO openclaw_run_state (id, session_id, user_id) VALUES ($1, $2, $3)", f"rs-{u}", f"s-{u}", u)
        await x("INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events) VALUES ($1, $2, $3, '[]')", f"run-{u}", f"s-{u}", f"rs-{u}")
        await x("INSERT INTO proactive_pending (id, user_id, message, trigger_type, expires_at) VALUES ($1, $2, 'x', 'reminder', "
                "'2999-01-01T00:00:00Z')", f"pp-{u}", u)
        await x("INSERT INTO proactive_scheduled (id, user_id, message, send_at, apscheduler_job_id) VALUES ($1, $2, 'bins', "
                "'2999-01-01T00:00:00Z', $3)", f"ps-{u}", u, f"job-{u}")
        await x("INSERT INTO reminders (id, user_id, title) VALUES ($1, $2, 'bins')", f"rem-{u}", u)
        await x("INSERT INTO people (id, user_id, name, visibility) VALUES ($1, $2, 'Private Contact', 'personal')", f"pc-{u}", u)
        await x("INSERT INTO person_gift_ideas (id, person_id, user_id, description) VALUES ($1, $2, $3, 'a kite')",
                f"gift-{u}", f"pc-{u}", u)
        await x("INSERT INTO notes (id, user_id, content) VALUES ($1, $2, 'my note')", f"note-{u}", u)
        await x("INSERT INTO lists (id, user_id, name, visibility) VALUES ($1, $2, 'private list', 'personal')", f"list-{u}", u)
        await x("INSERT INTO list_items (id, list_id, text) VALUES ($1, $2, 'an item')", f"li-{u}", f"list-{u}")
        await x("INSERT INTO events (id, user_id, title, start_date, visibility) VALUES ($1, $2, 'appointment', '2026-01-01', "
                "'personal')", f"ev-{u}", u)
        await x("INSERT INTO event_attendees (id, event_id, user_id, created_at, updated_at) VALUES ($1, $2, $3, 'now', 'now')",
                f"att-{u}", f"ev-{u}", u)
        await x("INSERT INTO push_subscriptions (user_id, endpoint, keys_p256dh, keys_auth) VALUES ($1, $2, 'k', 'a')",
                u, "https://push.invalid/" + u)
        await x("INSERT INTO ui_panel_sessions (panel_id, user_id) VALUES ($1, $2)", f"panel-{u}", u)
    # B's note pointing at A's private contact (0006: a plain REFERENCES people(id), no ON DELETE)
    await x("INSERT INTO notes (id, user_id, content, person_id) VALUES ('note-b-on-a', $1, 'B wrote this', $2)", b, f"pc-{a}")
    # A's household-SHARED objects (kept: item 1 of the review is an owner question), with an item assigned to A
    await x("INSERT INTO lists (id, user_id, name, visibility) VALUES ('list-fam', $1, 'shopping', 'family')", a)
    await x("INSERT INTO list_items (id, list_id, text, assigned_to) VALUES ('li-fam', 'list-fam', 'milk', $1)", a)
    await x("INSERT INTO people (id, user_id, name, visibility) VALUES ('pc-fam', $1, 'Family Friend', 'family')", a)
    await x("INSERT INTO events (id, user_id, title, start_date, visibility) VALUES ('ev-fam', $1, 'picnic', '2026-01-02', 'family')", a)
    # a session A owns that B also spoke in
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-shared', $1, 'A title')", a)
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('sh-a', 's-shared', 'user', 'A speaks')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('sh-b', 's-shared', 'user', 'B speaks', $1)",
            _meta(b))
    # a run A started in B's session, which holds NO turn of A's (only the run-state row links it to A)
    await x("INSERT INTO openclaw_run_state (id, session_id, user_id) VALUES ('rs-a-in-b', $1, $2)", f"s-{b}", a)
    await x("INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events) VALUES ('run-a-in-b', $1, 'rs-a-in-b', '[\"a reply to A\"]')",
            f"s-{b}")


# (a) the happy path: nothing names A afterwards except the kept family rows; B's rows survive; the hand-over satisfies the FK
async def test_the_erase_leaves_nothing_of_the_member_and_everything_of_the_other(world):
    a, b = world.a, world.b
    await _seed(world)
    await mof.forget_user(a)
    left = [(t, r.get("id")) for t, r in await world.naming(a)]
    assert sorted(left) == [("events", "ev-fam"), ("lists", "list-fam"), ("people", "pc-fam")], \
        "only the kept FAMILY containers still carry A's id (an open owner question)"
    assert await world.ids("chat_sessions") == {f"s-{b}", "s-shared"}
    c = await world.raw()
    assert await c.fetchval("SELECT user_id FROM chat_sessions WHERE id = 's-shared'") == b, "handed to B (a users row: FK holds)"
    assert await world.ids("chat_messages") == {f"m-{b}", "sh-b"}
    assert await world.ids("chat_ag_ui_runs") == {f"run-{b}"}, "A's runs go, the one in B's session too"
    for t in ("openclaw_run_state", "proactive_pending", "proactive_scheduled", "reminders", "person_gift_ideas", "list_items",
              "event_attendees"):
        assert all(b in i or "fam" in i for i in await world.ids(t)), t
    assert await c.fetchval("SELECT person_id FROM notes WHERE id = 'note-b-on-a'") is None, "B's note stays, unlinked"
    assert await c.fetchval("SELECT assigned_to FROM list_items WHERE id = 'li-fam'") is None
    assert {r[0] for r in await c.fetch("SELECT user_id FROM push_subscriptions")} == {b}
    assert {r[0] for r in await c.fetch("SELECT user_id FROM ui_panel_sessions")} == {b}


# (b) atomicity: a failure late in the transaction rolls EVERY statement back (they did not commit one by one); the retry finishes
async def test_a_late_failure_rolls_back_every_statement_and_the_retry_finishes(world, monkeypatch):
    a = world.a
    await _seed(world)
    real = mof._lists
    seen = {}

    async def boom(*_a):
        # INSIDE the open erase transaction, read from a SEPARATE connection: the consume is already committed (a fire claiming now
        # sees fired = 1), and the row's DELETE (inside the transaction) is not
        c = await world.raw()
        seen["during"] = await c.fetchval("SELECT fired FROM proactive_scheduled WHERE id = $1", f"ps-{a}")
        raise ConnectionError("store down mid-erase")
    monkeypatch.setattr(mof, "_lists", boom)              # runs AFTER the run ids were collected and the runs / turns deleted
    with pytest.raises(ConnectionError):
        await mof.forget_user(a)
    assert seen == {"during": 1}, "while the erase transaction is open, another connection sees the row, consumed"
    assert "run-a-in-b" in await world.ids("chat_ag_ui_runs"), "the run is still there"
    assert "rs-a-in-b" in await world.ids("openclaw_run_state"), "and so is the evidence that it was A's"
    assert {"sh-a", f"m-{a}"} <= await world.ids("chat_messages"), "the turns are still there"
    assert {f"note-{a}", f"rem-{a}"} <= (await world.ids("notes")) | (await world.ids("reminders")), "the owned rows too"
    c = await world.raw()
    assert await c.fetchval("SELECT user_id FROM chat_sessions WHERE id = 's-shared'") == a, "the hand-over rolled back"
    assert await c.fetchval("SELECT fired FROM proactive_scheduled WHERE id = $1", f"ps-{a}") == 1, \
        "the consume committed BEFORE the transaction (its own connection) and stays; the row's DELETE rolled back with the rest"
    assert await c.fetchval("SELECT fired FROM proactive_scheduled WHERE id = $1", f"ps-{world.b}") == 0, "B's send is untouched"
    monkeypatch.setattr(mof, "_lists", real)
    await mof.forget_user(a)                              # the retry: the already-consumed row does not hold it, and it goes
    assert "run-a-in-b" not in await world.ids("chat_ag_ui_runs")
    assert not await world.ids("openclaw_run_state", "user_id = $1", a)
    assert await world.ids("proactive_scheduled") == {f"ps-{world.b}"}


# (c) a never-migrated table: its statement fails INSIDE the transaction; the savepoint keeps the transaction alive
async def test_a_table_that_was_never_migrated_does_not_abort_the_transaction(world):
    a, b = world.a, world.b
    await _seed(world)
    c = await world.raw()
    await c.execute("DROP TABLE notes")                              # an OWNED table, deleted from mid-transaction
    await mof.forget_user(a)                                         # also crosses every OWNED table this schema never created
    assert await world.ids("chat_ag_ui_runs") == {f"run-{b}"}, "the statements before the missing table committed"
    assert await world.ids("list_items") == {f"li-{b}", "li-fam"}, "and so did the ones after it"
    assert not await world.ids("ui_panel_sessions", "user_id = $1", a, col="panel_id")
    assert await world.ids("ui_panel_sessions", col="panel_id") == {f"panel-{b}"}


# (d) the migrations' foreign keys: no violation in the delete order; the users row is NOT this eraser's, and the kept family rows
#     are why a later DELETE of it would fail
async def test_the_delete_order_satisfies_the_foreign_keys_and_the_users_row_stays(world):
    a = world.a
    await _seed(world)
    await mof.forget_user(a)                                         # raises ForeignKeyViolationError on a parent-first delete
    assert a in await world.ids("users"), "forget_user never deletes the users row"
    c = await world.raw()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await c.execute("DELETE FROM users WHERE id = $1", a)        # the kept FAMILY list / contact / event still reference it
    await c.execute("DELETE FROM lists WHERE id = 'list-fam'")
    await c.execute("DELETE FROM list_items WHERE list_id = 'list-fam'")
    await c.execute("DELETE FROM people WHERE id = 'pc-fam'")
    await c.execute("DELETE FROM events WHERE id = 'ev-fam'")
    await c.execute("DELETE FROM users WHERE id = $1", a)            # nothing else of A's references it once those go


# (e) concurrency smoke: B writes on another connection while A is erased; neither waits on the other, B's writes survive
async def test_another_members_write_runs_beside_the_erase_and_survives(world):
    a, b = world.a, world.b
    await _seed(world)

    async def b_writes():
        c = await world.raw()
        for i in range(20):
            async with c.transaction():
                await c.execute("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ($1, 's-shared', 'user', "
                                "'B again', $2)", f"b-new-{i}", _meta(b))
                await c.execute("INSERT INTO notes (id, user_id, content) VALUES ($1, $2, 'B note')", f"b-note-{i}", b)
            await asyncio.sleep(0)

    await asyncio.wait_for(asyncio.gather(mof.forget_user(a), b_writes()), 30)
    assert {f"b-new-{i}" for i in range(20)} <= await world.ids("chat_messages")
    assert {f"b-note-{i}" for i in range(20)} <= await world.ids("notes")
    c = await world.raw()
    assert await c.fetchval("SELECT user_id FROM chat_sessions WHERE id = 's-shared'") == b


# (f) a session ANOTHER member owns that held only A's turn is emptied: its row stays B's, every run in it goes, its title resets; in B's
#     own session holding B's and A's turns the unattributable run goes and B's run stays; B's session A never spoke in is untouched
async def test_a_session_another_member_owns_that_held_only_the_members_turns_loses_every_run(world):
    a, b = world.a, world.b
    await _seed(world)
    c = await world.raw()
    x = c.execute
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-b-only-a', $1, 'what A asked')", b)
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('oa-1', 's-b-only-a', 'user', 'A asks', $1)",
            _meta(a))
    await x("INSERT INTO openclaw_run_state (id, session_id, user_id) VALUES ('rs-b-x', 's-b-only-a', $1)", b)
    await x("INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events) VALUES ('run-oa-none', 's-b-only-a', 'r-nobody', '[]')")
    await x("INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events) VALUES ('run-oa-b', 's-b-only-a', 'rs-b-x', '[]')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('m-a-at-b', $1, 'user', 'A at B''s panel', $2)",
            f"s-{b}", _meta(a))
    await x("INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events) VALUES ('run-sb-none', $1, 'r-nobody-2', '[]')", f"s-{b}")
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-b-own', $1, 'B only')", b)
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('bo-1', 's-b-own', 'user', 'B alone')")
    await x("INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events) VALUES ('run-bo', 's-b-own', 'r-bo', '[]')")
    await mof.forget_user(a)
    assert tuple(await c.fetchrow("SELECT user_id, title FROM chat_sessions WHERE id = 's-b-only-a'")) == (b, "New Chat"), \
        "the row stays B's"
    assert not await world.ids("chat_messages", "session_id = 's-b-only-a'")
    assert not await world.ids("chat_ag_ui_runs", "session_id = 's-b-only-a'"), "no run of the emptied session survives"
    assert await world.ids("chat_ag_ui_runs", "session_id = $1", f"s-{b}") == {f"run-{b}"}, "B's run stays, the unattributable one goes"
    assert await world.ids("chat_messages", "session_id = $1", f"s-{b}") == {f"m-{b}"}
    assert await world.ids("chat_ag_ui_runs", "session_id = 's-b-own'") == {"run-bo"}, "untouched"
