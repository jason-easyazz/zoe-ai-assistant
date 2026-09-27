"""Spoken recurring reminders must actually recur.

Before this, "remind me to take my pills every weekday at 7am" was stored as a
ONE-OFF: the regex tier bailed on "every", the Gemma NLU schema had no
recurrence field, and `reminder_scan` could only run "time, no date" as daily.
Now recurrence is parsed deterministically (`reminder_recurrence`), stored as an
RRULE with its first occurrence as the anchor date, and the scan schedules each
next occurrence. Negative controls are inline ("without the pattern …").
"""
from __future__ import annotations

import sys
import types
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

import proactive.triggers.reminder_scan as scan
from reminder_recurrence import (
    describe_rrule,
    extract_recurrence,
    first_occurrence_date,
    next_occurrence,
    normalize_recurrence,
    parse_rrule,
)
from reminder_service import normalize_recurrence_fields

pytestmark = pytest.mark.ci_safe

PERTH = ZoneInfo("Australia/Perth")
# Saturday 2026-09-26 16:00 in Perth.
SAT_4PM = datetime(2026, 9, 26, 16, 0, tzinfo=PERTH)


# --------------------------------------------------------------------------- #
# 1. words → RRULE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text, rrule, rest", [
    ("remind me to take my pills every weekday at 7am", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
     "remind me to take my pills at 7am"),
    ("remind me to put the bins out every tuesday at 6pm", "FREQ=WEEKLY;BYDAY=TU",
     "remind me to put the bins out at 6pm"),
    ("remind me to water the plants every second tuesday", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU",
     "remind me to water the plants"),
    ("remind me to pay rent every fortnight on thursday", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TH",
     "remind me to pay rent"),
    ("remind me to call mum on sundays", "FREQ=WEEKLY;BYDAY=SU", "remind me to call mum"),
    ("remind me to walk every saturday and sunday at 8am", "FREQ=WEEKLY;BYDAY=SA,SU",
     "remind me to walk at 8am"),
    ("remind me to check the pool every morning", "FREQ=DAILY",
     "remind me to check the pool in the morning"),
    ("remind me to pay the card on the 3rd of every month", "FREQ=MONTHLY;BYMONTHDAY=3",
     "remind me to pay the card"),
    ("remind me about book club the first monday of every month", "FREQ=MONTHLY;BYDAY=1MO",
     "remind me about book club"),
    ("remind me to review the budget on the last day of every month", "FREQ=MONTHLY;BYMONTHDAY=-1",
     "remind me to review the budget"),
    ("remind me to stretch every 3 days", "FREQ=DAILY;INTERVAL=3", "remind me to stretch"),
    ("remind me to back up daily", "FREQ=DAILY", "remind me to back up"),
    ("remind me to renew the rego every year", "FREQ=YEARLY", "remind me to renew the rego"),
])
def test_extract_recurrence(text, rrule, rest):
    assert extract_recurrence(text) == (rrule, rest)


@pytest.mark.parametrize("text", [
    "remind me to buy the daily paper",           # adjective, not a schedule
    "remind me about the monthly report tomorrow",
    "remind me to call dad at 5",
    "remind me to check the oven",
])
def test_non_recurring_text_is_left_alone(text):
    assert extract_recurrence(text) is None


def test_rrule_subset_is_validated_not_half_honoured():
    assert parse_rrule("RRULE:FREQ=WEEKLY;BYDAY=MO") is not None
    for bad in ("FREQ=HOURLY", "FREQ=DAILY;COUNT=3", "FREQ=WEEKLY;BYDAY=1MO",
                "FREQ=MONTHLY;BYDAY=MO", "FREQ=DAILY;INTERVAL=0", "weekly-ish", ""):
        assert parse_rrule(bad) is None, bad


def test_normalize_recurrence_accepts_phrases_and_rejects_the_rest():
    assert normalize_recurrence("every weekday") == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert normalize_recurrence("freq=daily") == "FREQ=DAILY"
    assert normalize_recurrence("  ") is None
    with pytest.raises(ValueError):
        normalize_recurrence("whenever I feel like it")


