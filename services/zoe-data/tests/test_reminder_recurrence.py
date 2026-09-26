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
