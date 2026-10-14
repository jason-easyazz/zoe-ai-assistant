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

Alembic 0052 added the calendar data model (docs/architecture/calendar-data-model.md):
description, IANA timezone + UTC instants, status, links, source tracking,
recurrence, and the ``calendars`` / ``event_attendees`` / ``event_alarms`` tables.
Those columns ride ``create_event_record(extra=...)``; the helpers below the
writer validate and normalise them (one vocabulary for the router, the .ics
import/export in ``calendar_ical.py`` and, later, the sync connectors).
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

import write_receipts

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
    extra: Optional[dict] = None,
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

    ``extra`` carries the 0052 columns (description, timezone, status, url, source
    tracking, recurrence ...; allow-listed by ``EXTENDED_EVENT_COLUMNS``). They are
    appended to the INSERT only when given, so the 15-column statement every
    existing caller issues is byte-for-byte unchanged.
    """
    event_id = str(uuid.uuid4())
    all_day_int = 1 if all_day else 0
    extra_cols, extra_vals = _extra_columns(extra)
    cols_sql = "".join(f", {c}" for c in extra_cols)
    ph_sql = "".join(", ?" for _ in extra_cols)
    await db.execute(
        f"""INSERT INTO events (
            id, user_id, title, start_date, start_time, end_date, end_time,
            duration, category, location, all_day, recurring, metadata,
            visibility, deleted{cols_sql}
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0{ph_sql})""",
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
            *extra_vals,
        ),
    )
    # The receipt the write-claim guard checks a reply against (write_receipts.py): a row exists now.
    write_receipts.record(user_id, "calendar", event_id, "create_event_record")
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


# ── The 0052 calendar data model (docs/architecture/calendar-data-model.md) ──────────────────────────────
#
# Vocabulary follows RFC 5545 (iCalendar) / RFC 8984 (JSCalendar), lower-cased. Stored visibility keeps the
# 0001 names every existing reader filters on ('family', 'personal') and adds 'busy': the event's TIME is
# shown to the household as "Busy", nothing else. Every reader outside routers/calendar.py filters
# ``visibility = 'family' OR user_id = me``, so a 'busy' row is simply invisible to other members there
# (fail closed) until a reader opts in to the busy projection.

VISIBILITIES = ("family", "personal", "busy")
_VISIBILITY_ALIASES = {
    "household": "family", "shared": "family", "public": "family",
    "private": "personal", "confidential": "personal",
    "busy_only": "busy", "busy-only": "busy", "busyonly": "busy",
}
STATUSES = ("confirmed", "tentative", "cancelled")
_STATUS_ALIASES = {"canceled": "cancelled"}
TRANSPARENCIES = ("opaque", "transparent")
_TRANSPARENCY_ALIASES = {"free": "transparent", "busy": "opaque"}
SOURCES = ("local", "ha", "caldav", "google", "microsoft", "ics")
ATTENDEE_ROLES = ("chair", "req-participant", "opt-participant", "non-participant")
_ROLE_ALIASES = {"participant": "req-participant", "required": "req-participant", "optional": "opt-participant",
                 "organizer": "chair", "attendee": "req-participant", "informational": "non-participant"}
PARTSTATS = ("needs-action", "accepted", "declined", "tentative", "delegated")
_PARTSTAT_ALIASES = {"needs_action": "needs-action", "needsaction": "needs-action", "none": "needs-action",
                     "notresponded": "needs-action", "accept": "accepted", "decline": "declined",
                     "tentativelyaccepted": "tentative", "maybe": "tentative"}
ATTENDEE_KINDS = ("individual", "group", "resource", "room", "unknown")
PERSON_LINK_SOURCES = ("user", "email_match", "inferred")
LINK_SCHEMES = ("http", "https")
CONFERENCE_SCHEMES = ("http", "https", "tel", "sip", "sips")

#: The 0052 ``events`` columns a writer may set through ``create_event_record(extra=...)`` / an update.
EXTENDED_EVENT_COLUMNS = frozenset((
    "description", "timezone", "end_timezone", "start_at", "end_at", "status", "transparency", "url",
    "conference_url", "calendar_id", "source", "external_uid", "external_id", "etag", "sequence",
    "last_synced_at", "read_only", "rrule", "rdates", "exdates", "recurrence_id", "series_id", "deleted_at",
))
#: Fields whose change moves the UTC instants (``start_at`` / ``end_at`` are derived, never client-written).
INSTANT_INPUTS = frozenset(("start_date", "start_time", "end_date", "end_time", "duration", "all_day",
                            "timezone", "end_timezone"))


def _extra_columns(extra: Optional[dict]) -> "tuple[list[str], list]":
    if not extra:
        return [], []
    bad = set(extra) - EXTENDED_EVENT_COLUMNS
    if bad:
        raise ValueError(f"not an events column: {sorted(bad)}")
    cols = sorted(extra)
    return cols, [extra[c] for c in cols]


def _pick(value, allowed, aliases, what: str) -> str:
    key = str(value).strip().lower()
    key = aliases.get(key, key)
    if key not in allowed:
        raise ValueError(f"{what} must be one of: {', '.join(allowed)}")
    return key


def normalize_visibility(value) -> str:
    return _pick(value, VISIBILITIES, _VISIBILITY_ALIASES, "visibility")


def normalize_status(value) -> str:
    return _pick(value, STATUSES, _STATUS_ALIASES, "status")


def normalize_transparency(value) -> str:
    return _pick(value, TRANSPARENCIES, _TRANSPARENCY_ALIASES, "transparency")


def validate_timezone(value) -> Optional[str]:
    """An IANA zone name (``Australia/Perth``), or None. A connector maps Graph's Windows zone names first."""
    if value is None or str(value).strip() == "":
        return None
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = str(value).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown IANA time zone: {name!r}") from None
    return name


