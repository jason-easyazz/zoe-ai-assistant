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
import re
from datetime import datetime, timedelta, timezone, date

import aiosqlite

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

# How far ahead to look when scheduling reminders (hours).
_LOOKAHEAD_HOURS = 25


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


def _build_run_at(
    due_date_str: str | None,
    hour: int,
    minute: int,
    now_utc: datetime,
    user_tz_offset_hours: int = 8,  # AWST default; TODO: per-user tz
) -> datetime | None:
    """
    Build a UTC datetime for when the reminder should fire.

    The reminder times stored by Zoe are in the user's local time (AWST = UTC+8).
    We convert to UTC for APScheduler.
    """
    offset = timedelta(hours=user_tz_offset_hours)

    if due_date_str:
        try:
            d = date.fromisoformat(due_date_str)
        except ValueError:
            return None
        # Construct local datetime and convert to UTC
        local_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone(offset))
        return local_dt.astimezone(timezone.utc)

    # No date → daily: use today if the time hasn't passed, else tomorrow
    now_local = now_utc.astimezone(timezone(offset))
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


class ReminderScanTrigger(ProactiveTrigger):
    """
    Tier 2 trigger that scans the reminders table and auto-schedules
    upcoming reminders into APScheduler (Tier 1).

    Because it schedules via APScheduler, it returns no TriggerResults itself
    (fire_notification will be called by APScheduler when the job runs).
    """

    trigger_type = "reminder_scan"

    async def check(self, db: aiosqlite.Connection) -> list[TriggerResult]:
        from proactive.triggers.reminders import schedule_reminder  # deferred

        now_utc = datetime.now(timezone.utc)
        lookahead_cutoff = now_utc + timedelta(hours=_LOOKAHEAD_HOURS)

        # Fetch active, unacknowledged, non-deleted reminders that have a due_time.
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, user_id, title, due_date, due_time
               FROM reminders
               WHERE is_active = 1
                 AND acknowledged = 0
                 AND deleted = 0
                 AND due_time IS NOT NULL
                 AND due_time != ''"""
        ) as cur:
            reminders = await cur.fetchall()

        # Fetch reminder IDs already scheduled but not yet fired — skip them.
        # Using item_id so recurring reminders (no due_date) get rescheduled
        # each day after the previous day's job fires.
        async with db.execute(
            "SELECT item_id FROM proactive_scheduled WHERE fired = 0 AND item_id != ''"
        ) as cur:
            already_scheduled_items = {row[0] for row in await cur.fetchall()}

        scheduled_count = 0
        for row in reminders:
            rid = row["id"]

            # Skip if an unfired job already exists for this reminder
            if rid in already_scheduled_items:
                continue

            hm = _parse_due_time(row["due_time"])
            if hm is None:
                log.debug("reminder_scan: unparseable due_time %r for %s", row["due_time"], rid)
                continue

            run_at = _build_run_at(row["due_date"], hm[0], hm[1], now_utc)
            if run_at is None:
                continue

            # Only schedule reminders within the lookahead window
            if run_at > lookahead_cutoff:
                continue

            # Don't re-schedule reminders that have already passed
            if run_at <= now_utc:
                log.debug("reminder_scan: reminder %s is past-due (%s), skipping", rid, run_at)
                continue

            try:
                await schedule_reminder(
                    user_id=row["user_id"],
                    message=row["title"],
                    send_at=run_at,
                    item_id=rid,
                )
                scheduled_count += 1
                log.info(
                    "reminder_scan: scheduled reminder '%s' for user %s at %s",
                    row["title"],
                    row["user_id"],
                    run_at.isoformat(),
                )
            except Exception as exc:
                log.warning("reminder_scan: failed to schedule %s: %s", rid, exc)

        if scheduled_count:
            log.info("reminder_scan: scheduled %d reminder(s) this cycle", scheduled_count)

        # This trigger drives APScheduler — no direct TriggerResults to return.
        return []
