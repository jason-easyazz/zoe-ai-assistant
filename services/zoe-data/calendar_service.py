"""Single canonical calendar-event writer.

One INSERT for the `events` table, shared by every writer (the voice/direct
executor in ``intent_router``, the ``calendar_create_event`` MCP tool, and the
``/api/calendar/events`` router). Callers keep their own date parsing, UI
notifications, MemPalace policy, and response formatting — those DIFFER per
caller and preserving them is how observable behaviour stays identical. This
module owns ONLY the row write.

The `events` schema (see alembic 0001_initial_schema.py) has 15 writable
columns; this helper writes the full superset so a single INSERT covers all
three callers. Voice-path callers that only supply a subset leave the rest
NULL / defaulted exactly as their narrower INSERTs did.
"""

from __future__ import annotations

import uuid
from typing import Optional


async def create_event_record(
    db,
    *,
    user_id: str,
    title: str,
    start_date: str,
    start_time: Optional[str] = None,
    end_date: Optional[str] = None,
    end_time: Optional[str] = None,
    duration: Optional[int] = None,
    category: str = "general",
    location: Optional[str] = None,
    all_day: bool = False,
    recurring: Optional[str] = None,
    metadata: Optional[str] = None,
    visibility: str = "family",
) -> dict:
    """Insert one row into ``events`` and return a record dict.

    Takes an already-open ``db`` handle (AsyncpgCompat / aiosqlite style) and
    issues the single canonical INSERT with ``?`` placeholders. Does NOT parse
    dates, notify the UI, format responses, touch MemPalace, or commit — those
    are the caller's job (asyncpg auto-commits; ``db.commit()`` is a no-op).

    ``metadata`` is written verbatim: pass an already-serialized JSON string (or
    None). ``all_day`` is coerced to the stored 0/1 integer. The returned dict
    reflects the values written; callers that re-read the row for their response
    may ignore it.
    """
    event_id = str(uuid.uuid4())
    all_day_int = 1 if all_day else 0
    await db.execute(
        """INSERT INTO events (
            id, user_id, title, start_date, start_time, end_date, end_time,
            duration, category, location, all_day, recurring, metadata,
            visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            event_id,
            user_id,
            title,
            start_date,
            start_time,
            end_date,
            end_time,
            duration,
            category,
            location,
            all_day_int,
            recurring,
            metadata,
            visibility,
        ),
    )
    return {
        "id": event_id,
        "title": title,
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "category": category,
        "location": location,
        "all_day": all_day_int,
        "visibility": visibility,
    }