def validate_link(value, schemes=LINK_SCHEMES) -> Optional[str]:
    """A URL a surface may render as a link: only the listed schemes (never ``javascript:`` / ``data:``)."""
    if value is None or str(value).strip() == "":
        return None
    from urllib.parse import urlsplit

    url = str(value).strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in schemes or (scheme in ("http", "https") and not parts.netloc):
        raise ValueError(f"link must use one of: {', '.join(schemes)}")
    return url


def parse_time(value):
    from datetime import datetime as _dt

    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return _dt.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def parse_date(value):
    from datetime import date as _date

    if not value:
        return None
    try:
        return _date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def utc_iso(dt) -> str:
    """A UTC instant as stored in ``start_at`` / ``end_at``: fixed-width ``YYYY-MM-DDTHH:MM:SSZ`` (sorts as text)."""
    from datetime import timezone as _tz

    return dt.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_instants(event: dict) -> "tuple[Optional[str], Optional[str]]":
    """``(start_at, end_at)`` UTC instants for a timed event with an explicit IANA ``timezone``.

    ``start_date`` / ``start_time`` stay the wall clock in the event's own zone (RFC 5545 ``DTSTART;TZID=``).
    No ``timezone`` = a FLOATING event (RFC 5545 3.3.5: the same wall clock wherever you are - how every row
    written before 0052 is read), and an all-day event is a DATE: neither has an instant (None)."""
    from datetime import datetime as _dt, timedelta
    from zoneinfo import ZoneInfo

    tz = event.get("timezone")
    if not tz or event.get("all_day"):
        return None, None
    d0, t0 = parse_date(event.get("start_date")), parse_time(event.get("start_time"))
    if d0 is None or t0 is None:
        return None, None
    start = _dt.combine(d0, t0).replace(tzinfo=ZoneInfo(tz))
    end = None
    t1 = parse_time(event.get("end_time"))
    if t1 is not None:
        d1 = parse_date(event.get("end_date")) or d0
        end = _dt.combine(d1, t1).replace(tzinfo=ZoneInfo(event.get("end_timezone") or tz))
    elif event.get("duration"):
        try:
            end = start + timedelta(minutes=int(event["duration"]))
        except (TypeError, ValueError):
            end = None
    return utc_iso(start), (utc_iso(end) if end is not None else None)


def normalize_attendee(raw: dict, position: int = 0) -> dict:
    """One attendee in the stored vocabulary. Raises ValueError on a value outside it."""
    name = (raw.get("display_name") or raw.get("name") or "").strip() or None
    email = (raw.get("email") or "").strip() or None
    if email and email.lower().startswith("mailto:"):
        email = email[7:] or None
    person_id = raw.get("person_id") or None
    if not name and not email and not person_id:
        raise ValueError("an attendee needs a name, an email or a person_id")
    is_org = bool(raw.get("is_organizer"))
    link_source = None
    if person_id:
        link_source = _pick(raw.get("person_link_source") or "user", PERSON_LINK_SOURCES, {}, "person_link_source")
    return {
        "display_name": name,
        "email": email,
        "person_id": person_id,
        "person_link_source": link_source,
        "role": _pick(raw.get("role") or ("chair" if is_org else "req-participant"), ATTENDEE_ROLES, _ROLE_ALIASES, "role"),
        "partstat": _pick(raw.get("partstat") or "needs-action", PARTSTATS, _PARTSTAT_ALIASES, "partstat"),
        "kind": _pick(raw.get("kind") or "individual", ATTENDEE_KINDS, {}, "kind"),
        "is_organizer": 1 if is_org else 0,
        "rsvp": 1 if raw.get("rsvp") else 0,
        "position": int(raw.get("position") if raw.get("position") is not None else position),
    }


