"""Every calendar entry point that writes an event, run against a recording store: what it reads, what it WRITES (statement and
values), which hooks it fires, the receipts it records, what it returns, and what the store holds afterwards.

Used twice: ``python3 tests/calendar_scenarios.py <a zoe-data tree>`` prints the record of THAT tree as JSON (run against an export
of ``main`` to make ``tests/fixtures/calendar_as_on_main.json``), and ``test_calendar_same_as_main.py`` runs the same scenarios
in-process and compares. It imports nothing the one-door or the kernel-door change added, so it runs unchanged on ``main``.

WHERE THE CALENDAR LIVES. Postgres tables ``events`` (``user_id`` = whose calendar it is on, ``visibility`` family / personal /
busy), ``event_attendees``, ``event_alarms`` and ``calendars`` (a subscribed feed's sync state). Here: one SQLite file with the
real migration 0052 applied, reached through both shapes the service uses: the pooled ``?`` handle (the screen's REST routes, the
brain's tools, the day review, the spoken quick-add) and a raw asyncpg-shaped connection (``$n``; the spoken resolvers and an
accepted suggestion).

Invented people and words only. The clock is frozen and ids are counted, not random, so the record is the same on every run.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
import types
import uuid
from datetime import date, datetime
from pathlib import Path

ANN, BOB, HOUSE = "ann-0001", "bob-0002", "family-admin"
WORD, DAY = "womble", "2099-06-16"
NOW = "2099-06-01T04:00:00Z"
TODAY = date(2099, 6, 10)
# the store's own clock, frozen: the ``events`` column defaults stand in for Postgres's ``DEFAULT now()`` with this fixed value,
# so every stored stamp is a real value in the record (nothing is masked)
STORE_NOW = "2099-06-01 04:00:00"
CARD_NOW = "2099-06-01T04:00:00.123456+00:00"      # a card's own ``created_at`` (``datetime.now``), frozen the same way
_UNDO: list = []

BASE_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT DEFAULT 'user')",
    """CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT,
           email TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0, updated_at TEXT)""",
    f"""CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, start_date TEXT NOT NULL,
           start_time TEXT, end_date TEXT, end_time TEXT, duration INTEGER, category TEXT DEFAULT 'general',
           location TEXT, all_day INTEGER DEFAULT 0, recurring TEXT, metadata TEXT,
           visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
           created_at TEXT NOT NULL DEFAULT '{STORE_NOW}', updated_at TEXT NOT NULL DEFAULT '{STORE_NOW}',
           FOREIGN KEY (user_id) REFERENCES users(id))""",
)
SEED = f"""
INSERT INTO users (id, name) VALUES ('{ANN}', 'Member A'), ('{BOB}', 'Member B'), ('{HOUSE}', 'Household');
INSERT INTO events (id, user_id, title, start_date, start_time, visibility) VALUES
    ('ev-ann-family', '{ANN}', 'gribble', '{DAY}', '10:00', 'family'),
    ('ev-ann-private', '{ANN}', 'snark', '{DAY}', '11:00', 'personal'),
    ('ev-bob-family', '{BOB}', 'quango', '{DAY}', '12:00', 'family'),
    ('ev-bob-private', '{BOB}', 'zorb', '{DAY}', '13:00', 'personal'),
    ('ev-house', '{HOUSE}', 'plover', '{DAY}', '14:00', 'family');
INSERT INTO events (id, user_id, title, start_date, start_time, visibility, source, read_only) VALUES
    ('ev-ann-feed', '{ANN}', 'fed', '{DAY}', '15:00', 'family', 'ics', 1);
INSERT INTO events (id, user_id, title, start_date, start_time, visibility, rrule) VALUES
    ('ev-ann-weekly', '{ANN}', 'weekly', '{DAY}', '16:00', 'family', 'FREQ=WEEKLY');
