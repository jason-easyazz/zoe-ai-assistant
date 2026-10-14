"""
FastAPI router for calendar events.
Mounted at prefix="/api/calendar" with tag "calendar".

Alembic 0052 (docs/architecture/calendar-data-model.md) added description, IANA timezone + UTC instants,
status, transparency, links, attendees (child table, optional people-graph link), alarms, recurrence and
source tracking. A payload without those fields (every client written before 0052) behaves exactly as
before; every response carries them (``attendees`` / ``alarms`` as lists, possibly empty).

Visibility: ``family`` (the household sees it), ``personal`` (only the owner), ``busy`` (the household sees
WHEN, as "Busy", and nothing else - ``calendar_service.busy_projection``). Aliases ``household`` /
``private`` / ``busy_only`` are accepted on write; an unknown value is a 422.
"""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import calendar_service as cal
from auth import get_current_user
from calendar_service import create_event_record
from calendar_utils import row_to_event
from database import get_db
from guest_policy import require_feature_access
from models import EventCreate, EventUpdate
from push import broadcaster

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

#: 0052 fields a client writes directly (validated below). Everything else in EXTENDED_EVENT_COLUMNS is
#: written by trusted code only: the derived instants, and the source-tracking / recurrence columns a
#: connector or the .ics import owns.
_CLIENT_FIELDS = ("description", "timezone", "end_timezone", "status", "transparency", "url", "conference_url")


def _row_to_event(row) -> dict:
    """Convert asyncpg Row to dict, parsing metadata JSON."""
    return row_to_event(row)


def _visibility_filter_sql() -> str:
    """SQL fragment for WRITES and owner checks: (visibility='family' OR user_id=?) AND deleted=0."""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


def _read_filter_sql() -> str:
    """SQL fragment for READS: also another member's busy-only events (returned as the busy projection)."""
    return "(visibility IN ('family', 'busy') OR user_id = ?) AND deleted = 0"


def _present(row, user_id: str) -> dict:
    """One event as the API returns it: the busy projection for another member's busy-only event; otherwise the
    row with ``description`` bridged both ways to the desktop calendar's legacy ``metadata.description``."""
    event = _row_to_event(row)
    if event.get("visibility") == "busy" and event.get("user_id") != user_id:
        return cal.busy_projection(event)
    meta = event.get("metadata")
    if event.get("description") is None and isinstance(meta, dict) and isinstance(meta.get("description"), str):
        event["description"] = meta["description"] or None
    elif event.get("description") is not None and (meta is None or isinstance(meta, dict)):
        event["metadata"] = {**(meta or {}), "description": event["description"]}
    return event


async def _present_all(db, rows, user_id: str) -> list:
    events = [_present(r, user_id) for r in rows]
    await cal.load_children(db, events)
    return events