def test_describe_rrule_is_speakable():
    assert describe_rrule(parse_rrule("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")) == "every weekday"
    assert describe_rrule(parse_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU")) == "every 2 weeks on Tuesday"
    assert describe_rrule(parse_rrule("FREQ=MONTHLY;BYDAY=-1FR")) == "the last Friday of every month"


# --------------------------------------------------------------------------- #
# 2. next occurrence
# --------------------------------------------------------------------------- #
def _series(rrule: str, anchor: date, n: int, tz=PERTH, hm=(7, 0), after=SAT_4PM):
    rule, out, t = parse_rrule(rrule), [], after
    for _ in range(n):
        t = next_occurrence(rule, anchor, hm[0], hm[1], t, tz)
        out.append(t)
    return out


def test_every_second_tuesday_starts_this_tuesday_then_skips_a_week():
    rule = parse_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU")
    anchor = first_occurrence_date(rule, SAT_4PM.date(), 7, 0, SAT_4PM, PERTH)
    assert anchor == date(2026, 9, 29)
    assert [d.date() for d in _series("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU", anchor, 3)] == [
        date(2026, 9, 29), date(2026, 10, 13), date(2026, 10, 27)]


def test_weekday_rule_skips_the_weekend():
    days = [d.date() for d in _series("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", date(2026, 9, 28), 6)]
    assert days == [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
                    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]


def test_month_end_rules():
    assert [d.date() for d in _series("FREQ=MONTHLY;BYMONTHDAY=-1", date(2027, 1, 31), 2,
                                      after=datetime(2027, 1, 31, 8, 0, tzinfo=PERTH))] == [
        date(2027, 2, 28), date(2027, 3, 31)]
    # The 31st does not exist in November: skipped, never shifted to the 30th.
    assert [d.date() for d in _series("FREQ=MONTHLY;BYMONTHDAY=31", date(2026, 10, 31), 2)] == [
        date(2026, 10, 31), date(2026, 12, 31)]
    assert _series("FREQ=MONTHLY;BYDAY=-1FR", date(2026, 9, 26), 1)[0].date() == date(2026, 10, 30)


def test_local_clock_time_holds_across_a_dst_change():
    sydney = ZoneInfo("Australia/Sydney")  # DST starts 2026-10-04
    after = datetime(2026, 10, 2, 12, 0, tzinfo=sydney)
    series = _series("FREQ=DAILY", date(2026, 10, 1), 4, tz=sydney, hm=(9, 0), after=after)
    assert [(d.hour, d.minute) for d in series] == [(9, 0)] * 4
    assert series[0].utcoffset() != series[-1].utcoffset()


# --------------------------------------------------------------------------- #
# 3. write path
# --------------------------------------------------------------------------- #
_NOW_UTC = SAT_4PM.astimezone(timezone.utc)


def test_write_path_stores_rrule_and_first_occurrence_as_anchor():
    rrule, due = normalize_recurrence_fields("every weekday", None, "07:00", now_utc=_NOW_UTC)
    assert rrule == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert due == "2026-09-28"  # Monday — not today's (Saturday) date


def test_write_path_without_pattern_is_unchanged():
    assert normalize_recurrence_fields(None, "2026-10-01", "07:00", now_utc=_NOW_UTC) == (None, "2026-10-01")


def test_write_path_rejects_unsupported_recurrence():
    with pytest.raises(HTTPException) as exc:
        normalize_recurrence_fields("every blue moon", None, None, now_utc=_NOW_UTC)
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------- #
# 4. scan: schedules each next occurrence, never "past-due forever"
# --------------------------------------------------------------------------- #
class _Cursor:
    async def fetchone(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Db:
    def execute(self, sql, params=()):
        return _Cursor()


@pytest.fixture
def scheduled(monkeypatch):
    out = []

    async def fake_schedule_reminder(*, user_id, message, send_at, item_id):
        out.append(send_at)

    import proactive.triggers.reminders as reminders_mod

    monkeypatch.setattr(reminders_mod, "schedule_reminder", fake_schedule_reminder)
    monkeypatch.setattr(scan, "_ZOE_TZ", PERTH)
    monkeypatch.setattr(scan, "_WARNED_RECURRING_DATE_ONLY", set())
    return out


def _row(**kw):
    row = {"id": "rem-pills", "user_id": "jason", "title": "pills", "due_date": "2026-09-21",
           "due_time": "07:00", "snoozed_until": None, "recurring_pattern": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"}
    row.update(kw)
    return row


@pytest.mark.asyncio
async def test_scan_schedules_next_occurrence_of_a_past_anchor(scheduled):
    # Sunday 2026-09-27 08:00 local → next weekday 07:00 is Monday, inside 25 h.
    now = datetime(2026, 9, 27, 8, 0, tzinfo=PERTH).astimezone(timezone.utc)
    assert await scan.schedule_due_reminder(_Db(), _row(), now_utc=now) == "rem-pills"
    assert scheduled[0].astimezone(PERTH) == datetime(2026, 9, 28, 7, 0, tzinfo=PERTH)

    # Negative control: the same row as a one-off is past-due and never fires again.
    scheduled.clear()
    assert await scan.schedule_due_reminder(_Db(), _row(recurring_pattern=None), now_utc=now) is None
    assert scheduled == []


@pytest.mark.asyncio
async def test_scan_moves_on_after_an_occurrence_fires(scheduled):
    # Monday 07:05, just after that morning's reminder fired → Tuesday 07:00.
    now = datetime(2026, 9, 28, 7, 5, tzinfo=PERTH).astimezone(timezone.utc)
    assert await scan.schedule_due_reminder(_Db(), _row(), now_utc=now) == "rem-pills"
    assert scheduled[0].astimezone(PERTH) == datetime(2026, 9, 29, 7, 0, tzinfo=PERTH)


@pytest.mark.asyncio
async def test_scan_leaves_far_occurrences_for_a_later_cycle(scheduled):
    now = datetime(2026, 9, 26, 8, 0, tzinfo=PERTH).astimezone(timezone.utc)  # Saturday
    row = _row(recurring_pattern="FREQ=WEEKLY;BYDAY=TH")
    assert await scan.schedule_due_reminder(_Db(), row, now_utc=now) is None
    assert scheduled == []


@pytest.mark.asyncio
async def test_legacy_free_text_pattern_keeps_old_behaviour(scheduled):
    now = datetime(2026, 9, 27, 8, 0, tzinfo=PERTH).astimezone(timezone.utc)
    row = _row(due_time=None, recurring_pattern="weekly")
    assert await scan.schedule_due_reminder(_Db(), row, now_utc=now) is None


# --------------------------------------------------------------------------- #
# 5. intent path: recurrence is carried, and never costs a Gemma call
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_spoken_recurring_reminder_carries_rrule_without_llm(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("a recurring reminder with a clock time must not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)
    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("remind me to take my pills every weekday at 7am", user_id="guest")

    assert intent.name == "reminder_create"
    assert intent.slots["title"] == "take my pills"
    assert intent.slots["time"] == "07:00"
    assert intent.slots["recurrence"] == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"


@pytest.mark.asyncio
async def test_recurrence_survives_the_llm_fallback(monkeypatch):
    seen = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(_intent_name, raw):
        seen.append(raw)
        return {"title": "call dad", "date": "2026-09-27", "time": "17:00"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)
    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("remind me to call dad every sunday at 5", user_id="guest")

    assert seen == ["remind me to call dad at 5"]  # the filler never sees "every sunday"
    assert intent.slots["recurrence"] == "FREQ=WEEKLY;BYDAY=SU"


@pytest.mark.asyncio
async def test_direct_execution_stores_rrule_and_speaks_the_schedule(monkeypatch):
    import contextlib

    import database
    import intent_router

    inserts = []

    class _WriteDb:
        async def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("INSERT INTO REMINDERS"):
                inserts.append(tuple(params))

            class _C:
                async def fetchone(self_inner):
                    return {"id": "r1", "title": "take my pills"}
            return _C()

        async def commit(self):
            pass

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield _WriteDb()

    async def allow(*_a, **_k):
        return None

    async def user(_db, uid):
        return {"user_id": uid, "role": "user"}

    monkeypatch.setattr(database, "get_db_ctx", fake_ctx)
    monkeypatch.setattr(intent_router, "_load_direct_execution_user", user)
    monkeypatch.setattr("reminder_service.require_feature_access", allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", allow)

    slots = {"title": "take my pills", "date": "", "time": "07:00",
             "recurrence": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"}
    reply = await intent_router._execute_reminder_create_direct(intent_router.Intent("reminder_create", slots), "jason")

    row = inserts[0]
    assert "recurring" in row and "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" in row
    assert "every weekday" in reply and "07:00" in reply


# --------------------------------------------------------------------------- #
# 6. cross-review findings (Codex, #1708)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rrule, anchor, after, expected", [
    # Next occurrence is ~3 years away — beyond any fixed day window.
    ("FREQ=YEARLY;INTERVAL=3", date(2026, 1, 1), datetime(2026, 1, 1, 8, 0, tzinfo=PERTH), date(2029, 1, 1)),
    # Feb 29 yearly: common years are skipped, not shifted.
    ("FREQ=YEARLY", date(2028, 2, 29), datetime(2028, 3, 1, 8, 0, tzinfo=PERTH), date(2032, 2, 29)),
    ("FREQ=MONTHLY;INTERVAL=52", date(2026, 1, 31), datetime(2026, 1, 31, 8, 0, tzinfo=PERTH), date(2030, 5, 31)),
    ("FREQ=WEEKLY;INTERVAL=52;BYDAY=MO", date(2026, 9, 28), datetime(2026, 9, 28, 8, 0, tzinfo=PERTH), date(2027, 9, 27)),
])
def test_long_interval_rules_keep_recurring(rrule, anchor, after, expected):
    hit = next_occurrence(parse_rrule(rrule), anchor, 7, 0, after, PERTH)
    assert hit is not None and hit.date() == expected


@pytest.mark.parametrize("text, cue", [
    ("remind me to stretch every 53 days", "every 53 days"),
    ("remind me to drink water every hour", "every hour"),
    ("remind me about the bins every third monday", "every third monday"),
    ("remind me to back up hourly", "hourly"),
])
def test_unsupported_recurrence_is_detected(text, cue):
    from reminder_recurrence import find_unsupported_recurrence

    assert extract_recurrence(text) is None
    assert find_unsupported_recurrence(text) == cue


@pytest.mark.parametrize("text", [
    "remind me to thank everyone for each gift",
    "remind me to buy the daily paper",
    "remind me to do the quarterly BAS",
])
def test_ordinary_titles_are_not_mistaken_for_recurrence(text):
    from reminder_recurrence import find_unsupported_recurrence

    assert find_unsupported_recurrence(text) is None


@pytest.mark.asyncio
async def test_unsupported_recurrence_is_refused_not_stored_as_one_off(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("an unsupported recurrence must not reach the slot filler")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)
    import intent_router

    intent = await intent_router.detect_and_extract_intent("remind me to stretch every 53 days", user_id="guest")
    assert intent.slots == {"unsupported_recurrence": "every 53 days"}

    async def no_db(*_a, **_k):
        raise AssertionError("nothing may be written for an unsupported recurrence")

    monkeypatch.setattr(intent_router, "_load_direct_execution_user", no_db)
    reply = await intent_router._execute_reminder_create_direct(intent, "jason")
    assert "every 53 days" in reply and "haven't set it" in reply


def test_chat_skips_the_one_off_form_for_recurring_reminders():
    from routers.chat import _shows_form

    assert _shows_form("reminder_create", {"title": "pills", "time": "07:00"}) is True
    assert _shows_form("reminder_create", {"title": "pills", "recurrence": "FREQ=DAILY"}) is False
    assert _shows_form("reminder_create", {"unsupported_recurrence": "every hour"}) is False
    assert _shows_form("calendar_create", {"recurrence": "FREQ=DAILY"}) is True


# --------------------------------------------------------------------------- #
# 7. review round 2 (#1708: Greptile, Codex, shepherd review)
# --------------------------------------------------------------------------- #
def test_empty_byday_entries_are_rejected_not_dropped():
    # `BYDAY=,` used to parse as "no BYDAY" → weekly on the anchor's weekday.
    for bad in ("FREQ=WEEKLY;BYDAY=,", "FREQ=WEEKLY;BYDAY=MO,,TU", "FREQ=WEEKLY;BYDAY=MO,"):
        assert parse_rrule(bad) is None, bad
    assert parse_rrule("FREQ=WEEKLY;BYDAY=MO,TU") is not None


@pytest.mark.parametrize("text, rrule, rest", [
    # Month-first word order used to drop the weekday: "every month" matched alone
    # and stored a day-of-month rule, leaving "on the first monday" in the title.
    ("remind me every month on the first monday to check the boiler", "FREQ=MONTHLY;BYDAY=1MO",
     "remind me to check the boiler"),
    ("remind me each month on the last friday to pay rent", "FREQ=MONTHLY;BYDAY=-1FR",
     "remind me to pay rent"),
    ("remind me every month on the last day to review the budget", "FREQ=MONTHLY;BYMONTHDAY=-1",
     "remind me to review the budget"),
    # Each day may carry its own "every".
    ("remind me every monday and every friday at 9 to stretch", "FREQ=WEEKLY;BYDAY=MO,FR",
     "remind me at 9 to stretch"),
])
def test_more_spoken_schedules(text, rrule, rest):
    assert extract_recurrence(text) == (rrule, rest)


@pytest.mark.parametrize("text, cue", [
    # A modifier the rule cannot express must refuse, never schedule the excluded day.
    ("remind me to take pills every weekday except friday at 7am", "every weekday except friday"),
    ("remind me every day at 7am until friday to stretch", "every day until friday"),
    ("remind me every day to walk for 2 weeks", "every day for 2 weeks"),
    # A second, unsupported repeat after a supported one is not silently dropped.
    ("remind me every monday to drink water every hour", "every hour"),
])
def test_repeat_modifiers_are_refused_not_half_honoured(text, cue):
    from reminder_recurrence import find_unsupported_recurrence

    assert extract_recurrence(text) is None
    assert find_unsupported_recurrence(text) == cue


@pytest.mark.parametrize("text", [
    "remind me on tues to call the bank",           # singular abbreviation, one-off
    "remind me on weds to call",
    "remind me to water every plant tomorrow morning",
    "remind me to check every single window at 5pm",
])
def test_one_off_phrasing_is_not_recurring_or_refused(text):
    from reminder_recurrence import find_unsupported_recurrence

    assert extract_recurrence(text) is None
    assert find_unsupported_recurrence(text) is None


def test_ambiguous_fall_back_time_never_returns_a_past_instant():
    # Sydney 2026-04-05 03:00 AEDT → 02:00 AEST: 02:30 happens twice. RFC 5545
    # takes the FIRST (15:30Z). At 16:10Z that has passed, so the next fire is
    # the following night — never the already-past 15:30Z (wall-clock compare).
    sydney = ZoneInfo("Australia/Sydney")
    after = datetime(2026, 4, 4, 16, 10, tzinfo=timezone.utc)
    hit = next_occurrence(parse_rrule("FREQ=DAILY"), date(2026, 4, 1), 2, 30, after, sydney)
    assert hit > after
    assert hit.astimezone(timezone.utc) == datetime(2026, 4, 5, 16, 30, tzinfo=timezone.utc)


def test_nonexistent_spring_forward_time_follows_rfc5545():
    # Sydney 2026-10-04 02:00 → 03:00: 02:30 does not exist. RFC 5545 §3.3.5
    # interprets it with the offset BEFORE the gap (→ 03:30 AEDT), so a daily
    # reminder still fires that day rather than being skipped.
    sydney = ZoneInfo("Australia/Sydney")
    after = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    hit = next_occurrence(parse_rrule("FREQ=DAILY"), date(2026, 10, 1), 2, 30, after, sydney)
    assert hit.astimezone(timezone.utc) == datetime(2026, 10, 3, 16, 30, tzinfo=timezone.utc)
    assert hit.astimezone(sydney).hour == 3


@pytest.mark.asyncio
async def test_recurring_reminder_never_falls_back_to_a_one_off_writer(monkeypatch):
    import intent_router

    async def direct_unavailable(_intent, _uid):
        return None

    async def no_mcporter(*_a, **_k):
        raise AssertionError("the MCP writer has no recurrence — it must not be used")

    monkeypatch.setattr(intent_router, "_execute_reminder_create_direct", direct_unavailable)
    monkeypatch.setattr(intent_router, "_run_mcporter", no_mcporter)
    slots = {"title": "take my pills", "time": "07:00", "recurrence": "FREQ=DAILY"}
    reply = await intent_router.execute_intent(intent_router.Intent("reminder_create", slots), "jason")
    assert reply and "haven't" in reply


class _TodayDb:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        rows = self.rows

        class _C:
            async def fetchall(self_inner):
                return rows
        return _C()


@pytest.mark.asyncio
async def test_today_view_keeps_recurring_reminders_after_their_anchor(monkeypatch):
    import routers.reminders as rr

    class _ServerSaturday(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 26)  # the server's own date must NOT be used

    async def allow(*_a, **_k):
        return None

    # Household clock (ZOE_TIMEZONE): Sunday 2026-09-27 00:30 in Perth.
    monkeypatch.setattr(rr, "date", _ServerSaturday)
    monkeypatch.setattr(scan, "_ZOE_TZ", PERTH)
    monkeypatch.setattr(scan, "zoe_now", lambda now_utc=None: datetime(2026, 9, 27, 0, 30, tzinfo=PERTH))
    monkeypatch.setattr(rr, "require_feature_access", allow)
    rows = [
        {"id": "daily", "due_date": "2026-09-21", "due_time": "07:00", "recurring_pattern": "FREQ=DAILY"},
        {"id": "sundays", "due_date": "2026-09-20", "due_time": "08:00", "recurring_pattern": "FREQ=WEEKLY;BYDAY=SU"},
        {"id": "thursdays", "due_date": "2026-09-24", "due_time": "08:00", "recurring_pattern": "FREQ=WEEKLY;BYDAY=TH"},
        {"id": "one-off", "due_date": "2026-09-27", "due_time": "09:00", "recurring_pattern": None},
        {"id": "time-only", "due_date": None, "due_time": "10:00", "recurring_pattern": None},
        {"id": "future-anchor", "due_date": "2026-10-04", "due_time": "08:00", "recurring_pattern": "FREQ=DAILY"},
    ]
    db = _TodayDb(rows)
    out = await rr.list_today_reminders(user={"user_id": "jason"}, db=db)
    assert [r["id"] for r in out["reminders"]] == ["daily", "sundays", "one-off", "time-only"]
    assert "recurring_pattern" in db.sql[0]


@pytest.mark.asyncio
async def test_clearing_a_recurring_reminders_date_re_anchors_it(monkeypatch):
    # A NULL anchor made the scan anchor to "today" every cycle, so a monthly or
    # every-3-days rule fired DAILY. Clearing the date must re-anchor instead.
    import routers.reminders as rr
    from models import ReminderUpdate

    row = {"id": "r1", "user_id": "jason", "title": "t", "due_date": "2026-01-15", "due_time": "09:00",
           "recurring_pattern": "FREQ=MONTHLY", "is_active": 1, "acknowledged": 0, "deleted": 0}
    updates = []

    class _Db:
        async def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("UPDATE"):
                updates.append((sql, list(params)))

            class _C:
                async def fetchone(self_inner):
                    return row
            return _C()

        async def commit(self):
            pass

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(rr, "require_feature_access", noop)
    monkeypatch.setattr(rr, "_cancel_reminder_jobs_safe", noop)
    monkeypatch.setattr(rr, "_reschedule_reminder_due_safe", noop)
    monkeypatch.setattr(rr.broadcaster, "broadcast", noop)

    await rr.update_reminder("r1", ReminderUpdate(due_date=None), user={"user_id": "jason"}, db=_Db())
    sql, params = updates[0]
    anchor = params[0]
    assert "due_date = ?" in sql and anchor is not None
    assert date.fromisoformat(anchor) >= date(2026, 9, 26)  # the next occurrence, never NULL

    # Re-sending the stored (past) anchor from an edit form keeps it — no 422.
    updates.clear()
    await rr.update_reminder("r1", ReminderUpdate(due_date="2026-01-15", title="t2"),
                             user={"user_id": "jason"}, db=_Db())
    assert "due_date" not in updates[0][0]

    # Negative control: a one-off row's date still clears to NULL, as before.
    updates.clear()
    row["recurring_pattern"] = None
    await rr.update_reminder("r1", ReminderUpdate(due_date=None), user={"user_id": "jason"}, db=_Db())
    assert updates[0][1][0] is None


@pytest.mark.asyncio
async def test_acknowledging_a_recurring_reminder_keeps_the_series(monkeypatch):
    # "Complete" on Monday's pills must not end every later weekday: for an RRULE
    # row, acknowledge means done for THIS occurrence and the next is scheduled.
    import routers.reminders as rr

    row = {"id": "r1", "user_id": "jason", "title": "pills", "due_date": "2026-09-21", "due_time": "07:00",
           "recurring_pattern": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "is_active": 1, "acknowledged": 0,
           "deleted": 0, "visibility": "personal"}
    writes, calls = [], []

    class _Db:
        async def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith(("UPDATE", "INSERT")):
                writes.append(" ".join(sql.split()))
                if sql.lstrip().upper().startswith("UPDATE"):
                    calls.append(("update", reminder_id_of(params)))

            class _C:
                async def fetchone(self_inner):
                    return row
            return _C()

        async def commit(self):
            pass

    def reminder_id_of(params):
        return list(params)[-1]

    async def noop(*_a, **_k):
        return None

    async def cancel(rid):
        calls.append(("cancel", rid))

    async def resched(_db, rid):
        calls.append(("reschedule", rid))

    monkeypatch.setattr(rr, "require_feature_access", noop)
    monkeypatch.setattr(rr, "_cancel_reminder_jobs_safe", cancel)
    monkeypatch.setattr(rr, "_reschedule_reminder_due_safe", resched)
    monkeypatch.setattr(rr, "_create_notification", noop)
    monkeypatch.setattr(rr.broadcaster, "broadcast", noop)

    row["snoozed_until"] = "2026-09-28T00:00:00Z"
    await rr.acknowledge_reminder("r1", user={"user_id": "jason"}, db=_Db())
    assert not any("acknowledged = 1" in w for w in writes)
    # The generation bump lands BEFORE the cancel: a scan racing the cancel then
    # either still sees the old unfired job, or schedules with the NEW generation
    # — never a stale-generation job that would self-void and skip an occurrence.
    assert calls == [("update", "r1"), ("cancel", "r1"), ("reschedule", "r1")]
    ack = next(w for w in writes if w.startswith("UPDATE reminders"))
    # A pending snooze must not re-alert the occurrence just marked done, and a
    # job already running must self-void (generation check at fire time).
    assert "snoozed_until = NULL" in ack
    assert "schedule_generation = COALESCE(schedule_generation, 0) + 1" in ack

    # Negative control: a one-off is still acknowledged for good, exactly as before.
    writes.clear(), calls.clear()
    row["recurring_pattern"] = None
    await rr.acknowledge_reminder("r1", user={"user_id": "jason"}, db=_Db())
    assert any("SET acknowledged = 1" in w for w in writes)
    assert calls == [("cancel", "r1"), ("update", "r1")]  # B2 order, unchanged
