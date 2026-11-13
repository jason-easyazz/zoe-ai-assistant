"""The shared calendar read path (``calendar_read``) and the read-only write rule, end to end on SQLite with 0052 applied.

Gap audit 2026-11-13 (lane D X3/X4, lane A A4): every reader queried ``events`` itself and got the same things wrong.
Pinned here: a multi-day event that overlaps the window from either edge is listed; repeats expand inside the window
(plain word, fortnightly, RRULE, UNTIL / COUNT / EXDATE, an override) with a hard cap, and an unparseable repeat yields its
base date only; cancelled is hidden unless asked; all-day sorts before timed; a zoned event lands on its HOUSEHOLD day; a
provider-owned or read-only event refuses update and delete at the API, the MCP tools and the Skybridge voice resolvers
while a local event still edits; the list route carries ``read_only`` / ``is_recurring`` / ``source`` per item.

All data is invented. Negative controls (run by hand when written): the pre-fix ``routers/calendar.py``, ``mcp_server.py``
and ``skybridge_service.py`` (``git show origin/main:...``) turn the route-overlap, route-fields, MCP-guard and
Skybridge-guard tests red; ``occurrence_dates`` returning ``[anchor]`` turns every expansion test red; ``is_writable``
returning True turns every guard test red.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import calendar_read  # noqa: E402
import calendar_service as cal  # noqa: E402
import mcp_server  # noqa: E402
import routers.calendar as calendar_router  # noqa: E402
import skybridge_service as sky  # noqa: E402
from models import EventUpdate  # noqa: E402
from test_calendar_data_model import A, B, db  # noqa: E402,F401  (the 0052 SQLite fixture)
from test_skybridge_service import FakeDb  # noqa: E402

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _household_zone(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")


async def _add(db, title, start_date, *, user=A, **cols):
    base = {k: cols.pop(k) for k in ("start_time", "end_date", "end_time", "all_day", "recurring", "category", "visibility")
            if k in cols}
    rec = await cal.create_event_record(db, user_id=user, title=title, start_date=start_date, extra=cols or None, **base)
    await db.commit()
    return rec["id"]


async def _window(db, start, end, **kw):
    return await calendar_read.events_in_window(db, A, start, end, **kw)


def _days(occ, title):
    return [o["date"] for o in occ if o["title"] == title]


# -- overlap ------------------------------------------------------------------------------------------------------------------

async def test_a_multi_day_event_is_listed_from_either_edge_of_the_window(db):
    await _add(db, "Beach trip", "2026-12-01", end_date="2026-12-05", all_day=True)
    for start, end in (("2026-12-03", "2026-12-03"), ("2026-11-28", "2026-12-01"), ("2026-12-05", "2026-12-08")):
        assert _days(await _window(db, start, end), "Beach trip") == ["2026-12-01"], (start, end)
    assert await _window(db, "2026-12-06", "2026-12-09") == []                  # negative: after it ended
    assert await _window(db, "2026-11-25", "2026-11-30") == []                  # negative: before it began


async def test_the_list_route_shows_a_trip_that_began_before_the_window(db):
    await _add(db, "Conference", "2026-12-01", end_date="2026-12-04")
    events = (await calendar_router.list_events(start_date="2026-12-03", end_date="2026-12-03", category=None,
                                                include_cancelled=False, user={"user_id": A, "role": "user"}, db=db))["events"]
    assert [e["title"] for e in events] == ["Conference"]


# -- repeats ------------------------------------------------------------------------------------------------------------------

async def test_repeats_expand_inside_the_window(db):
    await _add(db, "Bins", "2026-11-02", recurring="weekly")                      # a Monday
    await _add(db, "Pay day", "2026-11-05", recurring="fortnightly")
    await _add(db, "Swim", "2026-10-06", rrule="FREQ=WEEKLY;BYDAY=TU,TH;WKST=MO")
    await _add(db, "Choir", "2026-11-04", rrule="RRULE:FREQ=WEEKLY;UNTIL=20261118T000000Z")
    await _add(db, "Course", "2026-11-03", rrule="FREQ=WEEKLY;COUNT=3")
    await _add(db, "Yoga", "2026-11-07", rrule="FREQ=WEEKLY", exdates='["2026-11-21"]')
    occ = await _window(db, "2026-11-16", "2026-11-30")
    assert _days(occ, "Bins") == ["2026-11-16", "2026-11-23", "2026-11-30"]
    assert _days(occ, "Pay day") == ["2026-11-19"]
    assert _days(occ, "Swim") == ["2026-11-17", "2026-11-19", "2026-11-24", "2026-11-26"]
    assert _days(occ, "Choir") == ["2026-11-18"]                                 # UNTIL is the last one
    assert _days(occ, "Course") == ["2026-11-17"]                                # 3rd and last of COUNT=3
    assert _days(occ, "Yoga") == ["2026-11-28"]                                  # EXDATE skipped the 21st
    assert all(o["is_recurring"] for o in occ)


async def test_daily_expansion_is_capped_and_an_unparseable_repeat_is_its_base_date_only(db):
    await _add(db, "Vitamins", "2026-01-01", recurring="daily")
    await _add(db, "Odd", "2026-11-20", recurring="every other blue moon")
    occ = await _window(db, "2026-11-01", "2028-01-01")
    # the window is clamped to MAX_WINDOW_DAYS (400) and the expansion to the cap (one slot may go to the padding day)
    assert calendar_read.MAX_OCCURRENCES_PER_EVENT - 1 <= len(_days(occ, "Vitamins")) <= calendar_read.MAX_OCCURRENCES_PER_EVENT
    assert _days(occ, "Odd") == ["2026-11-20"]
    assert _days(await _window(db, "2026-11-21", "2026-11-30"), "Odd") == []


async def test_an_override_replaces_its_masters_instance(db):
    master = await _add(db, "Standup", "2026-11-02", rrule="FREQ=WEEKLY", start_time="09:00")
    await _add(db, "Standup (moved)", "2026-11-10", start_time="11:00", series_id=master, recurrence_id="2026-11-09")
    occ = await _window(db, "2026-11-09", "2026-11-10")
    assert [(o["title"], o["date"]) for o in occ] == [("Standup (moved)", "2026-11-10")]


# -- status, ordering, zone ---------------------------------------------------------------------------------------------------

async def test_cancelled_is_hidden_unless_asked(db):
    await _add(db, "Recital", "2026-12-02", status="cancelled")
    await _add(db, "Recital rehearsal", "2026-12-02")
    assert [o["title"] for o in await _window(db, "2026-12-02", "2026-12-02")] == ["Recital rehearsal"]
    shown = await _window(db, "2026-12-02", "2026-12-02", include_cancelled=True)
    assert sorted(o["title"] for o in shown) == ["Recital", "Recital rehearsal"]


async def test_all_day_sorts_before_timed_and_timed_sorts_by_time(db):
    await _add(db, "Late", "2026-12-03", start_time="18:00")
    await _add(db, "Early", "2026-12-03", start_time="07:30")
    await _add(db, "Holiday", "2026-12-03", all_day=True)
    assert [o["title"] for o in await _window(db, "2026-12-03", "2026-12-03")] == ["Holiday", "Early", "Late"]


async def test_a_zoned_event_lands_on_its_household_day(db):
    # 01:00 in Sydney (UTC+11 in December) is 22:00 the evening before in Perth (UTC+8)
    await _add(db, "Call east", "2026-12-10", start_time="01:00", timezone="Australia/Sydney")
    occ = await _window(db, "2026-12-09", "2026-12-09")
    assert [(o["title"], o["start_time"]) for o in occ] == [("Call east", "22:00")]
    assert await _window(db, "2026-12-10", "2026-12-10") == []


# -- the write guard ----------------------------------------------------------------------------------------------------------

def test_is_writable_rule():
    assert calendar_read.is_writable({"source": "local", "read_only": 0})
    assert calendar_read.is_writable({"source": "ics", "read_only": 0})
    assert not calendar_read.is_writable({"source": "local", "read_only": 1})
    assert not calendar_read.is_writable({"source": "google", "read_only": 0})
    assert not calendar_read.is_writable({"source": "local", "calendar_writable": 0})
    assert "Google Calendar" in calendar_read.refusal({"source": "google"})


async def test_the_api_refuses_a_provider_event_and_still_edits_a_local_one(db):
    synced = await _add(db, "Team sync", "2026-12-04", source="google", external_uid="u1@example.com")
    local = await _add(db, "Piano", "2026-12-04")
    user = {"user_id": A, "role": "user"}
    for call in (calendar_router.update_event(synced, EventUpdate(title="edited"), user=user, db=db),
                 calendar_router.delete_event(synced, user=user, db=db)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409 and "Google Calendar" in exc.value.detail
    assert (await calendar_router.update_event(local, EventUpdate(title="Piano lesson"), user=user, db=db))["title"] == "Piano lesson"


async def test_the_mcp_tools_refuse_a_read_only_event_and_still_edit_a_local_one(db, monkeypatch):
    async def actor(_db, _name, _args, _ctx):
        return {"user_id": A, "role": "user"}

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(mcp_server, "_notify_ui", quiet)
    synced = await _add(db, "Shift", "2026-12-05", source="caldav", read_only=1)
    local = await _add(db, "Haircut", "2026-12-05", start_time="10:00", timezone="Australia/Perth")
    for name, args in (("calendar_update_event", {"event_id": synced, "title": "x"}),
                       ("calendar_delete_event", {"event_id": synced})):
        out = await mcp_server._execute_tool(db, name, args)
        assert out.get("status") == "read_only" and "change it there" in out["error"]
    assert (await mcp_server._execute_tool(db, "calendar_update_event", {"event_id": local, "start_time": "11:00"}))["status"] == "updated"
    cur = await db.execute("SELECT start_at FROM events WHERE id = ?", (local,))
    assert (await cur.fetchone())[0] == "2026-12-05T03:00:00Z"                    # the instant moved with the wall clock
    assert (await mcp_server._execute_tool(db, "calendar_delete_event", {"event_id": local}))["status"] == "deleted"
    cur = await db.execute("SELECT deleted, deleted_at FROM events WHERE id IN (?, ?) ORDER BY title", (local, synced))
    rows = [tuple(r) for r in await cur.fetchall()]
    assert rows[0][0] == 1 and rows[0][1] and rows[1][0] == 0                    # Haircut tombstoned, Shift untouched


async def test_the_mcp_list_tools_expand_and_label(db, monkeypatch):
    async def actor(_db, _name, _args, _ctx):
        return {"user_id": A, "role": "user"}

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    await _add(db, "Netball", "2026-11-04", recurring="weekly", source="google")
    out = await mcp_server._execute_tool(db, "calendar_list_events", {"start_date": "2026-11-10", "end_date": "2026-11-20"})
    assert [(e["start_date"], e["read_only"], e["source"]) for e in out["events"]] == [
        ("2026-11-11", True, "Google Calendar"), ("2026-11-18", True, "Google Calendar")]


def _sky_event(**extra):
    return {"id": "ev-sync", "user_id": "family-admin", "title": "Dentist", "start_date": "2026-06-11", "start_time": "15:00",
            "end_time": "15:30", "visibility": "family", "deleted": False, **extra}


async def test_skybridge_voice_move_and_delete_refuse_a_provider_event(monkeypatch):
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: date(2026, 6, 11))
    for utterance in ("change my dentist to 9am", "delete Dentist from my calendar"):
        event = _sky_event(source="microsoft")
        db = FakeDb(events=[event])
        context = {"intent": {"domain": "calendar"},
                   "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-11", "events": [dict(event)]}}]}
        result = await sky.resolve_skybridge_request(utterance, "family-admin", context=context, db=db)
        assert result["actions"] == [] and "Outlook" in result["spoken_summary"], utterance
        assert not [sql for sql, *_ in db.executed if "UPDATE events" in str(sql)]
        assert event["start_time"] == "15:00" and event["deleted"] is False
    local = _sky_event()
    db = FakeDb(events=[local])
    context = {"intent": {"domain": "calendar"},
               "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-11", "events": [dict(local)]}}]}
    result = await sky.resolve_skybridge_request("delete Dentist from my calendar", "family-admin", context=context, db=db)
    assert result["actions"][0]["type"] == "deleted" and local["deleted"] is True


# -- the route's per-item fields ----------------------------------------------------------------------------------------------

async def test_the_route_carries_read_only_is_recurring_and_source(db):
    await _add(db, "Gym", "2026-11-02", recurring="weekly")
    await _add(db, "Board meeting", "2026-11-10", source="google", read_only=1)
    events = (await calendar_router.list_events(start_date="2026-11-09", end_date="2026-11-10", category=None,
                                                include_cancelled=False, user={"user_id": A, "role": "user"}, db=db))["events"]
    got = {e["title"]: e for e in events}
    assert (got["Gym"]["start_date"], got["Gym"]["occurrence_date"], got["Gym"]["is_recurring"], got["Gym"]["read_only"],
            got["Gym"]["source"], got["Gym"]["source_label"]) == ("2026-11-09", "2026-11-09", True, False, "local", "Zoe")
    assert (got["Board meeting"]["read_only"], got["Board meeting"]["is_recurring"],
            got["Board meeting"]["source_label"]) == (True, False, "Google Calendar")


async def test_another_members_busy_event_keeps_its_projection(db):
    await _add(db, "Private thing", "2026-11-11", user=B, visibility="busy")
    events = (await calendar_router.list_events(start_date="2026-11-11", end_date="2026-11-11", category=None,
                                                include_cancelled=False, user={"user_id": A, "role": "user"}, db=db))["events"]
    assert [(e["title"], e.get("busy_only"), "source_label" in e) for e in events] == [("Busy", True, False)]


# -- review round 1 (PR 2447): overrides by original date, RDATE, write scoping, non-writable calendars ------------------------

async def test_an_occurrence_moved_out_of_the_window_leaves_its_old_date(db):
    master = await _add(db, "Swim club", "2026-11-02", rrule="FREQ=WEEKLY")
    await _add(db, "Swim club (moved)", "2026-12-20", series_id=master, recurrence_id="2026-11-16")
    occ = await _window(db, "2026-11-15", "2026-11-17")
    assert occ == []                                         # the master's 16th is replaced; the override is outside


async def test_an_occurrence_moved_into_the_window_appears_once(db):
    master = await _add(db, "Tutor", "2026-11-02", rrule="FREQ=WEEKLY")
    await _add(db, "Tutor (moved)", "2026-11-26", series_id=master, recurrence_id="2026-11-09")
    occ = await _window(db, "2026-11-25", "2026-11-27")
    assert [(o["title"], o["date"]) for o in occ] == [("Tutor (moved)", "2026-11-26")]


async def test_an_occurrence_moved_within_the_window_appears_once(db):
    master = await _add(db, "Band", "2026-11-02", rrule="FREQ=WEEKLY")
    await _add(db, "Band (moved)", "2026-11-18", series_id=master, recurrence_id="2026-11-16")
    occ = await _window(db, "2026-11-15", "2026-11-19")
    assert [(o["title"], o["date"]) for o in occ] == [("Band (moved)", "2026-11-18")]


async def test_an_rdate_only_series_expands_even_when_its_base_date_is_long_past(db):
    await _add(db, "Recital", "2026-01-10", rdates='["2026-11-20", "2026-11-27", "2027-03-01"]')
    occ = await _window(db, "2026-11-15", "2026-11-30")
    assert _days(occ, "Recital") == ["2026-11-20", "2026-11-27"] and all(o["is_recurring"] for o in occ)


async def test_exdate_wins_over_rdate(db):
    await _add(db, "Market", "2026-11-01", rrule="FREQ=WEEKLY", rdates='["2026-11-18"]', exdates='["2026-11-18", "2026-11-22"]')
    assert _days(await _window(db, "2026-11-16", "2026-11-23"), "Market") == []


class _PgOverSqlite:
    """The raw-asyncpg surface the Skybridge resolvers use ($n placeholders, ``fetch``, a status string), over the SQLite
    fixture, so the scoping is proven by real SQL rather than a fake that ignores WHERE clauses."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _q(sql, args):
        import re

        order = [int(n) - 1 for n in re.findall(r"\$(\d+)", sql)]
        return re.sub(r"\$\d+", "?", sql), [args[i] for i in order]

    async def fetch(self, sql, *args):
        q, p = self._q(sql, args)
        return await (await self.db.execute(q, p)).fetchall()

    async def execute(self, sql, *args):
        q, p = self._q(sql, args)
        cur = await self.db.execute(q, p)
        return f"UPDATE {cur.rowcount}"

    async def commit(self):
        await self.db.commit()


