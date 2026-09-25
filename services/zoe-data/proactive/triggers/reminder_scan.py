"""
Tier 2 trigger: reminder table scanner.

Reads the `reminders` table every slow-loop cycle and auto-schedules any
upcoming reminders that haven't been scheduled yet.  This bridges conversational
reminders (created via chat/tool) into APScheduler (Tier 1) so they fire on time.

Rules:
- Reminder has a due_time → schedule it.
  - If due_date is set → use that date.
  - If no due_date → treat as daily: schedule for today if the time hasn't
    passed yet, otherwise schedule for tomorrow.
- Reminder has a due_date but no due_time → schedule it at the household
  default time (`ZOE_REMINDER_DEFAULT_TIME`, default 09:00 local). Date-only
  reminders are how they are actually created from voice/chat ("remind me
  tomorrow to …"); skipping them meant none of them ever fired (2026-09-25
  audit §2.2).
- Reminder has neither → skip (nothing to anchor a fire time to).
- due_date that is not an ISO date (a stored literal like "tomorrow") → skip,
  and WARN once per reminder id so it is visible instead of silently dead.
  `reminder_service.normalize_due_date` now resolves those at write time.
- Reminders that are deleted, acknowledged, or inactive → skip.
- Only schedules reminders up to 25 hours in advance to avoid duplicate
  APScheduler jobs across restarts (APScheduler persists jobs in SQLite).
"""
from __future__ import annotations

import logging
import os
import re
import zoneinfo
from datetime import datetime, timedelta, timezone, date

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

# How far ahead to look when scheduling reminders (hours).
_LOOKAHEAD_HOURS = 25

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))

# Local wall-clock time a date-only reminder fires at. Read per call so an env
# change is honoured without a restart of the scan loop's module state.
_DEFAULT_DUE_TIME_FALLBACK = (9, 0)

# Reminder ids already warned about for a non-ISO due_date — warn ONCE per id,
# not every 5-minute scan cycle.
_WARNED_BAD_DUE_DATE: set[str] = set()


# Bad ZOE_REMINDER_DEFAULT_TIME values already warned about (warn once per value).
_WARNED_BAD_DEFAULT_TIME: set[str] = set()


def _default_due_hm() -> tuple[int, int]:
    """(hour, minute) for date-only reminders: `ZOE_REMINDER_DEFAULT_TIME` (e.g.
    '09:00', '7:30 AM'), falling back to 09:00 — with ONE warning per bad value —
    when unset, unparseable, or out of range ('25:00', '09:99')."""
    raw = os.environ.get("ZOE_REMINDER_DEFAULT_TIME", "")
    if not raw.strip():
        return _DEFAULT_DUE_TIME_FALLBACK
    hm = _parse_due_time(raw)
    if hm is not None:
        return hm
    if raw not in _WARNED_BAD_DEFAULT_TIME:
        _WARNED_BAD_DEFAULT_TIME.add(raw)
        log.warning(
            "reminder_scan: ZOE_REMINDER_DEFAULT_TIME=%r is not a valid clock time "
            "(HH:MM, 00-23:00-59, optional AM/PM); date-only reminders fire at 09:00 local.",
            raw,
        )
    return _DEFAULT_DUE_TIME_FALLBACK


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _parse_due_time(due_time_raw: str) -> tuple[int, int] | None:
    """
    Parse a due_time string into (hour, minute) in 24-hour format.

    Handles formats: '08:42', '10:25PM', '10:25 PM', '8:42 AM', '22:00'
    Returns None if unparseable OR out of range (hour 0-23, minute 0-59 — no
    silent `% 24` wrap: '25:00' is a config error, not 01:00, and '09:99' used
    to reach `datetime()` and raise on every scan cycle).
    """
    if not due_time_raw:
        return None
    s = due_time_raw.strip()

    # Try 12-hour with AM/PM: "10:25PM", "10:25 PM", "8:42am"
    m = re.match(r'^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$', s)
    if m:
        h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if h < 1 or h > 12:
            return None
        if ampm == 'PM' and h != 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0
        return _in_range(h, mi)

    # Try 24-hour: "08:42", "22:00"
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return _in_range(int(m.group(1)), int(m.group(2)))

    return None


def _in_range(hour: int, minute: int) -> tuple[int, int] | None:
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def build_run_at(
    due_date_str: str | None,
    hour: int,
    minute: int,
    now_utc: datetime,
    user_tz_offset_hours: int = 8,  # kept for API compat; _ZOE_TZ is used instead
) -> datetime | None:
    """
    Build a UTC datetime for when the reminder should fire.

    The reminder times stored by Zoe are in the user's local time (ZOE_TIMEZONE).
    We convert to UTC for APScheduler using zoneinfo (handles DST correctly).
    """
    if due_date_str:
        try:
            d = date.fromisoformat(due_date_str)
        except ValueError:
            return None
        # Construct local datetime and convert to UTC
        local_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=_ZOE_TZ)
        return local_dt.astimezone(timezone.utc)

    # No date → daily: use today if the time hasn't passed, else tomorrow
    now_local = now_utc.astimezone(_ZOE_TZ)
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


