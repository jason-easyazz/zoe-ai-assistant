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
    # Greptile P2 (security): id + problem class only — never the private title
    # or the raw stored text in the persisted app log.
    assert "tomorrow" not in caplog.text
    assert "handover" not in caplog.text
    assert _scan_env == []


# --------------------------------------------------------------------------- #
# 3. write-time normalisation (API + brain tool share create_reminder_record)
# --------------------------------------------------------------------------- #
def test_normalize_due_date_resolves_relative_days_and_keeps_iso():
    """Expectations come from the HOUSEHOLD clock (`reminder_scan.zoe_now`), the
    same helper the resolver uses — never `date.today()`, which is the CI
    runner's date: at 16:11 UTC a GitHub runner is on 09-25 while Perth is
    already on 09-26, and the old server-date expectation went red (#1686)."""
    household_today = scan.zoe_now().date()
    tomorrow = (household_today + timedelta(days=1)).isoformat()
    assert normalize_due_date("tomorrow") == tomorrow
    assert normalize_due_date("Tomorrow ") == tomorrow
    assert normalize_due_date("today") == household_today.isoformat()
    assert normalize_due_date("2026-06-15") == "2026-06-15"
    assert normalize_due_date("2026-06-15T09:00:00") == "2026-06-15"
    assert normalize_due_date(None) is None
    assert normalize_due_date("") is None
    nxt = normalize_due_date("next friday")
    assert date.fromisoformat(nxt).weekday() == 4 and date.fromisoformat(nxt) > household_today


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

    tomorrow = (scan.zoe_now().date() + timedelta(days=1)).isoformat()  # household clock, not the runner's
    assert db.stored["due_date"] == tomorrow  # negative control: pre-fix this was "tomorrow"
    assert reminder["due_date"] == tomorrow
    notif_sql, notif_params = next(c for c in db.calls if "INSERT INTO notifications" in c[0])
    assert f'"due_date": "{tomorrow}"' in notif_params[5]
    # And the stored value is what reminder_scan can now fire.
    assert scan._is_iso_date(db.stored["due_date"])


# --------------------------------------------------------------------------- #
# Codex P2s on #1686
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("phrase", ["June 31", "31 june", "2026-06-31", "2026-06-31T09:00", "feb 30 2026"])
def test_recognised_phrase_that_is_not_a_real_date_is_422_not_stored(phrase):
    """`_parse_date` recognises the PHRASE ("June 31" → "2026-06-31") but not the
    calendar; the resolved value must be re-validated or the scan can only ever
    warn about the stored row. Negative control: pre-fix `normalize_due_date("June 31")`
    returned "2026-06-31"."""
    with pytest.raises(HTTPException) as exc:
        normalize_due_date(phrase)
    assert exc.value.status_code == 422


def test_real_calendar_phrase_still_resolves():
    assert normalize_due_date("June 30") == f"{scan.zoe_now().date().year}-06-30"


@pytest.mark.parametrize("bad", ["25:00", "09:99", "13:00 PM", "0:60", "24:00"])
def test_parse_due_time_rejects_out_of_range(bad):
    """No silent `% 24` wrap: '25:00' is a config error, not 01:00, and '09:99'
    used to reach datetime() and raise on every scan cycle."""
    assert scan._parse_due_time(bad) is None


@pytest.mark.parametrize("good, hm", [("00:00", (0, 0)), ("23:59", (23, 59)), ("12:30 PM", (12, 30)), ("12:05 AM", (0, 5))])
def test_parse_due_time_accepts_range_edges(good, hm):
    assert scan._parse_due_time(good) == hm


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["25:00", "09:99"])
async def test_bad_default_time_falls_back_to_0900_with_one_warning(_scan_env, monkeypatch, caplog, bad):
    caplog.set_level(logging.WARNING, logger=scan.__name__)
    monkeypatch.setenv("ZOE_REMINDER_DEFAULT_TIME", bad)
    monkeypatch.setattr(scan, "_WARNED_BAD_DEFAULT_TIME", set())
    now_utc = _now_local_0800()
    today_iso = now_utc.astimezone(scan._ZOE_TZ).date().isoformat()
    rows = [
        {"id": f"r-{i}", "user_id": "jason", "title": "t", "due_date": today_iso, "due_time": None, "snoozed_until": None}
        for i in range(2)
    ]

    for row in rows:
        assert await scan.schedule_due_reminder(_ScanDb(rows), row, now_utc=now_utc) == row["id"]

    assert len(_scan_env) == 2, "both date-only rows scheduled — the bad value must not block them"
    for fired in _scan_env:
        local = fired["send_at"].astimezone(scan._ZOE_TZ)
        assert (local.hour, local.minute) == (9, 0)  # control: pre-fix '25:00' → 01:00, '09:99' → raise
    warnings = [r for r in caplog.records if "ZOE_REMINDER_DEFAULT_TIME" in r.getMessage()]
    assert len(warnings) == 1 and bad in warnings[0].getMessage()


