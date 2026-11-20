"""Every reminder entry point, run against one recording fake: what it reads, what it WRITES (statement and values), which
scheduled jobs it touches, what it announces, and what it returns.

Used twice: ``python3 tests/reminders_scenarios.py <a zoe-data tree>`` prints the record of THAT tree as JSON (run against an
export of ``main`` to make ``tests/fixtures/reminders_as_on_main.json``), and ``test_reminders_same_as_main.py`` runs the same
scenarios in-process and compares. It imports nothing the one-door change added, so it runs unchanged on ``main``.

Invented people and words only. The clock is frozen; ids made at random are masked.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
from datetime import datetime, timezone

OWNER, OTHER = "member-ova", "member-adu"
RID, WORD = "rem_bar_0001", "womble"
FROZEN = datetime(2026, 6, 10, 4, 0, 0, 123456, tzinfo=timezone.utc)      # 12:00 in a +08:00 household
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN.astimezone(tz) if tz else FROZEN.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return FROZEN.replace(tzinfo=None)


def _mask(value):
    if isinstance(value, str):
        return _UUID.sub("<id>", value)
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class _Cur:
    def __init__(self, rows):
        self.rows = rows

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


class DB:
    """One reminder row. A read answers by the ownership filter in its own WHERE; nothing is applied (the record is the point)."""

    def __init__(self, log, **row):
        self.log = log
        self.row = {"id": RID, "user_id": OWNER, "title": WORD, "description": None, "visibility": "personal",
                    "due_date": "2099-06-15", "due_time": "08:00", "recurring_pattern": None, "priority": "medium",
                    "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None, "schedule_generation": 3, **row}

    def execute(self, sql, params=()):
        norm, p = " ".join(sql.split()), list(params)
        self.log.append(["sql", norm, _mask(p)])
        if not norm.upper().startswith("SELECT"):
            return _Cur([])
        if "FROM users" in norm:
            return _Cur([{"id": p[0], "role": "user", "name": "Member"}])
        if "FROM reminders" not in norm:
            return _Cur([])
        where = norm.split("WHERE", 1)[1] if "WHERE" in norm else ""
        if "visibility = 'family'" in where:
            seen = self.row["visibility"] == "family" or self.row["user_id"] in p
        elif "user_id" in where:
            seen = self.row["user_id"] in p
        else:
            seen = True
        return _Cur([dict(self.row)] if seen else [])

    async def commit(self):
        self.log.append(["commit"])


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
    _set(reminder_scan, "schedule_due_reminder", rec("schedule_due", lambda _db, row, **k: [dict(row).get("id") and "<row>", sorted(k)]))
    _set(broadcaster, "broadcast", rec("broadcast", lambda channel, event, data, **k: [channel, event, k]))
    _set(mcp_server, "_notify_ui", rec("notify_ui"))
    _set(os_shadow, "note_reminder_create", lambda *a, **k: log.append(["shadow", list(a), _mask(k)]))
    _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_mask(a)]))
    for module in (rest, reminder_service, mcp_server):
        if hasattr(module, "require_feature_access"):
            _set(module, "require_feature_access", rec("feature", lambda _db, _user, **k: [k]))
        if hasattr(module, "datetime"):
            _set(module, "datetime", _Clock)
    _set(reminder_scan, "datetime", _Clock)

    async def actor(db, name, args, ctx):
        return {"user_id": (ctx or {}).get("user_id", OWNER), "role": "user"}

    _set(mcp_server, "_resolve_mcp_actor", actor)

    @contextlib.asynccontextmanager
    async def pg():
        yield DB(log)

    _set(mcp_server, "_pg_get_db", pg)
    return rest, mcp_server


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
    }


def run_all() -> dict:
    out = {}
    for name, (row, call) in scenarios().items():
        log: list = []
        rest, mcp = _wire(log)
        db = DB(log, **row)
        try:
            result = asyncio.run(call(rest, mcp, db))
            log.append(["returned", _mask(json.loads(json.dumps(result, default=str)))])
        except Exception as exc:  # noqa: BLE001 - a refusal is part of the record
            # an HTTP error is recorded with its detail (the door's own words); any other exception by its TYPE only: the
            # interpreter's message for the same fault differs between Python versions (3.10 and 3.12 word UnboundLocalError
            # differently), and it is the fault, not its wording, that main and this branch must share
            detail = _mask(str(exc.detail)) if hasattr(exc, "detail") else type(exc).__name__
            log.append(["raised", getattr(exc, "status_code", None), detail])
        finally:
            _restore()
        out[name] = json.loads(json.dumps(log, default=str))
    return out


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    print(json.dumps(run_all(), indent=1, sort_keys=True))
