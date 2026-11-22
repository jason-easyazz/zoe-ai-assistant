"""The calendar has ONE door: ``calendar_service.py``. Structural, over EVERY module of the service (tests and migrations aside).

WHERE THE CALENDAR LIVES. ``events`` (one row per event; ``user_id`` is whose calendar it is on), ``event_attendees``,
``event_alarms``, and ``calendars`` (a subscribed feed and its sync state). Nothing leaves the box: the only outside calendar is a
read-only ``.ics`` subscription that is FETCHED (``calendar_feed.py``); no code writes an event to a calendar elsewhere.

Anything a person does to an event (the screen, the brain's tools, the spoken paths, an accepted suggestion, the day review, an
``.ics`` import) writes through ``calendar_service.py`` and nowhere else. Three modules write these tables without anyone acting,
and are named here by their PATH, each with its reason: ``calendar_feed.py`` reconciles a subscribed feed (the system's own, like
the alarm ring engine; it alone may call ``connected_accounts.upsert_row`` on ``events`` / ``calendars``, a write this scan
cannot read because the table is an argument); ``calendar_forget.py`` and ``memory_forget_cascade.py`` are the erasers.

The scan reads an f-string whole (``x`` standing where a value is interpolated), allows any schema name and Postgres' ``ONLY``,
and is not fooled by ``alarm_events`` / ``struggle_events`` (other tables whose names END in ``events``).

Negative controls run on the real tree (then restored): ``UPDATE events SET title = ?`` planted in ``routers/calendar.py`` ->
``test_only_the_door_writes_the_event_tables`` fails naming it; the raw ``INSERT INTO events`` put back in
``pending_suggestions.py`` -> the same test fails naming that file; ``upsert_row(db, "events", ...)`` planted in
``skybridge_service.py`` -> ``test_only_the_feed_upserts_calendar_rows`` fails naming it. The planted cases below keep the scans
themselves honest.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]

#: an optional schema before the table: an unquoted identifier (letters, digits, ``_``, ``$``; not starting with a digit) or a quoted one
_SCHEMA = r"(?:(?:[A-Za-z_][A-Za-z0-9_$]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])\s*\.\s*)?"
TABLES = ("events", "event_attendees", "event_alarms", "calendars")
EVENT_WRITE = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?" + _SCHEMA
                         + r"[\"`\[]?(?:" + "|".join(TABLES) + r")\b", re.I)
DOOR = "calendar_service.py"
UPSERTS = ("upsert_row", "upsert_by_external_id")
FEED, UPSERT_HOME = "calendar_feed.py", "connected_accounts.py"       # the feed, and the module that defines the two helpers
#: by RELATIVE PATH (never by file name: ``routers/calendar.py`` is scanned), each with why nobody is acting
NOT_A_PERSON = {
    "calendar_feed.py": "reconciles a subscribed read-only feed: the system's own",
    "calendar_forget.py": "the eraser for a member's calendar",
    "memory_forget_cascade.py": "the eraser for a forgotten person's attendee rows",
}
#: ... and WHERE in each of the three, by function, with the exact number of write statements: an exempt module is not a blank
#: cheque (review: the scan skipped these modules whole, so a new writer added inside one passed)
NOT_A_PERSON_WRITERS = {
    ("calendar_feed.py", "_ensure_calendar"): 1, ("calendar_feed.py", "_link_members"): 1, ("calendar_feed.py", "_mark_success"): 1,
    ("calendar_feed.py", "_sync_once"): 1, ("calendar_feed.py", "_write_items"): 1,
    ("calendar_forget.py", "forget_user"): 7, ("calendar_forget.py", "<module>"): 1,       # (the module's docstring names one)
    ("memory_forget_cascade.py", "_run"): 2,
}


def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in ROOT.rglob("*.py") if not skip.intersection(p.relative_to(ROOT).parts))


def _texts(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield "".join(part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "x" for part in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def event_writes(source: str) -> list:
    return [" ".join(text.split())[:80] for text in _texts(ast.parse(source)) if EVENT_WRITE.search(text)]


def feed_upserts(source: str) -> list:
    """Calls of the feed's row writers (``connected_accounts.upsert_row`` / ``upsert_by_external_id``, by any attribute path or
    plain name) whose table is a calendar table, or is not a literal."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in UPSERTS:
            table = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "table"), None)
            if not (isinstance(table, ast.Constant) and table.value not in TABLES):
                out.append(table.value if isinstance(table, ast.Constant) else "<not a literal>")
    return out