def now_text() -> str:
    from datetime import datetime as _dt, timezone as _tz

    return utc_iso(_dt.now(_tz.utc))


async def replace_attendees(db, event_id: str, owner_id: str, attendees: list) -> int:
    """Make ``attendees`` (already normalised) the event's attendee rows. ``user_id`` on each row is the event's
    OWNER - the member whose "forget <person>" reaches it (``memory_forget_cascade``)."""
    await db.execute("DELETE FROM event_attendees WHERE event_id = ?", (event_id,))
    now = now_text()
    for i, a in enumerate(attendees):
        await db.execute(
            "INSERT INTO event_attendees (id, event_id, user_id, display_name, email, person_id, person_link_source, "
            "role, partstat, kind, is_organizer, rsvp, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, owner_id, a.get("display_name"), a.get("email"), a.get("person_id"),
             a.get("person_link_source"), a["role"], a["partstat"], a["kind"], a["is_organizer"], a["rsvp"],
             a.get("position", i), now, now),
        )
    return len(attendees)


async def replace_alarms(db, event_id: str, owner_id: str, alarms: list) -> int:
    """Store the event's alarms (VALARM / JSCalendar alerts). STORED ONLY: nothing fires them yet."""
    await db.execute("DELETE FROM event_alarms WHERE event_id = ?", (event_id,))
    now = now_text()
    for a in alarms:
        await db.execute(
            "INSERT INTO event_alarms (id, event_id, user_id, action, offset_minutes, related, trigger_at, description, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, owner_id, (a.get("action") or "display").lower(), a.get("offset_minutes"),
             (a.get("related") or "start").lower(), a.get("trigger_at"), a.get("description"), now),
        )
    return len(alarms)


async def unlinkable_person_ids(db, person_ids, *, actor_id: str, owner_id: str) -> list:
    """The ``person_ids`` an attendee may NOT link to: missing or forgotten (soft-deleted), or a PRIVATE contact that is
    not both the actor's and the event owner's. A link may point at the household's contacts, or at the owner's own
    when the owner is the one writing - never at another member's private contact."""
    bad = []
    for pid in dict.fromkeys(p for p in person_ids if p):
        cur = await db.execute("SELECT user_id, visibility, deleted FROM people WHERE id = ?", (pid,))
        row = await cur.fetchone()
        if row is None:
            bad.append(pid)
            continue
        r = dict(row)
        family = r.get("visibility") == "family"
        mine = r.get("user_id") == actor_id and r.get("user_id") == owner_id
        if r.get("deleted") or not (family or mine):
            bad.append(pid)
    return bad


async def load_children(db, events: list) -> None:
    """Attach ``attendees`` and ``alarms`` (lists, possibly empty) to each event dict: two queries for the whole list."""
    for e in events:
        e.setdefault("attendees", [])
        e.setdefault("alarms", [])
    by_id = {e["id"]: e for e in events if e.get("id") and not e.get("busy_only")}
    if not by_id:
        return
    ids = list(by_id)
    ph = ", ".join("?" for _ in ids)
    for table, key, order in (("event_attendees", "attendees", "event_id, position"),
                              ("event_alarms", "alarms", "event_id, offset_minutes")):
        cur = await db.execute(f"SELECT * FROM {table} WHERE event_id IN ({ph}) ORDER BY {order}", ids)
        async for row in cur:
            d = dict(row)
            target = by_id.get(d.get("event_id"))
            if target is None:
                continue
            if key == "attendees":
                d["is_organizer"] = bool(d.get("is_organizer"))
                d["rsvp"] = bool(d.get("rsvp"))
            for drop in ("event_id", "user_id", "created_at", "updated_at"):
                d.pop(drop, None)
            target[key].append(d)


#: What another member sees of a 'busy' event: when, and that it is busy. Never the title, notes, place,
#: people, links, category or metadata.
BUSY_FIELDS = ("id", "user_id", "start_date", "start_time", "end_date", "end_time", "duration", "all_day",
               "timezone", "end_timezone", "start_at", "end_at", "status", "transparency", "visibility",
               "rrule", "rdates", "exdates", "recurrence_id", "series_id", "deleted")


def busy_projection(event: dict) -> dict:
    out = {k: event.get(k) for k in BUSY_FIELDS if k in event}
    out.update({"title": "Busy", "busy_only": True, "category": None, "location": None, "description": None,
                "url": None, "conference_url": None, "metadata": None, "attendees": [], "alarms": []})
    return out