async def schedule_due_reminder(db, row, *, now_utc: datetime | None = None) -> str | None:
    """Schedule ONE reminder row into APScheduler (Tier 1) if it has a parseable,
    in-window, future due time and isn't already scheduled.

    Single source of truth shared by the slow-loop scan and the reminders
    router's reschedule path. Idempotent: it skips when an unfired
    proactive_scheduled row already exists for this reminder, so it never
    double-schedules. Returns the reminder id if scheduled, else None.
    """
    from proactive.triggers.reminders import schedule_reminder  # deferred

    now_utc = now_utc or datetime.now(timezone.utc)
    rid = row["id"]

    # Skip if an unfired job already exists for this reminder.
    async with db.execute(
        "SELECT 1 FROM proactive_scheduled WHERE item_id = ? AND fired = 0",
        (rid,),
    ) as cur:
        if await cur.fetchone() is not None:
            return None

    due_time = row["due_time"]
    due_date = row["due_date"]
    hm = _parse_due_time(due_time)
    if hm is None:
        if due_time:
            log.debug("reminder_scan: unparseable due_time %r for %s", due_time, rid)
            return None
        if not due_date:
            return None  # neither a time nor a date: nothing to anchor to
        hm = _default_due_hm()  # date-only → household default time

    if due_date and not _is_iso_date(due_date):
        if rid not in _WARNED_BAD_DUE_DATE:
            _WARNED_BAD_DUE_DATE.add(rid)
            log.warning(
                "reminder_scan: reminder %s has non-ISO due_date %r (title %r) — it can never "
                "fire; fix the row (YYYY-MM-DD). New writes resolve relative dates at create time.",
                rid, due_date, row["title"],
            )
        return None

    run_at = build_run_at(due_date, hm[0], hm[1], now_utc)
    if run_at is None:
        return None

    # Only schedule reminders within the lookahead window.
    if run_at > now_utc + timedelta(hours=_LOOKAHEAD_HOURS):
        return None

    # Don't (re)schedule reminders whose time has already passed.
    if run_at <= now_utc:
        log.debug("reminder_scan: reminder %s is past-due (%s), skipping", rid, run_at)
        return None

    await schedule_reminder(
        user_id=row["user_id"],
        message=row["title"],
        send_at=run_at,
        item_id=rid,
    )
    log.info(
        "reminder_scan: scheduled reminder '%s' for user %s at %s",
        row["title"], row["user_id"], run_at.isoformat(),
    )
    return rid


class ReminderScanTrigger(ProactiveTrigger):
    """
    Tier 2 trigger that scans the reminders table and auto-schedules
    upcoming reminders into APScheduler (Tier 1).

    Because it schedules via APScheduler, it returns no TriggerResults itself
    (fire_notification will be called by APScheduler when the job runs).
    """

    trigger_type = "reminder_scan"

    async def check(self, db) -> list[TriggerResult]:
        now_utc = datetime.now(timezone.utc)

        # Fetch active, unacknowledged, non-deleted reminders that have a due_time
        # OR a due_date (date-only rows fire at the household default time) and
        # are not currently snoozed.
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        async with db.execute(
            """SELECT id, user_id, title, due_date, due_time, snoozed_until
               FROM reminders
               WHERE is_active = 1
                 AND acknowledged = 0
                 AND deleted = 0
                 AND ((due_time IS NOT NULL AND due_time != '')
                      OR (due_date IS NOT NULL AND due_date != ''))
                 AND (snoozed_until IS NULL OR snoozed_until <= ?)""",
            (now_iso,),
        ) as cur:
            reminders = await cur.fetchall()

        # Fetch reminder IDs already scheduled but not yet fired — fast-path skip.
        # Using item_id so recurring reminders (no due_date) get rescheduled
        # each day after the previous day's job fires. schedule_due_reminder
        # re-checks this per row, so it stays correct even without this set.
        async with db.execute(
            "SELECT item_id FROM proactive_scheduled WHERE fired = 0 AND item_id != ''"
        ) as cur:
            already_scheduled_items = {row[0] for row in await cur.fetchall()}

        scheduled_count = 0
        for row in reminders:
            if row["id"] in already_scheduled_items:
                continue
            try:
                if await schedule_due_reminder(db, row, now_utc=now_utc):
                    scheduled_count += 1
            except Exception as exc:
                log.warning("reminder_scan: failed to schedule %s: %s", row["id"], exc)

        if scheduled_count:
            log.info("reminder_scan: scheduled %d reminder(s) this cycle", scheduled_count)

        # This trigger drives APScheduler — no direct TriggerResults to return.
        return []
