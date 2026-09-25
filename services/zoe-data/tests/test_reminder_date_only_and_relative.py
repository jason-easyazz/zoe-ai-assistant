"""Reminders created the way Jason actually creates them must be able to fire.

2026-09-25 audit §2.2 — two independent gaps, both reproduced from live rows:

1. `reminder_scan` skipped every reminder with a `due_date` but no `due_time`
   (all five of Jason's active reminders), so nothing had fired since 07-04.
   Now: date-only rows fire at `ZOE_REMINDER_DEFAULT_TIME` (default 09:00 local).
2. The brain's `add_reminder` tool passed the model's literal "tomorrow"
   through `reminder_create` and it was stored verbatim; `build_run_at` then
   hit `ValueError` in `fromisoformat` and returned None — silently, every
   5-minute cycle. Now: `reminder_service.normalize_due_date` resolves relative
   days to ISO at WRITE time (both the API and the direct intent path go
   through `create_reminder_record`), rejects garbage with 422, and the scan
   WARNs once per reminder id for any legacy non-ISO row instead of staying
   silent.

Negative controls are the "old behaviour" assertions inline: the pre-fix
scan SQL filter and the literal stored value, each shown to fail the new
assertions.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import proactive.triggers.reminder_scan as scan
from models import ReminderCreate
from reminder_service import create_reminder_record, normalize_due_date

pytestmark = pytest.mark.ci_safe


# --------------------------------------------------------------------------- #
# Fakes (async-with + await dual-mode cursor, mirrors test_scheduler_reliability)
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class _ScanDb:
    """proactive_scheduled is always empty → nothing is 'already scheduled'."""

    def __init__(self, reminders):
        self.reminders = reminders
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        self.sql.append(sql)

        async def _run():
            if "FROM reminders" in sql:
                return _Cursor(self.reminders)
            return _Cursor([])

        return _Exec(_run)


@pytest.fixture
def _scan_env(monkeypatch):
    scheduled = []

    async def fake_schedule_reminder(*, user_id, message, send_at, item_id):
        scheduled.append({"user_id": user_id, "message": message, "send_at": send_at, "item_id": item_id})

    import proactive.triggers.reminders as reminders_mod

    monkeypatch.setattr(reminders_mod, "schedule_reminder", fake_schedule_reminder)
    monkeypatch.setattr(scan, "_WARNED_BAD_DUE_DATE", set())
    monkeypatch.delenv("ZOE_REMINDER_DEFAULT_TIME", raising=False)
    return scheduled


def _now_local_0800() -> datetime:
    """A 'now' of 08:00 local today, so today-09:00 is in the future and in-window."""
    today = datetime.now(scan._ZOE_TZ).date()
    return datetime(today.year, today.month, today.day, 8, 0, tzinfo=scan._ZOE_TZ).astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# 1. date-only reminders
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_date_only_reminder_is_scheduled_at_default_time(_scan_env):
    now_utc = _now_local_0800()
    today_iso = now_utc.astimezone(scan._ZOE_TZ).date().isoformat()
    row = {"id": "rem-date-only", "user_id": "jason", "title": "see where the foreshore hardware went",
           "due_date": today_iso, "due_time": None, "snoozed_until": None}

    rid = await scan.schedule_due_reminder(_ScanDb([row]), row, now_utc=now_utc)

    assert rid == "rem-date-only"
    assert len(_scan_env) == 1
    fired_local = _scan_env[0]["send_at"].astimezone(scan._ZOE_TZ)
    assert (fired_local.hour, fired_local.minute) == (9, 0)
    assert fired_local.date().isoformat() == today_iso


@pytest.mark.asyncio
async def test_default_time_is_env_tunable(_scan_env, monkeypatch):
    monkeypatch.setenv("ZOE_REMINDER_DEFAULT_TIME", "7:30 AM")
    now_utc = _now_local_0800() - timedelta(hours=2)  # 06:00 local
    today_iso = now_utc.astimezone(scan._ZOE_TZ).date().isoformat()
    row = {"id": "r2", "user_id": "jason", "title": "t", "due_date": today_iso, "due_time": "", "snoozed_until": None}

    assert await scan.schedule_due_reminder(_ScanDb([row]), row, now_utc=now_utc) == "r2"
    fired_local = _scan_env[0]["send_at"].astimezone(scan._ZOE_TZ)
    assert (fired_local.hour, fired_local.minute) == (7, 30)


@pytest.mark.asyncio
async def test_scan_query_selects_date_only_rows_and_schedules_them(_scan_env):
    """The trigger's own SQL must not filter date-only rows out before the
    per-row logic ever sees them (the pre-fix filter was `due_time IS NOT NULL`)."""
    now_utc = _now_local_0800()
    today_iso = now_utc.astimezone(scan._ZOE_TZ).date().isoformat()
    row = {"id": "r3", "user_id": "jason", "title": "t", "due_date": today_iso, "due_time": None, "snoozed_until": None}
    db = _ScanDb([row])

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc if tz is None else now_utc.astimezone(tz)

    import proactive.triggers.reminder_scan as mod
    original = mod.datetime
    mod.datetime = _FrozenNow
    try:
        await scan.ReminderScanTrigger().check(db)
    finally:
        mod.datetime = original

    select_sql = next(s for s in db.sql if "FROM reminders" in s)
    assert "due_date IS NOT NULL" in select_sql
    # Negative control: the OLD filter would have excluded the row outright.
    assert "AND due_time IS NOT NULL\n" not in select_sql
    assert [s["item_id"] for s in _scan_env] == ["r3"]


@pytest.mark.asyncio
async def test_reminder_with_neither_date_nor_time_is_still_skipped(_scan_env):
    row = {"id": "r4", "user_id": "jason", "title": "t", "due_date": None, "due_time": None, "snoozed_until": None}
    assert await scan.schedule_due_reminder(_ScanDb([row]), row, now_utc=_now_local_0800()) is None
    assert _scan_env == []


# --------------------------------------------------------------------------- #
# 2. non-ISO literal in the row: warn ONCE, never silently
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_non_iso_due_date_warns_once_naming_the_reminder(_scan_env, caplog):
    caplog.set_level(logging.WARNING, logger=scan.__name__)
    row = {"id": "rem-van", "user_id": "jason", "title": "do the handover for the van",
           "due_date": "tomorrow", "due_time": "07:00", "snoozed_until": None}
    db = _ScanDb([row])

    assert await scan.schedule_due_reminder(db, row, now_utc=_now_local_0800()) is None
    assert await scan.schedule_due_reminder(db, row, now_utc=_now_local_0800()) is None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "non-ISO due_date" in r.getMessage()]
    assert len(warnings) == 1, "must warn exactly once per reminder id, not every scan cycle"
    assert "rem-van" in warnings[0].getMessage()
    assert "'tomorrow'" in warnings[0].getMessage()
    assert _scan_env == []


# --------------------------------------------------------------------------- #
# 3. write-time normalisation (API + brain tool share create_reminder_record)
# --------------------------------------------------------------------------- #
def test_normalize_due_date_resolves_relative_days_and_keeps_iso():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert normalize_due_date("tomorrow") == tomorrow
    assert normalize_due_date("Tomorrow ") == tomorrow
    assert normalize_due_date("today") == date.today().isoformat()
    assert normalize_due_date("2026-06-15") == "2026-06-15"
    assert normalize_due_date("2026-06-15T09:00:00") == "2026-06-15"
    assert normalize_due_date(None) is None
    assert normalize_due_date("") is None
    nxt = normalize_due_date("next friday")
    assert date.fromisoformat(nxt).weekday() == 4 and date.fromisoformat(nxt) > date.today()


def test_normalize_due_date_rejects_garbage_with_422():
    with pytest.raises(HTTPException) as exc:
        normalize_due_date("whenever the van is ready")
    assert exc.value.status_code == 422
    assert "due_date" in exc.value.detail


class _WriteCursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _WriteDb:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.stored: dict = {}

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if "INSERT INTO reminders" in sql:
            self.stored = {"id": params[0], "user_id": params[1], "title": params[2],
                           "due_date": params[7], "due_time": params[8],
                           "is_active": 1, "acknowledged": 0, "deleted": 0}
        if sql.strip().upper().startswith("SELECT"):
            return _WriteCursor(self.stored)
        return _WriteCursor()

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_create_reminder_record_stores_iso_not_the_literal_tomorrow(monkeypatch):
    async def _allow(*_a, **_k):
        return None

    async def _quiet(*_a, **_k):
        return None

    monkeypatch.setattr("reminder_service.require_feature_access", _allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", _quiet)
    db = _WriteDb()

    reminder = await create_reminder_record(
        ReminderCreate(title="do the handover for the van", due_date="tomorrow", due_time="07:00"),
        user={"user_id": "jason", "role": "admin"},
        db=db,
    )

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert db.stored["due_date"] == tomorrow  # negative control: pre-fix this was "tomorrow"
    assert reminder["due_date"] == tomorrow
    notif_sql, notif_params = next(c for c in db.calls if "INSERT INTO notifications" in c[0])
    assert f'"due_date": "{tomorrow}"' in notif_params[5]
    # And the stored value is what reminder_scan can now fire.
    assert scan._is_iso_date(db.stored["due_date"])