# --------------------------------------------------------------------------- #
# Greptile threads on #1686
# --------------------------------------------------------------------------- #
class _ScanDbFiredToday(_ScanDb):
    """proactive_scheduled reports a job for the reminder already FIRED today."""

    def execute(self, sql, params=()):
        self.sql.append(sql)

        async def _run():
            if "FROM reminders" in sql:
                return _Cursor(self.reminders)
            if "fired = 1" in sql:
                return _Cursor([(1,)])
            return _Cursor([])

        return _Exec(_run)


def _now_local(hour: int, minute: int = 0) -> datetime:
    today = datetime.now(scan._ZOE_TZ).date()
    return datetime(today.year, today.month, today.day, hour, minute, tzinfo=scan._ZOE_TZ).astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_same_day_date_only_reminder_after_default_time_fires_shortly(_scan_env, monkeypatch, caplog):
    """P1: 'remind me today to call mum' said at 15:00 → 09:00 is gone; fire in
    _SAME_DAY_GRACE_MIN minutes, not never. Negative control: pre-fix this
    returned None (past-due skip) and _scan_env stayed empty."""
    caplog.set_level(logging.INFO, logger=scan.__name__)
    monkeypatch.setattr(scan, "_SAME_DAY_FALLBACK_LOGGED", set())
    now_utc = _now_local(15, 0)
    today_iso = scan.zoe_now(now_utc).date().isoformat()
    row = {"id": "rem-today", "user_id": "jason", "title": "call mum", "due_date": today_iso, "due_time": None, "snoozed_until": None}
    db = _ScanDb([row])

    assert await scan.schedule_due_reminder(db, row, now_utc=now_utc) == "rem-today"
    assert await scan.schedule_due_reminder(db, row, now_utc=now_utc) == "rem-today"  # not yet fired → still schedules

    assert len(_scan_env) == 2
    assert _scan_env[0]["send_at"] == now_utc + timedelta(minutes=scan._SAME_DAY_GRACE_MIN)
    infos = [r for r in caplog.records if "same-day fallback" in r.getMessage()]
    assert len(infos) == 1 and "rem-today" in infos[0].getMessage()
    assert "call mum" not in infos[0].getMessage()


@pytest.mark.asyncio
async def test_same_day_fallback_does_not_refire_once_fired_today(_scan_env):
    """The other branch: a job for this reminder already fired since local
    midnight → the fallback must NOT re-arm on the next 5-minute scan."""
    now_utc = _now_local(15, 0)
    today_iso = scan.zoe_now(now_utc).date().isoformat()
    row = {"id": "rem-today", "user_id": "jason", "title": "call mum", "due_date": today_iso, "due_time": None, "snoozed_until": None}

    assert await scan.schedule_due_reminder(_ScanDbFiredToday([row]), row, now_utc=now_utc) is None
    assert _scan_env == []


@pytest.mark.asyncio
async def test_explicit_past_due_time_today_is_still_skipped(_scan_env):
    """The fallback is for date-ONLY rows; a user-named time that has passed
    is not silently moved."""
    now_utc = _now_local(15, 0)
    today_iso = scan.zoe_now(now_utc).date().isoformat()
    row = {"id": "rem-explicit", "user_id": "jason", "title": "t", "due_date": today_iso, "due_time": "09:00", "snoozed_until": None}

    assert await scan.schedule_due_reminder(_ScanDb([row]), row, now_utc=now_utc) is None
    assert _scan_env == []


