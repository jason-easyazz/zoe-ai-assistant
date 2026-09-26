"""Shared reminder persistence helpers for API and intent execution."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from typing import Mapping

from fastapi import HTTPException

from guest_policy import require_feature_access
from models import ReminderCreate
from push import broadcaster


def normalize_due_date(raw: object, *, now_utc: datetime | None = None) -> str | None:
    """Resolve a reminder `due_date` to ISO `YYYY-MM-DD` at WRITE time.

    Relative days resolve against the HOUSEHOLD clock (`ZOE_TIMEZONE`, the same
    clock `reminder_scan` fires against — `reminder_scan.zoe_now`), never the
    server's local date: at 23:30 UTC a +08:00 household is already on the next
    day, and "today" must mean THEIR today. `now_utc` is for tests.

    Callers (the API, and the `reminder_create` intent the brain's `add_reminder`
    tool dispatches) pass whatever the model or user said — the 2026-09-25 audit
    found the literal string "tomorrow" stored as a due_date, which
    `reminder_scan` could never parse, so that reminder silently never fired.

    - None / blank → None (no date; time-only reminders are daily).
    - ISO date, or ISO datetime (date part kept) → as-is.
    - Relative day ("today", "tomorrow", "next friday", "june 3") → resolved via
      the same grammar the calendar quick-add already uses
      (`intent_router._parse_date`, imported lazily to keep this module light).
    - A bare month-day with NO year ("june 3") that is already past this year
      means NEXT year's — nobody sets a reminder for a day that has gone.
    - An explicit date in the past (ISO, or a phrase carrying a year) → 422:
      the scan never fires past-dated rows, so storing one is the silent-never-
      fires bug this function exists to prevent. Today is allowed.
    - Anything else → 422; a reminder with an unfireable date is worse than an
      error the caller can see.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    from proactive.triggers.reminder_scan import zoe_now

    today = zoe_now(now_utc).date()

    def _not_past(d: date) -> str:
        if d < today:
            raise HTTPException(
                status_code=422,
                detail=f"due_date {text!r} is already past ({d.isoformat()}); a reminder for a past day can never fire",
            )
        return d.isoformat()

    # ISO date, or ISO datetime ("2026-06-15T09:00") — keep the date part.
    iso_candidate = text[:10] if len(text) > 10 and text[10] in "T " else text
    try:
        return _not_past(date.fromisoformat(iso_candidate))
    except ValueError:
        pass
    from intent_router import _parse_date  # lazy: intent_router is the heavy module

    relative = text.lower()
    if relative.startswith("next "):
        relative = relative[5:].strip()  # "next friday" → the weekday grammar
    resolved = _parse_date(relative, today=today)
    if resolved:
        # _parse_date recognises the PHRASE, not the calendar: "June 31" comes
        # back as "2026-06-31". Re-validate so an unfireable date is a 422 here,
        # never a stored row the scan can only warn about (Codex P2, #1686).
        try:
            d = date.fromisoformat(resolved)
        except ValueError:
            d = None
        if d is not None:
            has_year = re.search(r"\b\d{4}\b", relative) is not None
            if d < today and not has_year:
                # Bare "june 3" after June 3 → next year's June 3 (Greptile P1, #1689).
                try:
                    d = d.replace(year=d.year + 1)
                except ValueError:  # Feb 29 → the next leap year is not "next year"
                    raise HTTPException(status_code=422, detail=f"due_date {text!r} has no valid next occurrence")
            return _not_past(d)
    raise HTTPException(
        status_code=422,
        detail=f"due_date {text!r} is not a date; use YYYY-MM-DD or a day like 'tomorrow'",
    )


