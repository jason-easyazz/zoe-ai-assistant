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

import re
import uuid
from typing import Optional

# Conversational events: a health event is private.
# Every event writer defaults to visibility='family' (the household calendar the panel shows)
# and every calendar READ (the chat/voice show_calendar tool, the router, the MCP tool)
# returns user_id = me OR visibility = 'family'. Measured 2026-10-06 (day-sim ask 8): the
# user said "I've got the dentist on Friday for a cracked molar and I'm really nervous",
# the brain called add_calendar_event unprompted (category "Health"), and a DIFFERENT
# household member asking "what time is my dentist appointment on Friday?" was handed
# "Dentist appointment for cracked molar on Friday" by show_calendar.
# The chat/voice/MCP writers therefore store a health event as personal (the creator still sees it; the household does not).
# ZOE_CALENDAR_HEALTH_PRIVATE=0 restores the old default.
_HEALTH_CATEGORIES = frozenset([
    "health", "medical", "medication", "therapy", "mental health", "wellbeing", "wellness",
    "dental", "doctor",
])
_HEALTH_TITLE_RE = re.compile(
    r"\b(?:dentist|dental|orthodontist|doctor|dr\.?|gp|physio(?:therapist)?|psycholog\w*|"
    r"psychiatr\w*|therapist|therapy|counsell?or|specialist|surgeon|surgery|hospital|clinic|"
    r"x-?ray|mri|blood\s+test|biopsy|chemo(?:therapy)?|dialysis|midwife|obstetrician|"
    r"gyn(?:ae|e)\w*|optometrist|oncolog\w*|check-?up|vaccin\w*|molar|root\s+canal|"
    r"prescription|medication)\b",
    re.IGNORECASE,
)


def health_private_enabled() -> bool:
    """ZOE_CALENDAR_HEALTH_PRIVATE - default ON (a privacy default), read per call."""
    from typed_env import env_bool

    return env_bool("ZOE_CALENDAR_HEALTH_PRIVATE", True)


def is_health_event(title: str, category: str = "") -> bool:
    return ((category or "").strip().lower() in _HEALTH_CATEGORIES
            or bool(_HEALTH_TITLE_RE.search(title or "")))


def conversational_visibility(title: str, category: str = "") -> str:
    """The visibility for an event written FROM A CONVERSATION (chat, voice, the brain's
    add_calendar_event tool, the MCP tool): personal for a health event, else the household
    default family. The /api/calendar router is unaffected: there the caller chooses."""
    if health_private_enabled() and is_health_event(title, category):
        return "personal"
    return "family"


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
