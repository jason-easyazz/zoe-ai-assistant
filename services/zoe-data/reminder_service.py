"""Shared reminder persistence helpers for API and intent execution."""

from __future__ import annotations

import json
import uuid
from typing import Mapping

from guest_policy import require_feature_access
from models import ReminderCreate
from push import broadcaster


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
            payload.due_date,
            payload.due_time,
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
        data={"reminder_id": reminder_id, "due_date": payload.due_date, "due_time": payload.due_time},
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = row_to_dict(row) or {}
    await broadcaster.broadcast("reminders", "reminder_created", reminder, user_id=user_id)
    return reminder