def normalize_due_time(raw: object) -> str | None:
    """Validate a reminder `due_time` at WRITE time: blank → None; a real clock
    time ('08:42', '10:25 PM') → kept as given; anything the scanner's strict
    parser rejects ('25:00', '09:99') → 422. Stored legacy rows are handled
    leniently by the scan; new writes must not create more of them."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    from proactive.triggers.reminder_scan import _parse_due_time

    if _parse_due_time(text) is None:
        raise HTTPException(
            status_code=422,
            detail=f"due_time {text!r} is not a clock time; use HH:MM (00-23:00-59), optionally with AM/PM",
        )
    return text


def normalize_recurrence_fields(
    raw_pattern: object, due_date: str | None, due_time: str | None, *, now_utc: datetime | None = None
) -> tuple[str | None, str | None]:
    """Resolve a reminder's recurrence at WRITE time → (rrule, anchor due_date).

    `raw_pattern` may be an RRULE or a spoken phrase ("every weekday"); it is
    stored as a canonical RRULE, never free text the scan cannot run. The
    returned due_date is the FIRST occurrence on/after the given date (or today)
    that is still in the future, which the scan then uses as the rule's anchor.
    Unsupported patterns → 422. No pattern → (None, due_date) unchanged."""
    from reminder_recurrence import first_occurrence_date, normalize_recurrence, parse_rrule

    try:
        rrule = normalize_recurrence(raw_pattern)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"recurring_pattern {str(raw_pattern)!r} is not a supported recurrence "
            "(RRULE FREQ=DAILY|WEEKLY|MONTHLY|YEARLY, or e.g. 'every weekday')",
        )
    if rrule is None:
        return None, due_date
    from proactive.triggers.reminder_scan import _ZOE_TZ, _default_due_hm, _parse_due_time, zoe_now

    now = zoe_now(now_utc)
    hour, minute = _parse_due_time(due_time or "") or _default_due_hm()
    start = date.fromisoformat(due_date) if due_date else now.date()
    first = first_occurrence_date(parse_rrule(rrule), start, hour, minute, now, _ZOE_TZ)
    if first is None:
        raise HTTPException(status_code=422, detail=f"recurrence {rrule!r} has no upcoming occurrence")
    return rrule, first.isoformat()


def row_to_dict(row) -> dict | None:
    """Convert asyncpg/compat rows to a plain reminder dict."""
    if row is None:
        return None
    data = dict(row)
    for key in ("is_active", "acknowledged", "deleted"):
        if key in data and data[key] is not None:
            data[key] = bool(data[key])
    return data


async def _create_notification(db, *, user_id: str, notif_type: str, title: str, message: str, data: dict) -> None:
    await db.execute(
        """INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, NOW())""",
        (
            str(uuid.uuid4()),
            user_id,
            notif_type,
            title,
            message,
            json.dumps(data or {}),
        ),
    )


async def create_reminder_record(payload: ReminderCreate, *, user: Mapping[str, object], db) -> dict:
    """Create a reminder with the same policy, notification, and broadcast behavior as the API route."""
    await require_feature_access(db, user, feature="reminders", action="create")
    user_id = str(user["user_id"])
    reminder_id = str(uuid.uuid4())
    due_date = normalize_due_date(payload.due_date)
    due_time = normalize_due_time(payload.due_time)
    recurring_pattern, due_date = normalize_recurrence_fields(payload.recurring_pattern, due_date, due_time)
    reminder_type = payload.reminder_type
    if recurring_pattern and reminder_type == "one-time":
        reminder_type = "recurring"

    await db.execute(
        """INSERT INTO reminders (
            id, user_id, title, description, reminder_type, category, priority,
            due_date, due_time, recurring_pattern, is_active, acknowledged,
            snoozed_until, visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, ?, 0)""",
        (
            reminder_id,
            user_id,
            payload.title,
            payload.description,
            reminder_type,
            payload.category,
            payload.priority,
            due_date,
            due_time,
            recurring_pattern,
            payload.visibility,
        ),
    )
    await _create_notification(
        db,
        user_id=user_id,
        notif_type="reminder_created",
        title="Reminder Created",
        message=f"Reminder added: {payload.title}",
        data={"reminder_id": reminder_id, "due_date": due_date, "due_time": due_time,
              "recurring_pattern": recurring_pattern},
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = row_to_dict(row) or {}
    await broadcaster.broadcast("reminders", "reminder_created", reminder, user_id=user_id)
    return reminder