def _422(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _clean_fields(data: dict) -> dict:
    """Validate + normalise the client-writable 0052 fields present in ``data`` (a model_dump). Raises 422."""
    out = {}
    try:
        for key in _CLIENT_FIELDS:
            if key not in data:
                continue
            value = data[key]
            if key in ("timezone", "end_timezone"):
                value = cal.validate_timezone(value)
            elif key == "status":
                value = cal.normalize_status(value) if value is not None else "confirmed"
            elif key == "transparency":
                value = cal.normalize_transparency(value) if value is not None else "opaque"
            elif key == "url":
                value = cal.validate_link(value)
            elif key == "conference_url":
                value = cal.validate_link(value, cal.CONFERENCE_SCHEMES)
            elif key == "description":
                value = value if value else None
            out[key] = value
    except ValueError as exc:
        raise _422(exc) from None
    return out


async def _clean_attendees(db, raw, *, actor_id: str, owner_id: str) -> Optional[list]:
    """None = leave the attendees alone; a list = replace them. Person links are checked (422 on a bad one)."""
    if raw is None:
        return None
    try:
        attendees = [cal.normalize_attendee(a if isinstance(a, dict) else a.model_dump(), i) for i, a in enumerate(raw)]
    except ValueError as exc:
        raise _422(exc) from None
    bad = await cal.unlinkable_person_ids(db, [a["person_id"] for a in attendees], actor_id=actor_id, owner_id=owner_id)
    if bad:
        raise HTTPException(status_code=422, detail="attendee person_id is not a contact you can link")
    return attendees


async def _enforce_calendar_read_access(db, user: dict) -> str:
    """Apply the shared calendar read gate, then return user_id."""
    await require_feature_access(db, user, feature="calendar", action="read")
    return user["user_id"]


@router.get("/events", response_model=dict)
async def list_events(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_cancelled: bool = Query(False),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List events with optional start_date, end_date, category filters. Cancelled events only on request."""
    user_id = await _enforce_calendar_read_access(db, user)
    conditions = [_read_filter_sql()]
    params: list = [user_id]

    if start_date:
        conditions.append("start_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("start_date <= ?")
        params.append(end_date)
    if category:
        # a category filter never reaches another member's busy-only event (its category is hidden)
        conditions.append("category = ? AND (visibility <> 'busy' OR user_id = ?)")
        params.extend([category, user_id])
    if include_cancelled is not True:
        conditions.append("status <> 'cancelled'")

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM events WHERE {where} ORDER BY start_date, start_time"
    cursor = await db.execute(sql, params)
    rows = [row async for row in cursor]
    return {"events": await _present_all(db, rows, user_id)}


@router.get("/events/today", response_model=dict)
async def list_today_events(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get today's events."""
    user_id = await _enforce_calendar_read_access(db, user)
    today = date.today().isoformat()
    sql = (f"SELECT * FROM events WHERE {_read_filter_sql()} AND status <> 'cancelled' AND start_date = ? "
           "ORDER BY start_time")
    cursor = await db.execute(sql, [user_id, today])
    rows = [row async for row in cursor]
    return {"events": await _present_all(db, rows, user_id)}


@router.get("/events/{event_id}", response_model=dict)
async def get_event(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single event by ID."""
    user_id = await _enforce_calendar_read_access(db, user)
    where = f"{_read_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return (await _present_all(db, [row], user_id))[0]


@router.post("/events", response_model=dict)
async def create_event(
    payload: EventCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new calendar event."""
    await require_feature_access(db, user, feature="calendar", action="create")
    user_id = user["user_id"]
    try:
        visibility = cal.normalize_visibility(payload.visibility)
    except ValueError as exc:
        raise _422(exc) from None
    data = payload.model_dump(exclude_unset=True)
    extra = _clean_fields(data)
    if "description" not in extra and isinstance(payload.metadata, dict) and isinstance(payload.metadata.get("description"), str):
        # the desktop calendar writes notes into metadata.description: the column is the one place they live now
        if payload.metadata["description"]:
            extra["description"] = payload.metadata["description"]
    if extra.get("timezone"):
        start_at, end_at = cal.derive_instants({**payload.model_dump(), **extra})
        extra.update({"start_at": start_at, "end_at": end_at})
    attendees = await _clean_attendees(db, payload.attendees, actor_id=user_id, owner_id=user_id)
    metadata_json = json.dumps(payload.metadata) if payload.metadata else None

    record = await create_event_record(
        db,
        user_id=user_id,
        title=payload.title,
        start_date=payload.start_date,
        start_time=payload.start_time,
        end_date=payload.end_date,
        end_time=payload.end_time,
        duration=payload.duration,
        category=payload.category,
        location=payload.location,
        all_day=payload.all_day,
        recurring=payload.recurring,
        metadata=metadata_json,
        visibility=visibility,
        extra=extra or None,
    )
    event_id = record["id"]
    if attendees:
        await cal.replace_attendees(db, event_id, user_id, attendees)
    await db.commit()

    cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
    row = await cursor.fetchone()
    event = (await _present_all(db, [row], user_id))[0]

    await broadcaster.broadcast("calendar", "event_created", event, user_id=user_id)
    return event


@router.put("/events/{event_id}", response_model=dict)
async def update_event(
    event_id: str,
    payload: EventUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing event."""
    await require_feature_access(db, user, feature="calendar", action="update")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    current = dict(row)
    if current.get("read_only"):
        # an externally-owned event (a synced calendar Zoe may not write): the change belongs at its source
        raise HTTPException(status_code=409, detail="This event comes from a read-only calendar")
    # Household model: a family-visible event is editable by any signed-in
    # household member (the visibility filter above already scopes the lookup
    # to family-or-own). This mirrors the people router — the panel is a
    # shared surface, and the stricter owner-only check made family events
    # uneditable from it (403s in the operator's 2026-07-13 report).
    # …but only the OWNER may change visibility: a non-owner flipping a family
    # event to 'personal' would hijack it out of the household's (and the
    # owner's) view entirely (same guard as reminders).
    owner_id = current.get("user_id")
    is_owner = owner_id == user_id

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    if not is_owner:
        data.pop("visibility", None)
    has_attendees = "attendees" in payload.model_fields_set
    data.pop("attendees", None)
    extra = _clean_fields(data)
    for key in _CLIENT_FIELDS:
        data.pop(key, None)
    if "description" not in extra and isinstance(data.get("metadata"), dict) \
            and isinstance(data["metadata"].get("description"), str):
        extra["description"] = data["metadata"]["description"] or None
    if "visibility" in data:
        if data["visibility"] is None:
            data.pop("visibility")
        else:
            try:
                data["visibility"] = cal.normalize_visibility(data["visibility"])
            except ValueError as exc:
                raise _422(exc) from None
    if cal.INSTANT_INPUTS.intersection(data) or cal.INSTANT_INPUTS.intersection(extra):
        start_at, end_at = cal.derive_instants({**current, **data, **extra})
        extra.update({"start_at": start_at, "end_at": end_at})
    attendees = None
    if has_attendees:
        attendees = await _clean_attendees(db, payload.attendees or [], actor_id=user_id, owner_id=owner_id)

    for key, value in data.items():
        if key == "metadata":
            updates.append("metadata = ?")
            params.append(json.dumps(value) if value is not None else None)
        elif key == "all_day":
            updates.append("all_day = ?")
            params.append(1 if value else 0)
        else:
            updates.append(f"{key} = ?")
            params.append(value)
    for key, value in extra.items():
        if key not in cal.EXTENDED_EVENT_COLUMNS:  # defence in depth: the column name is interpolated
            continue
        updates.append(f"{key} = ?")
        params.append(value)

    if not updates and attendees is None:
        cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
        row = await cursor.fetchone()
        return (await _present_all(db, [row], user_id))[0]

    updates.append("updated_at = NOW()")
    params.extend([event_id])
    sql = f"UPDATE events SET {', '.join(updates)} WHERE id = ?"
    await db.execute(sql, params)
    if attendees is not None:
        await cal.replace_attendees(db, event_id, owner_id, attendees)
    await db.commit()

    cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
    row = await cursor.fetchone()
    event = (await _present_all(db, [row], user_id))[0]

    await broadcaster.broadcast("calendar", "event_updated", event, user_id=user_id)
    return event


@router.delete("/events/{event_id}", response_model=dict)
async def delete_event(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete an event (set deleted=1; ``deleted_at`` is the sync tombstone's time)."""
    await require_feature_access(db, user, feature="calendar", action="delete")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if dict(row).get("read_only"):
        raise HTTPException(status_code=409, detail="This event comes from a read-only calendar")
    # Household model: family-visible events are deletable by any signed-in
    # household member — same rationale as update_event above (soft delete,
    # reversible; mirrors the people router's family-or-own semantics).

    await db.execute(
        "UPDATE events SET deleted = 1, deleted_at = ?, updated_at = NOW() WHERE id = ?",
        [cal.now_text(), event_id],
    )
    await db.commit()

    event = _row_to_event(row)
    event["deleted"] = True

    await broadcaster.broadcast("calendar", "event_deleted", {"id": event_id, **event}, user_id=user_id)
    return {"ok": True, "id": event_id}
