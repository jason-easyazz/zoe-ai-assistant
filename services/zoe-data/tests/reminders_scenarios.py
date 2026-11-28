"""Every reminder entry point, run against one recording fake: what it reads, what it WRITES (statement and values), which
scheduled jobs it touches, what it announces, and what it returns.

Used twice: ``python3 tests/reminders_scenarios.py <a zoe-data tree>`` prints the record of THAT tree as JSON (run against an
export of ``main`` to make ``tests/fixtures/reminders_as_on_main.json``), and ``test_reminders_same_as_main.py`` runs the same
scenarios in-process and compares. It imports nothing the one-door change added, so it runs unchanged on ``main``.

Invented people and words only. The clock is frozen; ids are counted in the order they are made (``uuid.uuid4``), so the
record is the same on every run and an id read back is checked against the id written. The store is real (SQLite in memory,
the live columns): every write is applied, and each scenario's record ends with every row it left (``DB.rows``).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

OWNER, OTHER = "member-ova", "member-adu"
RID, WORD = "rem_bar_0001", "womble"
FROZEN = datetime(2026, 6, 10, 4, 0, 0, 123456, tzinfo=timezone.utc)      # 12:00 in a +08:00 household


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN.astimezone(tz) if tz else FROZEN.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return FROZEN.replace(tzinfo=None)


def _mask(value):
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class Row(dict):
    """A row read by column name or by position (as the pool's rows are)."""

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class _Cur:
    """What ``execute`` returns, as the pool's does: awaitable AND an async context manager, with the rows and ``rowcount``."""

    def __init__(self, cur):
        names = [d[0] for d in cur.description] if cur.description else []
        self.rows = [Row(zip(names, r)) for r in cur.fetchall()] if names else []
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


#: The tables a reminder entry point reads or writes, with the columns the live schema gives them (``alembic/versions/0001`` and
#: ``0012``). The store's clock is frozen: a column default stands in for Postgres's ``DEFAULT NOW()`` with a fixed value, and
#: ``NOW()`` in a statement reads ``<now()>``, so every stored value is a real value in the record.
STORE_NOW = "2026-06-01 04:00:00"
DDL = f"""
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member');
CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
    reminder_type TEXT DEFAULT 'one-time', category TEXT DEFAULT 'general', priority TEXT DEFAULT 'normal', due_date TEXT,
    due_time TEXT, recurring_pattern TEXT, is_active INTEGER DEFAULT 1, acknowledged INTEGER DEFAULT 0, snoozed_until TEXT,
    visibility TEXT NOT NULL DEFAULT 'personal', deleted INTEGER DEFAULT 0, created_at TEXT NOT NULL DEFAULT '{STORE_NOW}',
    updated_at TEXT NOT NULL DEFAULT '{STORE_NOW}', schedule_generation INTEGER DEFAULT 0);
CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, type TEXT NOT NULL, title TEXT, message TEXT, data TEXT,
    delivered INTEGER DEFAULT 0, action_taken TEXT, created_at TEXT NOT NULL DEFAULT '{STORE_NOW}');
CREATE TABLE proactive_scheduled (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, message TEXT NOT NULL, trigger_type TEXT DEFAULT 'scheduled',
    send_at TEXT NOT NULL, apscheduler_job_id TEXT, fired INTEGER DEFAULT 0, item_id TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '{STORE_NOW}');
CREATE TABLE open_loops (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, loop_text TEXT NOT NULL, created_at TEXT DEFAULT '{STORE_NOW}',
    resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT);
CREATE TABLE proactive_candidates (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_ref TEXT, expires_at TEXT);
INSERT INTO users (id, name, role) VALUES ('{OWNER}', 'Member', 'user'), ('{OTHER}', 'Member', 'user');
INSERT INTO open_loops (id, user_id, loop_text) VALUES ('loop_bar_0001', '{OWNER}', '{WORD}');
INSERT INTO proactive_candidates (id, user_id, source_ref, expires_at) VALUES ('cand_loop_0001', '{OWNER}', 'open_loops:loop_bar_0001',
    '2099-01-01T00:00:00Z');
"""
_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")      # a stamp the store takes from the REAL clock (CURRENT_TIMESTAMP)


class DB:
    """A real store: one SQLite database in memory holding one reminder row (and the tables around it). Every statement is
    recorded with its values AND applied, as the pool applies it (it commits each statement: ``commit`` is a recorded no-op), so
    reads answer from what earlier writes left and ``rows()`` is what the entry point left behind.

    Replaces a fake that applied nothing and answered every read from the preset row: a change to what a write LEFT in
    ``reminders`` (or to which row a read-back returned) with the same statement and values passed it."""

    def __init__(self, log, **row):
        self.log = log
        self.con = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.con.create_function("NOW", 0, lambda: "<now()>")
        self.con.executescript(DDL)
        self.row = {"id": RID, "user_id": OWNER, "title": WORD, "description": None, "visibility": "personal",
                    "due_date": "2099-06-15", "due_time": "08:00", "recurring_pattern": None, "priority": "medium",
                    "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None, "schedule_generation": 3, **row}
        cols = ", ".join(self.row)
        self.con.execute(f"INSERT INTO reminders ({cols}) VALUES ({', '.join('?' * len(self.row))})", tuple(self.row.values()))

    def execute(self, sql, *params):
        # both shapes the pool takes: ``execute(sql, (a, b))`` and ``execute(sql, a, b)``
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = params[0]
        norm, p = " ".join(sql.split()), list(params)
        self.log.append(["sql", norm, _mask(p)])
        try:
            return _Cur(self.con.execute(sql, tuple(_mask(v) if isinstance(v, datetime) else v for v in p)))
        except sqlite3.Error as exc:     # in the record even when the caller swallows it (a statement this store cannot run)
            self.log.append(["sql_error", type(exc).__name__])
            raise

    async def commit(self):
        self.log.append(["commit"])

    def rows(self) -> dict:
        """Every row of every table an entry point may write, every column (bar a stamp taken from the real clock)."""
        out = {}
        for table in ("reminders", "notifications", "proactive_scheduled", "open_loops", "proactive_candidates"):
            cur = self.con.execute(f"SELECT * FROM {table} ORDER BY id")
            names = [d[0] for d in cur.description]
            out[table] = [{k: _CLOCK.sub("<clock>", v) if isinstance(v, str) and k == "resolved_at" else v
                           for k, v in zip(names, r)} for r in cur.fetchall()]
        return out


_UNDO: list = []


def _set(obj, name, value) -> None:
    """Replace an attribute for one scenario (``_restore`` puts every one back, so a test run leaves nothing behind)."""
    _UNDO.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _restore() -> None:
    while _UNDO:
        obj, name, value = _UNDO.pop()
        setattr(obj, name, value)


def _wire(log):
    """Record every side effect at the lowest seam both trees share. Returns the modules a scenario calls."""
    import mcp_server
    import os_shadow
    import reminder_service
    import write_receipts
    from proactive.triggers import reminder_scan, reminders as trig
    from push import broadcaster
    from routers import reminders as rest

    def rec(name, pick=lambda *a, **k: [list(a), k]):
        async def fake(*a, **k):
            log.append([name, *_mask(pick(*a, **k))])
        return fake

    _set(trig, "cancel_reminder_jobs", rec("cancel_jobs"))
    _set(trig, "cancel_reminder", rec("cancel_job"))
    _set(trig, "schedule_reminder", rec("schedule"))
    # the row a job is scheduled FROM, every field (a stale or wrong row scheduled used to pass as "<row>"), and what is announced
    _set(reminder_scan, "schedule_due_reminder", rec("schedule_due", lambda _db, row, **k: [dict(row), k]))
    _set(broadcaster, "broadcast", rec("broadcast", lambda channel, event, data, **k: [channel, event, data, k]))
    counted = iter(range(1, 10_000))
    _set(uuid, "uuid4", lambda: uuid.UUID(int=next(counted)))
    _set(mcp_server, "_notify_ui", rec("notify_ui"))
    _set(os_shadow, "note_reminder_create", lambda *a, **k: log.append(["shadow", list(a), _mask(k)]))
    _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_mask(a)]))
    for module in (rest, reminder_service, mcp_server):
        if hasattr(module, "require_feature_access"):
            _set(module, "require_feature_access", rec("feature", lambda _db, user, **k: [k, user]))   # WHOSE role is checked
        if hasattr(module, "datetime"):
            _set(module, "datetime", _Clock)
    _set(reminder_scan, "datetime", _Clock)
    import anticipation
    import commitments
    import omi_review_apply
    import panel_review_cards
    import pending_suggestions

    for module in (anticipation, commitments, omi_review_apply, panel_review_cards, pending_suggestions):
        if getattr(module, "datetime", None) is datetime:          # the class (a module that imports ``datetime`` the module keeps it)
            _set(module, "datetime", _Clock)

    async def actor(db, name, args, ctx):
        return {"user_id": (ctx or {}).get("user_id", OWNER), "role": "user"}

    _set(mcp_server, "_resolve_mcp_actor", actor)

    @contextlib.asynccontextmanager
    async def pg():
        yield _DB_OF[0]             # the same store (a fresh preset row here would answer a read from nothing written)

    _set(mcp_server, "_pg_get_db", pg)

    @contextlib.asynccontextmanager
    async def compat():
        yield _DB_OF[0]

    import db_compat

    _set(db_compat, "get_compat_db", compat)     # the pendant review and the panel cards open their own handle: this one
    return rest, mcp_server