"""


def _set(obj, name, value) -> None:
    _UNDO.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _restore() -> None:
    while _UNDO:
        obj, name, value = _UNDO.pop()
        setattr(obj, name, value)


def _on_the_frozen_clock(build):
    """A card stamps ``created_at`` from ``datetime.now`` it imports inside the call, so the clock is frozen for exactly that
    (synchronous) call: ``datetime.datetime`` reads ``CARD_NOW`` while it runs and is put back before it returns."""
    import datetime as dt

    real = dt.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real.fromisoformat(CARD_NOW).astimezone(tz) if tz else real.fromisoformat(CARD_NOW).replace(tzinfo=None)

    def built(*a, **k):
        dt.datetime = Frozen
        try:
            return build(*a, **k)
        finally:
            dt.datetime = real
    return built


def _plainly(value):
    if isinstance(value, dict):
        return {str(k): _plainly(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plainly(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _plain(value):
    return json.loads(json.dumps(_plainly(value), default=str))


def _migrate(root: Path, path: str) -> None:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        for ddl in BASE_DDL:
            conn.exec_driver_sql(ddl)
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("mig_0052", root / "alembic/versions/0052_calendar_data_model.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.upgrade()
    engine.dispose()


class _Cur:
    def __init__(self, cur):
        self._rows = cur.fetchall() if cur.description else []
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _Pooled:
    """The pooled ``?`` handle (``execute(sql, params)`` -> a cursor), recording every statement (reads too) and commit."""

    def __init__(self, con, log) -> None:
        self.con, self.log = con, log

    async def execute(self, sql, params=()):
        self.log.append(["sql", " ".join(sql.split()), _plain(list(params or ()))])
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.log.append(["commit"])
        self.con.commit()


class _Raw:
    """A raw asyncpg-shaped connection (``$n``; ``execute`` answers the command tag), recording the same way."""

    def __init__(self, con, log) -> None:
        self.con, self.log = con, log

    def _run(self, sql, args):
        self.log.append(["pg", " ".join(sql.split()), _plain(list(args))])
        return self.con.execute(re.sub(r"\$(\d+)", r"?\1", sql), tuple(args))

    async def execute(self, sql, *args):
        cur = self._run(sql, args)
        verb = sql.split()[0].upper()
        self.con.commit()                                              # asyncpg outside a transaction: each statement commits
        return f"INSERT 0 {cur.rowcount}" if verb == "INSERT" else f"{verb} {cur.rowcount}"

    async def fetch(self, sql, *args):
        return [dict(r) for r in self._run(sql, args).fetchall()]

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None


class World:
    def __init__(self, root: Path, log: list) -> None:
        import calendar_service
        import intent_router
        import mcp_server
        import omi_review_apply
        import pending_suggestions
        import skybridge_service
        import write_receipts
        from push import broadcaster
        from routers import calendar as rest

        self.rest, self.mcp, self.sky, self.router, self.omi, self.sugg, self.cal = (
            rest, mcp_server, skybridge_service, intent_router, omi_review_apply, pending_suggestions, calendar_service)
        self.log = log
        path = str(Path(tempfile.mkdtemp(prefix="cal")) / "z.db")
        _migrate(root, path)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "<now()>")
        self.con.executescript(SEED)
        self.con.commit()
        self.db, self.pg = _Pooled(self.con, log), _Raw(self.con, log)

        def rec(name, pick=lambda *a, **k: [list(a), k]):
            async def fake(*a, **k):
                log.append([name, *_plain(pick(*a, **k))])
            return fake

        counted = iter(range(1, 10_000))
        _set(uuid, "uuid4", lambda: uuid.UUID(int=next(counted)))                  # ids counted, in the order they are made
        _set(calendar_service, "now_text", lambda: NOW)
        _set(broadcaster, "broadcast", rec("broadcast", lambda channel, event, data, **k: [channel, event, data, k]))
        _set(mcp_server, "_notify_ui", rec("notify_ui"))
        _set(intent_router, "_notify_ui_channel", rec("notify_ui_channel"))
        _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_plain(a)]))
        _set(rest, "require_feature_access", rec("feature", lambda _db, user, **k: [user.get("user_id"), k]))

        async def is_panel_guest(request, user, db, **k):
            return bool((user or {}).get("panel_id"))

        _set(rest.panel_principal, "is_panel_guest", is_panel_guest)

        async def actor(db, name, args, ctx):
            return {"user_id": (ctx or {}).get("user_id", ANN), "role": "user"}

        _set(mcp_server, "_resolve_mcp_actor", actor)
        _set(skybridge_service, "_today", lambda: TODAY)
        _set(skybridge_service, "today_for_zoe_tz", lambda: TODAY)
        _set(intent_router, "_zoe_now", lambda: datetime(2099, 6, 10, 12, 0))
        import card_service

        _set(card_service.CardService, "build_card", _on_the_frozen_clock(card_service.CardService.build_card))

        @contextlib.asynccontextmanager
        async def pooled():
            yield self.db

        import database
        import db_compat

        _set(database, "get_db_ctx", pooled)
        _set(db_compat, "get_compat_db", pooled)

    def rows(self) -> dict:
        out = {}
        for table, order in (("events", "id"), ("event_attendees", "event_id, position, id"), ("event_alarms", "event_id, id")):
            rows = [dict(r) for r in self.con.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            out[table] = [{k: v for k, v in r.items() if v is not None} for r in rows]       # every stored value, ids too
        return out


def _member(uid):
    return {"user_id": uid, "role": "user"}


PANEL = {"user_id": "guest", "role": "guest", "panel_id": "panel-zed"}
REQUEST = types.SimpleNamespace(headers={}, client=None)


def scenarios() -> dict:
    """name -> a coroutine function of the world; its return value is recorded."""
    from models import EventCreate, EventUpdate

    def create(who=None, **fields):
        async def run(w):
            payload = EventCreate(**{"title": WORD, "start_date": DAY, **fields})
            return await w.rest.create_event(payload, user=who or _member(ANN), db=w.db, request=REQUEST)
        return run

    def edit(event_id, who=None, **fields):
        async def run(w):
            return await w.rest.update_event(event_id, EventUpdate(**fields), user=who or _member(ANN), db=w.db, request=REQUEST)
        return run

    def delete(event_id, who=None):
        async def run(w):
            return await w.rest.delete_event(event_id, user=who or _member(ANN), db=w.db, request=REQUEST)
        return run

    def tool(name, who=ANN, **args):
        async def run(w):
            return await w.mcp._execute_tool(w.db, name, args, {"user_id": who})
        return run

    def card(*ids):
        async def events(w):
            found = [dict(r) for r in w.con.execute("SELECT * FROM events WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)] \
                if ids else []
            return {"intent": {"domain": "calendar"},
                    "cards": [{"content": {"source": "calendar_show", "start_date": DAY, "events": found}}]}
        return events

    the_day = card()        # the calendar is showing DAY and lists nothing: the resolver reads that day's events itself

    def said_create(who=ANN, **fields):
        async def run(w):
            intent = w.sky.SkybridgeIntent("calendar", "create_event", **{"start_date": date.fromisoformat(DAY), "title": WORD,
                                                                          **fields})
            return await w.sky._resolve_calendar_create(intent, who, w.pg)
        return run

    def said_move(target, who=ANN, context=the_day):
        async def run(w):
            intent = w.sky.SkybridgeIntent("calendar", "update_time", target_time="09:00", target_text=target)
            return await w.sky._resolve_calendar_update_time(intent, who, w.pg, await context(w))
        return run

    def said_delete(target, who=ANN, context=the_day):
        async def run(w):
            intent = w.sky.SkybridgeIntent("calendar", "delete_event", target_text=target)
            return await w.sky._resolve_calendar_delete_event(intent, who, w.pg, await context(w))
        return run

    def quick_add(who=ANN, **slots):
        async def run(w):
            return await w.router._execute_calendar_create_direct(w.router.Intent("calendar_create", {"title": WORD, **slots}), who)
        return run

    def suggestion(who=ANN, **slots):
        async def run(w):
            return await w.sugg._execute_action(w.pg, "calendar_create", slots, who)
        return run

    def day_review(who=ANN, **payload):
        async def run(w):
            item = {"id": "cand_bar_0001", "kind": "calendar", "day_local": DAY, "payload": {"title": WORD, **payload}}
            return await w.omi.write_calendar_event(who, item, "review")
        return run

    async def ics_import(w):
        import calendar_ical

        guest = {"display_name": "Quib", "role": "req-participant", "partstat": "needs-action", "kind": "individual",
                 "is_organizer": 0, "rsvp": 0}
        parsed = [{"event": {"title": WORD, "start_date": DAY, "all_day": 1}, "attendees": [guest],
                   "alarms": [{"action": "display", "offset_minutes": -10}]}]
        ids = await calendar_ical.store_parsed(w.db, parsed, user_id=ANN)
        await w.db.commit()
        return ids

    return {
        # the screen's REST routes
        "rest_create": create(start_time="15:00", category="general", location="shed"),
        "rest_create_private": create(visibility="personal", all_day=True),
        "rest_create_with_notes_and_guests": create(description="bring the womble", attendees=[{"display_name": "Quib"}]),
        "rest_create_bad_visibility": create(visibility="everyone"),
        "rest_create_by_the_panel": create(PANEL),
        "rest_create_private_by_the_panel": create(PANEL, visibility="personal"),
        "rest_create_by_a_stranger": create({"user_id": "guest", "role": "guest"}),
        "rest_edit_own": edit("ev-ann-private", title=WORD, start_time="09:30"),
        "rest_edit_nothing": edit("ev-ann-private"),
        "rest_edit_guests_only": edit("ev-ann-family", attendees=[{"display_name": "Quib"}]),
        "rest_edit_household_event_by_another_member": edit("ev-ann-family", _member(BOB), title=WORD, visibility="personal"),
        "rest_edit_private_event_by_another_member": edit("ev-ann-private", _member(BOB), title=WORD),
        "rest_edit_household_event_by_the_panel": edit("ev-house", PANEL, title=WORD),
        "rest_edit_members_household_event_by_the_panel": edit("ev-ann-family", PANEL, location="shed"),
        "rest_edit_private_event_by_the_panel": edit("ev-ann-private", PANEL, title=WORD),
        "rest_edit_a_fed_event": edit("ev-ann-feed", title=WORD),
        "rest_edit_a_missing_event": edit("ev-nope", title=WORD),
        "rest_delete_own": delete("ev-ann-private"),
        "rest_delete_household_event_by_another_member": delete("ev-ann-family", _member(BOB)),
        "rest_delete_private_event_by_another_member": delete("ev-ann-private", _member(BOB)),
        "rest_delete_household_event_by_the_panel": delete("ev-ann-family", PANEL),
        "rest_delete_private_event_by_the_panel": delete("ev-ann-private", PANEL),
        "rest_delete_a_fed_event": delete("ev-ann-feed"),
        "rest_delete_by_a_stranger": delete("ev-ann-family", {"user_id": "guest", "role": "guest"}),
        # the brain's tools
        "tool_create": tool("calendar_create_event", title=WORD, start_date=DAY, start_time="15:00", location="shed"),
        "tool_create_a_health_event": tool("calendar_create_event", title="dentist", start_date=DAY, category="health"),
        "tool_update": tool("calendar_update_event", event_id="ev-ann-private", title=WORD, start_time="09:30", all_day=False),
        "tool_update_no_fields": tool("calendar_update_event", event_id="ev-ann-private"),
        "tool_update_another_members_household_event": tool("calendar_update_event", BOB, event_id="ev-ann-family", title=WORD),
        "tool_update_a_fed_event": tool("calendar_update_event", event_id="ev-ann-feed", title=WORD),
        "tool_update_a_repeating_events_time": tool("calendar_update_event", event_id="ev-ann-weekly", start_time="09:30"),
        "tool_delete": tool("calendar_delete_event", event_id="ev-ann-private"),
        "tool_delete_another_members_household_event": tool("calendar_delete_event", BOB, event_id="ev-ann-family"),
        "tool_delete_a_repeating_event": tool("calendar_delete_event", event_id="ev-ann-weekly"),
        # the spoken path
        "spoken_create": said_create(target_time="15:00"),
        "spoken_create_all_day": said_create(all_day=True),
        "spoken_create_a_range": said_create(target_time="09:00", end_time="18:00"),
        "spoken_create_by_a_guest": said_create("guest", target_time="15:00"),
        "spoken_move_own": said_move("snark"),
        "spoken_move_own_from_the_card": said_move("snark", context=card("ev-ann-private", "ev-ann-family")),
        "spoken_move_another_members_household_event": said_move("quango"),
        "spoken_move_a_fed_event": said_move("fed"),
        "spoken_move_a_repeating_event": said_move("weekly"),
        "spoken_move_nothing_matches": said_move("nothing-like-it"),
        "spoken_move_by_a_guest": said_move("snark", "voice-guest"),
        "spoken_delete_own": said_delete("snark"),
        "spoken_delete_another_members_household_event": said_delete("quango"),
        "spoken_delete_the_households_event": said_delete("plover"),
        "spoken_delete_a_card_event_that_is_not_theirs_to_see": said_delete("zorb", context=card("ev-bob-private")),
        "spoken_delete_by_a_guest": said_delete("snark", "guest"),
        "spoken_quick_add": quick_add(date=DAY, time="3pm"),
        "spoken_quick_add_today": quick_add(),
        "spoken_quick_add_a_health_event": quick_add(date=DAY, category="health"),
        "spoken_quick_add_no_title": quick_add(title=""),
        # an accepted suggestion, the pendant day review, and an .ics file import
        "suggestion_accepted": suggestion(title=WORD, start_date=DAY),
        "suggestion_accepted_with_a_description_only": suggestion(description=WORD, start_date=DAY),
        "day_review_event": day_review(date=DAY, time="15:00"),
        "day_review_event_without_a_day": day_review(),
        "ics_import": ics_import,
    }


def _isolated() -> None:
    """Refuse to run outside the test configuration: an event made or changed reaches stores that default under the live
    ``~/.zoe`` (logs, the shadow's files). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_LOG_DIR", "MEMPALACE_DATA_DIR", "ZOE_MEMORY_REJECT_LEDGER"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"calendar scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


def run_all(root: Path | None = None) -> dict:
    _isolated()
    root = Path(root or Path(__file__).resolve().parents[1])
    out = {}
    for name, call in scenarios().items():
        log: list = []
        try:
            world = World(root, log)
            try:
                log.append(["returned", _plain(asyncio.run(call(world)))])
            except Exception as exc:  # noqa: BLE001 - a refusal is part of the record
                # an HTTP error with its own words; any other exception by its TYPE only (the interpreter's wording differs
                # between Python versions, and it is the fault, not its wording, that main and this branch must share)
                log.append(["raised", getattr(exc, "status_code", getattr(exc, "status", None)),
                            str(getattr(exc, "detail", None) or type(exc).__name__)])
            log.append(["rows", _plain(world.rows())])
            world.con.close()
        finally:
            _restore()
        out[name] = log
    return out


if __name__ == "__main__":
    sys.path[:0] = [sys.argv[1], sys.argv[1].rstrip("/") + "/tests"]
    import conftest  # noqa: F401 - that tree's test configuration: every live store path pinned to a temporary directory
    record = run_all(Path(sys.argv[1]))
    # one line per scenario: a change to one shows as one line in a diff
    text = "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(record[k], sort_keys=True)}" for k in sorted(record)) + "\n}\n"
    Path(sys.argv[2]).write_text(text, "utf-8") if len(sys.argv) > 2 else print(text)