@pytest.mark.asyncio
async def test_same_day_fallback_not_applied_to_a_past_date(_scan_env):
    now_utc = _now_local(15, 0)
    yesterday = (scan.zoe_now(now_utc).date() - timedelta(days=1)).isoformat()
    row = {"id": "rem-old", "user_id": "jason", "title": "t", "due_date": yesterday, "due_time": None, "snoozed_until": None}

    assert await scan.schedule_due_reminder(_ScanDb([row]), row, now_utc=now_utc) is None
    assert _scan_env == []


def test_relative_dates_resolve_in_zoe_timezone_not_server_date(monkeypatch):
    """P1: 23:30 UTC is already the NEXT day for a +08:00 household. "today" must
    be the household's today. Control: with a UTC household the same instant
    resolves to the UTC date, proving the tz (not the server clock) decides."""
    from zoneinfo import ZoneInfo

    frozen = datetime(2026, 9, 25, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(scan, "_ZOE_TZ", ZoneInfo("Etc/GMT-8"))  # POSIX sign: Etc/GMT-8 == UTC+08:00
    assert normalize_due_date("today", now_utc=frozen) == "2026-09-26"
    assert normalize_due_date("tomorrow", now_utc=frozen) == "2026-09-27"
    # Household today is Saturday 09-26, so "saturday" is NEXT Saturday (the
    # weekday grammar never means today) — the UTC control below gets 09-26.
    assert normalize_due_date("saturday", now_utc=frozen) == "2026-10-03"
    assert normalize_due_date("june 3", now_utc=frozen) == "2026-06-03"

    monkeypatch.setattr(scan, "_ZOE_TZ", ZoneInfo("UTC"))
    assert normalize_due_date("today", now_utc=frozen) == "2026-09-25"  # still Friday in UTC
    assert normalize_due_date("saturday", now_utc=frozen) == "2026-09-26"


@pytest.mark.asyncio
async def test_bad_due_date_warning_never_logs_title_or_raw_value(_scan_env, caplog):
    """P2 security: only the reminder id and the problem class reach the log."""
    caplog.set_level(logging.DEBUG, logger=scan.__name__)
    row = {"id": "rem-private", "user_id": "jason", "title": "call the divorce lawyer about the house",
           "due_date": "next week sometime", "due_time": None, "snoozed_until": None}

    assert await scan.schedule_due_reminder(_ScanDb([row]), row, now_utc=_now_local(8)) is None

    assert "rem-private" in caplog.text
    assert "divorce" not in caplog.text
    assert "next week sometime" not in caplog.text


@pytest.mark.asyncio
async def test_stored_legacy_out_of_range_due_time_still_fires_at_default(_scan_env, monkeypatch, caplog):
    """P1: a stored '25:00' (the old parser fired it at 01:00) must keep firing —
    at the household default time — with one id-only warning, not be skipped.
    Negative control: pre-fix this returned None on every pass."""
    caplog.set_level(logging.WARNING, logger=scan.__name__)
    monkeypatch.setattr(scan, "_WARNED_BAD_DUE_TIME", set())
    now_utc = _now_local(8)
    tomorrow_iso = (scan.zoe_now(now_utc).date() + timedelta(days=1)).isoformat()
    row = {"id": "rem-legacy", "user_id": "jason", "title": "private thing", "due_date": tomorrow_iso, "due_time": "25:00", "snoozed_until": None}
    db = _ScanDb([row])

    assert await scan.schedule_due_reminder(db, row, now_utc=now_utc) == "rem-legacy"
    assert await scan.schedule_due_reminder(db, row, now_utc=now_utc) == "rem-legacy"

    fired_local = _scan_env[0]["send_at"].astimezone(scan._ZOE_TZ)
    assert (fired_local.hour, fired_local.minute) == (9, 0)
    warnings = [r for r in caplog.records if "invalid stored due_time" in r.getMessage()]
    assert len(warnings) == 1 and "rem-legacy" in warnings[0].getMessage()
    assert "25:00" not in caplog.text and "private thing" not in caplog.text


@pytest.mark.parametrize("bad", ["25:00", "09:99", "noon-ish"])
def test_write_path_rejects_invalid_due_time(bad):
    from reminder_service import normalize_due_time

    with pytest.raises(HTTPException) as exc:
        normalize_due_time(bad)
    assert exc.value.status_code == 422


def test_write_path_keeps_valid_due_time_as_given():
    from reminder_service import normalize_due_time

    assert normalize_due_time("10:25 PM") == "10:25 PM"
    assert normalize_due_time("") is None
    assert normalize_due_time(None) is None