_DB_OF: list = [None]


def _user(who):
    return {"user_id": who, "role": "user"}


def scenarios():
    """name -> (row overrides, a coroutine function of (rest, mcp, db))."""
    from models import ReminderCreate, ReminderUpdate, SnoozeBody

    def edit(who=OWNER, **changes):
        return lambda rest, mcp, db: rest.update_reminder(RID, ReminderUpdate(**changes), user=_user(who), db=db)

    def tool(name, who=OWNER, **args):
        return lambda rest, mcp, db: mcp._execute_tool(db, name, {"reminder_id": RID, **args}, {"user_id": who})

    async def spoken(rest, mcp, db):
        import database
        import intent_router

        @contextlib.asynccontextmanager
        async def ctx():
            yield db

        async def user(_db, uid):
            return _user(uid)

        _set(database, "get_db_ctx", ctx)
        _set(intent_router, "_load_direct_execution_user", user)
        return await intent_router.execute_intent(
            intent_router.Intent("reminder_create", {"title": WORD, "date": "2099-06-16", "time": "15:00"}), OWNER)

    async def anticipation_leave(rest, mcp, db):
        """A "yes" to Zoe's offer of a leave-by reminder (``anticipation._do``, kind ``leave``)."""
        import anticipation

        offer = {"kind": "leave", "slots": {"start_date": "2099-06-16", "leave_time": "15:00", "label": WORD}}
        return list(await anticipation._do(db, OWNER, offer, None, FROZEN))

    async def commitment_late(rest, mcp, db):
        """A promise that came due unkept (``commitments._make_reminder``): a reminder a minute out, its own job at +45 s."""
        import commitments

        return await commitments._make_reminder(db, OWNER, WORD, FROZEN)

    async def pendant_review(rest, mcp, db):
        """An approved pendant candidate (``omi_review_apply.write_reminder``), on the handle it opens itself."""
        import omi_review_apply

        candidate = {"id": "cand_bar_0001_womble", "day_local": "2099-06-15",
                     "payload": {"title": WORD, "date": "2099-06-16", "time": "15:00"}}
        return await omi_review_apply.write_reminder(OWNER, candidate, "panel")

    async def panel_card(rest, mcp, db):
        """"Remind me" on an open-loop card (``panel_review_cards.loop_to_reminder``), then the loop is closed."""
        import panel_review_cards

        return await panel_review_cards.loop_to_reminder(OWNER, {"id": "loop_bar_0001", "text": WORD}, "2099-06-16", "15:00")

    async def suggestion(rest, mcp, db):
        """An accepted reminder offer (``pending_suggestions._execute_action`` -> ``_create_suggested_reminder``), its
        after-commit steps run as the accept route runs them once the transaction has committed."""
        import pending_suggestions

        after: list = []
        made = await pending_suggestions._execute_action(
            db, "reminder_create", {"title": WORD, "due_date": "2099-06-16", "due_time": "15:00"}, OWNER, after)
        for later in after:
            await later(db)
        return [made, len(after)]

    weekly = {"recurring_pattern": "FREQ=WEEKLY;BYDAY=MO", "due_date": "2026-06-08"}
    return {
        # the screen's REST routes
        "rest_create": ({}, lambda rest, mcp, db: rest.create_reminder(
            ReminderCreate(title=WORD, due_date="2099-06-16", due_time="15:00"), user=_user(OWNER), db=db)),
        "rest_create_family_recurring": ({}, lambda rest, mcp, db: rest.create_reminder(
            ReminderCreate(title=WORD, due_time="3pm", recurring_pattern="every weekday", visibility="family"), user=_user(OWNER), db=db)),
        "rest_edit_title": ({}, edit(title=WORD + " two")),
        "rest_edit_day_and_time": ({}, edit(due_date="2099-07-01", due_time="9am")),
        "rest_edit_form_resends_the_row": ({}, edit(title=WORD + " two", due_date="2099-06-15", due_time="08:00", is_active=False)),
        "rest_edit_resends_a_past_day": ({"due_date": "2026-06-01"}, edit(title=WORD + " two", due_date="2026-06-01")),
        "rest_edit_nothing": ({}, edit()),
        # an edit of a snoozed reminder re-arms at the snooze (still ahead) or leaves it to the scan (already past): the row the
        # edit READS BACK decides, so these need a store that holds what was written
        "rest_edit_keeps_a_future_snooze": ({"snoozed_until": "2026-06-10T05:00:00Z"}, edit(title=WORD + " two")),
        "rest_edit_with_a_past_snooze": ({"snoozed_until": "2026-06-10T03:00:00Z"}, edit(title=WORD + " two")),
        "rest_edit_family_row_by_another_member": ({"visibility": "family"}, edit(OTHER, title=WORD + " two", visibility="personal")),
        "rest_edit_private_row_by_another_member": ({}, edit(OTHER, title=WORD + " two")),
        "rest_edit_recurring_same_anchor": (weekly, edit(title=WORD + " two", due_date="2026-06-08")),
        "rest_edit_recurring_cleared_day": (weekly, edit(due_date=None)),
        "rest_delete": ({}, lambda rest, mcp, db: rest.delete_reminder(RID, user=_user(OWNER), db=db)),
        "rest_delete_family_row_by_another_member": ({"visibility": "family"},
                                                     lambda rest, mcp, db: rest.delete_reminder(RID, user=_user(OTHER), db=db)),
        "rest_snooze": ({}, lambda rest, mcp, db: rest.snooze_reminder(RID, SnoozeBody(snooze_minutes=10), user=_user(OWNER), db=db)),
        "rest_snooze_a_done_reminder": ({"acknowledged": 1},
                                        lambda rest, mcp, db: rest.snooze_reminder(RID, SnoozeBody(snooze_minutes=10), user=_user(OWNER), db=db)),
        "rest_snooze_family_row_by_another_member": ({"visibility": "family"}, lambda rest, mcp, db: rest.snooze_reminder(
            RID, SnoozeBody(snooze_minutes=10), user=_user(OTHER), db=db)),
        "rest_done": ({}, lambda rest, mcp, db: rest.acknowledge_reminder(RID, user=_user(OWNER), db=db)),
        "rest_done_recurring": (weekly, lambda rest, mcp, db: rest.acknowledge_reminder(RID, user=_user(OWNER), db=db)),
        "rest_done_family_row_by_another_member": ({"visibility": "family"},
                                                   lambda rest, mcp, db: rest.acknowledge_reminder(RID, user=_user(OTHER), db=db)),
        # the brain's MCP tools
        "tool_create": ({}, lambda rest, mcp, db: mcp._execute_tool(
            db, "reminder_create", {"title": WORD, "due_date": "2099-06-16", "due_time": "15:00"}, {"user_id": OWNER})),
        "tool_update": ({}, tool("reminder_update", title=WORD + " two", due_date="tomorrow", due_time="9am", priority="high")),
        "tool_update_no_fields": ({}, tool("reminder_update")),
        "tool_update_another_members_row": ({"visibility": "family"}, tool("reminder_update", OTHER, title=WORD + " two")),
        "tool_delete": ({}, tool("reminder_delete")),
        "tool_delete_another_members_row": ({"visibility": "family"}, tool("reminder_delete", OTHER)),
        "tool_snooze": ({}, tool("reminder_snooze", minutes=30)),
        "tool_snooze_default_minutes_a_done_reminder": ({"acknowledged": 1}, tool("reminder_snooze")),
        "tool_snooze_another_members_row": ({}, tool("reminder_snooze", OTHER, minutes=5)),
        # the spoken path
        "spoken_create": ({}, spoken),
        # the automatic makers, each through its own function (the one that names the door)
        "maker_anticipation_leave": ({}, anticipation_leave),
        "maker_commitment_late": ({}, commitment_late),
        "maker_pendant_review": ({"user_id": OTHER}, pendant_review),      # no earlier row of this candidate: it is made
        # an earlier approval of the same candidate already made it (its description carries the candidate's ref): none is made
        "maker_pendant_review_retry_finds_its_row": ({"description": "From your pendant on 2099-06-15 (you approved it; ref cand_bar)."},
                                                     pendant_review),
        "maker_panel_card": ({}, panel_card),
        "maker_suggestion": ({}, suggestion),
    }


