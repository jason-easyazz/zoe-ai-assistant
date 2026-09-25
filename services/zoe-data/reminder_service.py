"""Shared reminder persistence helpers for API and intent execution."""

from __future__ import annotations

import json
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
    - Anything else → 422; a reminder with an unfireable date is worse than an
      error the caller can see.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # ISO date, or ISO datetime ("2026-06-15T09:00") — keep the date part.
    iso_candidate = text[:10] if len(text) > 10 and text[10] in "T " else text
    try:
        return date.fromisoformat(iso_candidate).isoformat()
    except ValueError:
        pass
    from intent_router import _parse_date  # lazy: intent_router is the heavy module
    from proactive.triggers.reminder_scan import zoe_now

    relative = text.lower()
    if relative.startswith("next "):
        relative = relative[5:].strip()  # "next friday" → the weekday grammar
    resolved = _parse_date(relative, today=zoe_now(now_utc).date())
    if resolved:
        # _parse_date recognises the PHRASE, not the calendar: "June 31" comes
        # back as "2026-06-31". Re-validate so an unfireable date is a 422 here,
        # never a stored row the scan can only warn about (Codex P2, #1686).
        try:
            return date.fromisoformat(resolved).isoformat()
        except ValueError:
            pass
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
            payload.reminder_type,
            payload.category,
            payload.priority,
            due_date,
            due_time,
            payload.recurring_pattern,
            payload.visibility,
        ),
    )
    await _create_notification(
        db,
        user_id=user_id,
        notif_type="reminder_created",
        title="Reminder Created",
        message=f"Reminder added: {payload.title}",
        data={"reminder_id": reminder_id, "due_date": due_date, "due_time": due_time},
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = row_to_dict(row) or {}
    await broadcaster.broadcast("reminders", "reminder_created", reminder, user_id=user_id)
    return reminder
