"""The calendar readers that moved onto ``calendar_read.events_in_window`` (gap audit 2026-11-13, calendar feed step 4) and the
read-only ``subscription`` source (step 3), end to end on SQLite with 0052 applied. All data is invented.

Readers: the morning check-in's day context, the proactivity selector's events ahead, the pendant day review's duplicate check
and the anticipation night pass (its offer behaviour is pinned in ``test_anticipation.py``). Each keeps its own scope (the
member's OWN events, starting on the day asked). What changes, one test each: a cancelled event is gone, a repeat is seen on a
later occurrence, an EXDATE / override replaces the series' instance, and a zoned event is seen at its household day and time.
The common case is unchanged.

Negative controls (run by hand when written): the pre-change ``morning_checkin.py`` / ``selector.py`` / ``omi_day_review.py`` /
``anticipation.py`` (``git show origin/main:...``) turn the matching reader's cases red in every behaviour test except the
common case; taking ``subscription`` out of ``calendar_read.PROVIDER_SOURCES`` turns every subscription test red.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import anticipation as ant  # noqa: E402
import calendar_read  # noqa: E402
import calendar_service as cal  # noqa: E402
import mcp_server  # noqa: E402
import omi_day_review as dr  # noqa: E402
import routers.calendar as calendar_router  # noqa: E402
import skybridge_service as sky  # noqa: E402
from models import EventUpdate  # noqa: E402
from proactive import selector as sel  # noqa: E402
from proactive.triggers.morning_checkin import _today_events  # noqa: E402
from test_calendar_data_model import A, B, db  # noqa: E402,F401  (the 0052 SQLite fixture)
from test_calendar_read import _add  # noqa: E402
from test_skybridge_service import FakeDb  # noqa: E402

pytestmark = pytest.mark.ci_safe

PERTH = ZoneInfo("Australia/Perth")
DAY = "2026-12-10"                                     # a Thursday
EVE = datetime(2026, 12, 9, 20, 0, tzinfo=PERTH)       # the evening before
READERS = ("anticipation", "morning", "selector", "omi")


@pytest.fixture(autouse=True)
def _household_zone(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")


async def _seen(reader, db, titles) -> dict:
    """``{title: household HH:MM}`` of the member's events starting on DAY, as this reader sees them. The day review answers
    yes/no per title, so its times are ``"?"``."""
    if reader == "anticipation":
        return {e.title: e.start_time for e in await ant._events(db, A, EVE) if e.start_date == DAY}
    if reader == "morning":
        return {e["title"]: e["start"] for e in await _today_events(db, A, DAY)}
    if reader == "selector":
        return {e["title"]: e["start"].strftime("%H:%M") for e in await sel._events_ahead(db, A, EVE)
                if e["start"].date().isoformat() == DAY}
    out = {}
    for t in titles:
        cand = dr.Candidate("calendar_event", {"title": t, "date": DAY, "time": ""}, [], [], 0.5, "not_sure", "k")
        if await dr._on_calendar(db, A, cand):
            out[t] = "?"
    return out


def _times(reader, want: dict) -> dict:
    return {t: ("?" if reader == "omi" else hhmm) for t, hhmm in want.items()}


@pytest.mark.parametrize("reader", READERS)
async def test_the_common_case_is_unchanged(db, reader):
    await _add(db, "Piano lesson", DAY, start_time="16:00")
    await _add(db, "Bike service", "2026-12-11", start_time="09:00")
    await _add(db, "Choir practice", DAY, start_time="18:00", user=B)               # another member's own event
    assert await _seen(reader, db, ["Piano lesson", "Bike service", "Choir practice"]) == _times(reader, {"Piano lesson": "16:00"})


@pytest.mark.parametrize("reader", READERS)
async def test_a_cancelled_event_is_gone(db, reader):
    await _add(db, "School recital", DAY, start_time="17:00", status="cancelled")
    assert await _seen(reader, db, ["School recital"]) == {}


@pytest.mark.parametrize("reader", READERS)
async def test_a_repeat_is_seen_on_a_later_occurrence(db, reader):
    await _add(db, "Swim squad", "2026-11-12", start_time="17:00", recurring="weekly")          # four Thursdays ago
    await _add(db, "Bins night", "2026-11-19", start_time="19:00", rrule="FREQ=WEEKLY;INTERVAL=2")  # off-week on DAY
    assert await _seen(reader, db, ["Swim squad", "Bins night"]) == _times(reader, {"Swim squad": "17:00"})


@pytest.mark.parametrize("reader", READERS)
async def test_an_exdate_and_an_override_replace_the_series_instance(db, reader):
    await _add(db, "Tennis club", "2026-11-12", start_time="16:00", rrule="FREQ=WEEKLY", exdates=f'["{DAY}"]')
    master = await _add(db, "Tutor", "2026-11-12", start_time="15:00", rrule="FREQ=WEEKLY")
    await _add(db, "Tutor moved", DAY, start_time="18:00", series_id=master, recurrence_id=DAY)
    assert await _seen(reader, db, ["Tennis club", "Tutor", "Tutor moved"]) == _times(reader, {"Tutor moved": "18:00"})


@pytest.mark.parametrize("reader", READERS)
async def test_a_zoned_event_is_seen_at_its_household_day_and_time(db, reader):
    # 01:00 on the 11th in Sydney (UTC+11 in December) is 22:00 on the 10th in Perth (UTC+8)
    await _add(db, "Call east", "2026-12-11", start_time="01:00", timezone="Australia/Sydney")
    assert await _seen(reader, db, ["Call east"]) == _times(reader, {"Call east": "22:00"})


# -- the read-only subscription source ----------------------------------------------------------------------------------------

def test_subscription_is_a_provider_source_with_a_plain_label():
    row = {"source": "subscription", "read_only": 0}
    assert "subscription" in cal.SOURCES and not calendar_read.is_writable(row)
    assert calendar_read.refusal(row) == "That one comes from a subscribed calendar; change it there."
    assert calendar_read.is_writable({"source": "ics", "read_only": 0})                # an imported file stays editable


async def test_the_api_refuses_a_repeating_subscription_event_and_still_edits_a_local_one(db):
    fed = await _add(db, "Club fixture", "2026-12-04", source="subscription", rrule="FREQ=WEEKLY")
    local = await _add(db, "Piano", "2026-12-04")
    user = {"user_id": A, "role": "user"}
    for call in (calendar_router.update_event(fed, EventUpdate(title="edited"), user=user, db=db),
                 calendar_router.delete_event(fed, user=user, db=db)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409 and "a subscribed calendar" in exc.value.detail
    assert (await calendar_router.update_event(local, EventUpdate(title="Piano lesson"), user=user, db=db))["title"] == "Piano lesson"


async def test_the_mcp_tools_refuse_a_repeating_subscription_event_with_the_provider_line(db, monkeypatch):
    async def actor(_db, _name, _args, _ctx):
        return {"user_id": A, "role": "user"}

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(mcp_server, "_notify_ui", quiet)
    fed = await _add(db, "Club fixture", "2026-12-05", start_time="10:00", source="subscription", rrule="FREQ=WEEKLY")
    local = await _add(db, "Haircut", "2026-12-05", start_time="10:00")
    for name, args in (("calendar_update_event", {"event_id": fed, "start_time": "11:00"}),
                       ("calendar_delete_event", {"event_id": fed})):
        out = await mcp_server._execute_tool(db, name, args)
        assert out.get("status") == "read_only" and "a subscribed calendar" in out["error"], name   # not the repeating line
    assert (await mcp_server._execute_tool(db, "calendar_update_event", {"event_id": local, "title": "Trim"}))["status"] == "updated"
    assert (await mcp_server._execute_tool(db, "calendar_delete_event", {"event_id": local}))["status"] == "deleted"


def _sky_event(**extra):
    return {"id": "ev-fed", "user_id": "family-admin", "title": "Dentist", "start_date": "2026-06-11", "start_time": "15:00",
            "end_time": "15:30", "visibility": "family", "deleted": False, **extra}


async def test_skybridge_move_and_delete_refuse_a_repeating_subscription_event(monkeypatch):
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: date(2026, 6, 11))

    def context(event):
        return {"intent": {"domain": "calendar"},
                "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-11", "events": [dict(event)]}}]}

    for utterance in ("change my dentist to 9am", "delete Dentist from my calendar"):
        event = _sky_event(source="subscription", rrule="FREQ=WEEKLY")
        db = FakeDb(events=[event])
        result = await sky.resolve_skybridge_request(utterance, "family-admin", context=context(event), db=db)
        assert result["actions"] == [] and "a subscribed calendar" in result["spoken_summary"], utterance
        assert "repeating" not in result["spoken_summary"]
        assert event["start_time"] == "15:00" and event["deleted"] is False
    local = _sky_event()
    db = FakeDb(events=[local])
    result = await sky.resolve_skybridge_request("delete Dentist from my calendar", "family-admin", context=context(local), db=db)
    assert result["actions"][0]["type"] == "deleted" and local["deleted"] is True