def test_only_the_door_writes_the_event_tables():
    modules = _modules()
    rel = {p.relative_to(ROOT).as_posix(): p for p in modules}
    assert len(modules) > 100 and {"routers/calendar.py", "mcp_server.py", "skybridge_service.py", "pending_suggestions.py",
                                   "intent_router.py", "omi_review_apply.py", "calendar_ical.py", DOOR, *NOT_A_PERSON} <= set(rel)
    offenders = {name: found for name, p in rel.items()
                 if name != DOOR and name not in NOT_A_PERSON and (found := event_writes(p.read_text("utf-8")))}
    assert not offenders, offenders
    assert len(event_writes(rel[DOOR].read_text("utf-8"))) >= 14            # the door itself holds the writes (the scan sees them)
    for name in NOT_A_PERSON:                                              # each named exception does write (none is a stale entry)
        assert event_writes(rel[name].read_text("utf-8")), name


def test_the_three_exempt_modules_write_only_in_their_named_functions():
    """Module, function and exact count, as the memory rows' scan does. Control run: a second
    ``await db.execute("DELETE FROM events WHERE id = ?", (x,))`` planted in ``calendar_feed._mark_success`` fails this (count
    2, not 1), and one planted in a new function of ``calendar_forget.py`` fails it naming that function; both passed the
    module-wide exemption."""
    import door_callers

    found = {(name, fn): n for name in NOT_A_PERSON
             for fn, n in door_callers.sql_writes_by_function((ROOT / name).read_text("utf-8"), EVENT_WRITE).items()}
    assert found == NOT_A_PERSON_WRITERS
    assert {name for name, _fn in NOT_A_PERSON_WRITERS} == set(NOT_A_PERSON)


def test_only_the_feed_upserts_calendar_rows():
    offenders = {p.relative_to(ROOT).as_posix(): found for p in _modules()
                 if p.relative_to(ROOT).as_posix() not in (FEED, UPSERT_HOME) and (found := feed_upserts(p.read_text("utf-8")))}
    assert not offenders, offenders
    assert "events" in feed_upserts((ROOT / FEED).read_text("utf-8"))
    # the helpers' own module only passes its caller's table on (it names no calendar table itself)
    assert set(feed_upserts((ROOT / UPSERT_HOME).read_text("utf-8"))) == {"<not a literal>"}


def test_a_planted_event_write_is_caught():
    for sql in ("UPDATE events SET title = ?", "INSERT INTO event_attendees (id) VALUES (?)", "DELETE FROM event_alarms WHERE event_id = ?",
                'UPDATE "public"."events" SET x = ?', "UPDATE public1.events SET x = ?", "DELETE FROM ONLY events WHERE id = ?",
                "update only app_2$x.event_alarms set x = ?", "insert or replace into events (id) values (1)",
                "UPDATE calendars SET name = ?"):
        assert event_writes(f"async def f(db):\n    await db.execute('{sql}')\n"), sql
    for sql in ('f"UPDATE {schema}.events SET x = ?"', 'f"DELETE FROM {schema}.event_attendees WHERE {col} = ?"',
                "f\"UPDATE events SET {', '.join(sets)} WHERE id = ?\""):
        assert event_writes(f"async def f(db, schema, col, sets):\n    await db.execute({sql})\n"), sql
    # other tables whose names end in ``events``, a longer name, and a read are not calendar writes
    assert not event_writes("async def f(db):\n    await db.execute('INSERT INTO alarm_events (id) VALUES (?)')\n"
                            "    await db.execute('UPDATE struggle_events SET x = 1')\n    await db.execute('SELECT * FROM events WHERE id = ?')\n"
                            "    await db.execute('UPDATE events_archive SET x = 1')\n")
    assert feed_upserts("async def f(db, ca, t):\n    await ca.upsert_row(db, 'events', match={}, values={})\n"
                        "    await upsert_row(db, table='calendars', match={}, values={})\n    await ca.upsert_row(db, t, match={}, values={})\n"
                        ) == ["events", "calendars", "<not a literal>"]
    assert feed_upserts("async def f(db, ca):\n    await ca.upsert_by_external_id(db, 'events', match={}, values={})\n") == ["events"]
    assert not feed_upserts("async def f(db, ca):\n    await ca.upsert_row(db, 'source_contacts', match={}, values={})\n")