def _isolated() -> None:
    """Refuse to run outside the test configuration: a reminder made or cancelled reaches stores that default under the live
    ``~/.zoe`` (logs, the shadow's files). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_LOG_DIR", "MEMPALACE_DATA_DIR", "ZOE_MEMORY_REJECT_LEDGER"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"reminders scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


def run_one(name: str) -> list:
    """One scenario's record (from reset state)."""
    _isolated()
    row, call = scenarios()[name]
    log: list = []
    rest, mcp = _wire(log)
    db = DB(log, **row)
    _DB_OF[0] = db
    try:
        result = asyncio.run(call(rest, mcp, db))
        log.append(["returned", _mask(json.loads(json.dumps(result, default=str)))])
    except Exception as exc:  # noqa: BLE001 - a refusal is part of the record
        # an HTTP error is recorded with its detail (the door's own words); any other exception by its TYPE only: the
        # interpreter's message for the same fault differs between Python versions (3.10 and 3.12 word UnboundLocalError
        # differently), and it is the fault, not its wording, that main and this branch must share
        detail = _mask(str(exc.detail)) if hasattr(exc, "detail") else type(exc).__name__
        log.append(["raised", getattr(exc, "status_code", None), detail])
    try:
        log.append(["rows", _mask(db.rows())])           # what the entry point LEFT, after it returned or refused
    finally:
        _restore()
        _DB_OF[0] = None
        db.con.close()
    return json.loads(json.dumps(log, default=str))


def run_all() -> dict:
    return {name: run_one(name) for name in scenarios()}


if __name__ == "__main__":
    sys.path[:0] = [sys.argv[1], sys.argv[1].rstrip("/") + "/tests"]
    import conftest  # noqa: F401 - that tree's test configuration: every live store path pinned to a temporary directory
    print(json.dumps(run_all(), indent=1, sort_keys=True))
