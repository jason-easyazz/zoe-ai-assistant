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
- Reminder has no due_time → skip (no precise time to fire).
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


def _parse_due_time(due_time_raw: str) -> tuple[int, int] | None:
    """
    Parse a due_time string into (hour, minute) in 24-hour format.

    Handles formats: '08:42', '10:25PM', '10:25 PM', '8:42 AM', '22:00'
    Returns None if unparseable.
    """
    if not due_time_raw:
        return None
    s = due_time_raw.strip()

    # Try 12-hour with AM/PM: "10:25PM", "10:25 PM", "8:42am"
    m = re.match(r'^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$', s)
    if m:
        h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if ampm == 'PM' and h != 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0
        return (h % 24, mi)

    # Try 24-hour: "08:42", "22:00"
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return (int(m.group(1)) % 24, int(m.group(2)))

    return None


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

    hm = _parse_due_time(row["due_time"])
    if hm is None:
        log.debug("reminder_scan: unparseable due_time %r for %s", row["due_time"], rid)
        return None

    run_at = build_run_at(row["due_date"], hm[0], hm[1], now_utc)
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
        # and are not currently snoozed.
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        async with db.execute(
            """SELECT id, user_id, title, due_date, due_time, snoozed_until
               FROM reminders
               WHERE is_active = 1
                 AND acknowledged = 0
                 AND deleted = 0
                 AND due_time IS NOT NULL
                 AND due_time != ''
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