async def _row(db, event_id):
    cur = await db.execute("SELECT title, start_time, deleted FROM events WHERE id = ?", (event_id,))
    return tuple(await cur.fetchone())


def _sky_context(event_id, title):
    return {"intent": {"domain": "calendar"},
            "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-11",
                                   "events": [{"id": event_id, "title": title, "start_date": "2026-06-11",
                                               "start_time": "15:00"}]}}]}


async def test_another_members_private_event_id_is_not_found_everywhere(db, monkeypatch):
    async def actor(_db, _name, _args, _ctx):
        return {"user_id": A, "role": "user"}

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: date(2026, 6, 11))
    secret = await _add(db, "Dentist", "2026-06-11", user=B, visibility="personal", start_time="15:00", source="google")
    user = {"user_id": A, "role": "user"}
    for call in (calendar_router.update_event(secret, EventUpdate(title="x"), user=user, db=db),
                 calendar_router.delete_event(secret, user=user, db=db)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404 and "Google" not in exc.value.detail
    for name, args in (("calendar_update_event", {"event_id": secret, "title": "x"}), ("calendar_delete_event", {"event_id": secret})):
        out = await mcp_server._execute_tool(db, name, args)
        assert "not found" in out["error"] and "Google" not in out["error"]
    for utterance in ("change my dentist to 9am", "delete Dentist from my calendar"):
        result = await sky.resolve_skybridge_request(utterance, A, context=_sky_context(secret, "Dentist"), db=_PgOverSqlite(db))
        assert result["actions"] == [] and "could not find" in result["spoken_summary"], utterance
        assert "Google" not in str(result)
    assert await _row(db, secret) == ("Dentist", "15:00", 0)


async def test_an_event_in_a_non_writable_calendar_is_refused_and_a_writable_one_edits(db, monkeypatch):
    async def actor(_db, _name, _args, _ctx):
        return {"user_id": A, "role": "user"}

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(mcp_server, "_notify_ui", quiet)
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: date(2026, 6, 11))
    await db.execute("INSERT INTO calendars (id, user_id, name, writable, created_at, updated_at) VALUES "
                     "('cal-locked', ?, 'School term', 0, 'x', 'x'), ('cal-open', ?, 'Family', 1, 'x', 'x')", (A, A))
    locked = await _add(db, "Exam", "2026-06-11", start_time="15:00", calendar_id="cal-locked")
    user = {"user_id": A, "role": "user"}
    for call in (calendar_router.update_event(locked, EventUpdate(title="x"), user=user, db=db),
                 calendar_router.delete_event(locked, user=user, db=db)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409 and "School term" in exc.value.detail
    for name, args in (("calendar_update_event", {"event_id": locked, "title": "x"}), ("calendar_delete_event", {"event_id": locked})):
        assert (await mcp_server._execute_tool(db, name, args)).get("status") == "read_only"
    for utterance in ("change my exam to 9am", "delete Exam from my calendar"):
        result = await sky.resolve_skybridge_request(utterance, A, context=_sky_context(locked, "Exam"), db=_PgOverSqlite(db))
        assert result["actions"] == [] and "read-only calendar" in result["spoken_summary"], utterance
    assert await _row(db, locked) == ("Exam", "15:00", 0)
    await db.execute("UPDATE calendars SET writable = 1 WHERE id = 'cal-locked'")
    await db.commit()
    assert (await calendar_router.update_event(locked, EventUpdate(title="Exam (room 4)"), user=user, db=db))["title"] == "Exam (room 4)"
    result = await sky.resolve_skybridge_request("delete Exam from my calendar", A, context=_sky_context(locked, "Exam (room 4)"),
                                                 db=_PgOverSqlite(db))
    assert result["actions"][0]["type"] == "deleted" and (await _row(db, locked))[2] == 1
