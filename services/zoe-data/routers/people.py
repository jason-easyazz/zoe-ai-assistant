"""
People/contacts API router — v2 (People CRM).

Changes from v1:
- circle field added to all CRUD operations
- /fields routes moved before /{person_id} to fix live-404 route-order bug
- Sub-resources: activities, important-dates, gift-ideas, bucket-list, mark-read
- Health score recalculated on every write
- Symmetric MemPalace archive on DELETE
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from models import (
    PersonCreate,
    PersonUpdate,
    PeopleFieldDefinitionCreate,
    PeopleFieldDefinitionUpdate,
)
from push import broadcaster

router = APIRouter(prefix="/api/people", tags=["people"])

_VALID_CIRCLES = {"inner", "friends", "family", "work", "acquaintance", "public"}


# ── Row helpers ────────────────────────────────────────────────────────────────

def _row_to_person(row) -> dict:
    """Convert DB row to person dict with preferences as parsed JSON."""
    pref = row["preferences"] if hasattr(row, "__getitem__") else getattr(row, "preferences", None)
    if pref and isinstance(pref, str):
        try:
            pref = json.loads(pref)
        except json.JSONDecodeError:
            pref = None
    d = dict(row) if not isinstance(row, dict) else row
    return {
        "id": d.get("id"),
        "user_id": d.get("user_id"),
        "name": d.get("name"),
        "relationship": d.get("relationship"),
        "circle": d.get("circle", "acquaintance"),
        "email": d.get("email"),
        "phone": d.get("phone"),
        "birthday": d.get("birthday"),
        "notes": d.get("notes"),
        "preferences": pref,
        "visibility": d.get("visibility"),
        "health_score": d.get("health_score", 0.5),
        "notification_count": d.get("notification_count", 0),
        "contact_count": d.get("contact_count", 0),
        "last_contacted_at": d.get("last_contacted_at"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


async def _load_custom_fields(db, person_ids: list[str]) -> dict[str, dict]:
    if not person_ids:
        return {}
    placeholders = ",".join(["?"] * len(person_ids))
    cursor = await db.execute(
        f"SELECT person_id, field_key, value_json FROM people_field_values WHERE person_id IN ({placeholders})",
        person_ids,
    )
    rows = await cursor.fetchall()
    by_person: dict[str, dict] = {pid: {} for pid in person_ids}
    for row in rows:
        value = dict(row).get("value_json") if not isinstance(row, dict) else row.get("value_json")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        pid = dict(row).get("person_id") if not isinstance(row, dict) else row.get("person_id")
        by_person.setdefault(pid, {})[dict(row).get("field_key") if not isinstance(row, dict) else row.get("field_key")] = value
    return by_person


async def _upsert_custom_fields(db, person_id: str, custom_fields: Optional[dict]) -> None:
    if not custom_fields:
        return
    for field_key, value in custom_fields.items():
        payload = json.dumps(value)
        await db.execute(
            """INSERT INTO people_field_values (id, person_id, field_key, value_json, updated_at)
               VALUES (?, ?, ?, ?, NOW())
               ON CONFLICT(person_id, field_key)
               DO UPDATE SET value_json=excluded.value_json, updated_at=NOW()""",
            (str(uuid.uuid4()), person_id, field_key, payload),
        )


async def _store_person_memory(db, user_id: str, person: dict, action: str):
    """Write a person-related fact to MemPalace through MemoryService."""
    summary = f"{person.get('name')} ({person.get('relationship') or 'contact'})"
    notes = person.get("notes") or ""
    circle = person.get("circle", "acquaintance")
    fact = f"Person in contacts: {summary}. Circle: {circle}. {notes[:200]}".strip().rstrip(".")
    try:
        from memory_service import MemoryServiceError, get_memory_service
        try:
            await get_memory_service().ingest(
                fact,
                user_id=user_id,
                source=f"person_{action}",
                memory_type="person",
                confidence=0.85,
                status="approved",
                tags=["person", action],
                entity_type="person",
                entity_id=person.get("id"),
            )
        except MemoryServiceError as exc:
            import logging as _lg
            _lg.getLogger(__name__).info("people: memory ingest skipped: %s", exc)
    except Exception:
        pass


def _visibility_filter_sql() -> str:
    return "(visibility = 'family' OR user_id = ?)"


async def _recalc_health(db, person_id: str, user_id: str) -> None:
    """Recalculate and persist health_score in the background."""
    try:
        from person_health import recalc_and_save
        await recalc_and_save(person_id, user_id, db)
    except Exception as exc:
        import logging as _lg
        _lg.getLogger(__name__).debug("health recalc failed for %s: %s", person_id, exc)


async def _get_person_or_404(db, person_id: str, user_id: str) -> dict:
    vis = _visibility_filter_sql()
    async with db.execute(
        f"SELECT * FROM people WHERE id = ? AND deleted = 0 AND {vis}",
        (person_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    return _row_to_person(dict(row))


# ── Core CRUD ─────────────────────────────────────────────────────────────────

@router.get("/")
async def list_people(
    search: Optional[str] = Query(None),
    circle: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List people with optional search, circle filter, limit, offset."""
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    params = [user_id]
    filters = [f"deleted = 0 AND {vis}"]

    if circle and circle in _VALID_CIRCLES:
        filters.append("circle = ?")
        params.append(circle)

    if search:
        filters.append("(name LIKE ? OR email LIKE ? OR phone LIKE ? OR relationship LIKE ?)")
        q = f"%{search}%"
        params.extend([q, q, q, q])

    where = " AND ".join(filters)
    sql = f"SELECT * FROM people WHERE {where} ORDER BY name LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    count_sql = f"SELECT COUNT(*) FROM people WHERE {where}"
    count_params = params[:-2]
    async with db.execute(count_sql, count_params) as cur:
        count_row = await cur.fetchone()
        count = count_row[0] if count_row else 0

    people = [_row_to_person(dict(r)) for r in rows]
    custom_by_person = await _load_custom_fields(db, [p["id"] for p in people])
    for person in people:
        person["custom_fields"] = custom_by_person.get(person["id"], {})
    return {"people": people, "count": count}


@router.post("/")
async def create_person(
    body: PersonCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a person."""
    await require_feature_access(db, user, feature="people", action="create")
    user_id = user["user_id"]
    person_id = str(uuid.uuid4())
    pref = json.dumps(body.preferences) if body.preferences is not None else None
    circle = getattr(body, "circle", "acquaintance") or "acquaintance"
    if circle not in _VALID_CIRCLES:
        circle = "acquaintance"

    await db.execute(
        """
        INSERT INTO people (id, user_id, name, relationship, circle, email, phone, birthday, notes, preferences, visibility)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id, user_id, body.name, body.relationship, circle,
            body.email, body.phone, body.birthday, body.notes, pref, body.visibility,
        ),
    )
    await _upsert_custom_fields(db, person_id, body.custom_fields)
    await db.commit()

    async with db.execute("SELECT * FROM people WHERE id = ?", (person_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to fetch created person")
    person = _row_to_person(dict(row))
    custom_fields = await _load_custom_fields(db, [person_id])
    person["custom_fields"] = custom_fields.get(person_id, {})
    await _store_person_memory(db, user_id, person, "created")
    await _recalc_health(db, person_id, user_id)
    await db.commit()

    await broadcaster.broadcast("all", "people:created", person)
    return person


@router.get("/search")
async def search_people(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Search people by q param."""
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    pattern = f"%{q}%"
    sql = f"""
        SELECT * FROM people
        WHERE deleted = 0 AND {vis}
        AND (name LIKE ? OR email LIKE ? OR phone LIKE ? OR relationship LIKE ? OR notes LIKE ?)
        ORDER BY name
        LIMIT ?
    """
    async with db.execute(sql, (user_id, pattern, pattern, pattern, pattern, pattern, limit)) as cur:
        rows = await cur.fetchall()
    people = [_row_to_person(dict(r)) for r in rows]
    custom_by_person = await _load_custom_fields(db, [p["id"] for p in people])
    for person in people:
        person["custom_fields"] = custom_by_person.get(person["id"], {})
    return {"people": people, "count": len(people)}


# ── /fields routes MUST come before /{person_id} to avoid route collision ─────

@router.get("/fields")
async def list_people_fields(
    active_only: bool = Query(True),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="manage_fields")
    sql = """SELECT * FROM people_field_definitions
             WHERE (? = 0 OR is_active = 1)
             ORDER BY sort_order, label"""
    cursor = await db.execute(sql, (1 if active_only else 0,))
    rows = await cursor.fetchall()
    fields = []
    for row in rows:
        d = dict(row)
        options = d.get("options_json")
        if options and isinstance(options, str):
            try:
                options = json.loads(options)
            except json.JSONDecodeError:
                options = None
        fields.append({
            "id": d.get("id"),
            "field_key": d.get("field_key"),
            "label": d.get("label"),
            "field_type": d.get("field_type"),
            "required": bool(d.get("required")),
            "options": options,
            "scope": d.get("scope"),
            "sort_order": d.get("sort_order"),
            "visibility": d.get("visibility"),
            "is_active": bool(d.get("is_active")),
        })
    return {"fields": fields, "count": len(fields)}


@router.post("/fields")
async def create_people_field(
    body: PeopleFieldDefinitionCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="manage_fields")
    field_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO people_field_definitions
           (id, field_key, label, field_type, required, options_json, scope, sort_order, visibility, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            field_id, body.field_key, body.label, body.field_type,
            1 if body.required else 0,
            json.dumps(body.options) if body.options is not None else None,
            body.scope, body.sort_order, body.visibility,
            1 if body.is_active else 0,
        ),
    )
    await db.commit()
    return {"ok": True, "id": field_id}


@router.put("/fields/{field_key}")
async def update_people_field(
    field_key: str,
    body: PeopleFieldDefinitionUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="manage_fields")
    updates = []
    params = []
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "options":
            updates.append("options_json = ?")
            params.append(json.dumps(value) if value is not None else None)
        elif key in {"required", "is_active"}:
            updates.append(f"{key} = ?")
            params.append(1 if value else 0)
        else:
            updates.append(f"{key} = ?")
            params.append(value)
    if not updates:
        return {"ok": True, "updated": False}
    updates.append("updated_at = NOW()")
    params.append(field_key)
    cursor = await db.execute(
        f"UPDATE people_field_definitions SET {', '.join(updates)} WHERE field_key = ?",
        params,
    )
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Field definition not found")
    return {"ok": True, "updated": True}


# ── /{person_id} routes (must come AFTER /fields) ─────────────────────────────

@router.get("/{person_id}")
async def get_person(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single person by ID."""
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    person = await _get_person_or_404(db, person_id, user_id)
    custom_fields = await _load_custom_fields(db, [person_id])
    person["custom_fields"] = custom_fields.get(person_id, {})
    return person


@router.put("/{person_id}")
async def update_person(
    person_id: str,
    body: PersonUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update a person."""
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)

    updates = []
    params = []
    if body.name is not None:
        updates.append("name = ?"); params.append(body.name)
    if body.relationship is not None:
        updates.append("relationship = ?"); params.append(body.relationship)
    if body.email is not None:
        updates.append("email = ?"); params.append(body.email)
    if body.phone is not None:
        updates.append("phone = ?"); params.append(body.phone)
    if body.birthday is not None:
        updates.append("birthday = ?"); params.append(body.birthday)
    if body.notes is not None:
        updates.append("notes = ?"); params.append(body.notes)
    if body.preferences is not None:
        updates.append("preferences = ?"); params.append(json.dumps(body.preferences))
    if body.visibility is not None:
        updates.append("visibility = ?"); params.append(body.visibility)
    circle = getattr(body, "circle", None)
    if circle is not None:
        if circle not in _VALID_CIRCLES:
            circle = "acquaintance"
        updates.append("circle = ?"); params.append(circle)
    if body.custom_fields is not None:
        await _upsert_custom_fields(db, person_id, body.custom_fields)

    if updates:
        params.append(person_id)
        sql = f"UPDATE people SET {', '.join(updates)}, updated_at = NOW() WHERE id = ?"
        await db.execute(sql, params)
        await db.commit()

    async with db.execute("SELECT * FROM people WHERE id = ?", (person_id,)) as cur:
        row = await cur.fetchone()
    person = _row_to_person(dict(row))
    custom_fields = await _load_custom_fields(db, [person_id])
    person["custom_fields"] = custom_fields.get(person_id, {})
    await _store_person_memory(db, user_id, person, "updated")
    await _recalc_health(db, person_id, user_id)
    await db.commit()
    await broadcaster.broadcast("all", "people:updated", {"id": person_id})
    return person


@router.delete("/{person_id}")
async def delete_person(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft-delete a person and archive their MemPalace facts."""
    await require_feature_access(db, user, feature="people", action="delete")
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    async with db.execute(
        f"SELECT id FROM people WHERE id = ? AND deleted = 0 AND {vis}",
        (person_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    await db.execute(
        "UPDATE people SET deleted = 1, updated_at = NOW() WHERE id = ?",
        (person_id,),
    )
    await db.commit()

    # Archive all MemPalace facts for this entity
    asyncio.ensure_future(_archive_person_mempalace(person_id, user_id))

    await broadcaster.broadcast("all", "people:deleted", {"id": person_id})
    return {"ok": True, "id": person_id}


async def _archive_person_mempalace(person_id: str, user_id: str) -> None:
    try:
        from memory_service import get_memory_service
        await get_memory_service().archive_by_entity(entity_id=person_id, user_id=user_id)
    except Exception as exc:
        import logging as _lg
        _lg.getLogger(__name__).debug("MemPalace archive failed for %s: %s", person_id, exc)


# ── Mark notifications read ───────────────────────────────────────────────────

@router.put("/{person_id}/mark-read")
async def mark_person_read(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Reset notification_count to 0 for a person."""
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    await db.execute(
        "UPDATE people SET notification_count = 0, updated_at = NOW() WHERE id = ? AND user_id = ?",
        (person_id, user_id),
    )
    await db.commit()
    return {"ok": True}


# ── Activities ────────────────────────────────────────────────────────────────

@router.get("/{person_id}/activities")
async def list_activities(
    person_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    cursor = await db.execute(
        "SELECT * FROM person_activities WHERE person_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (person_id, user_id, limit, offset),
    )
    rows = await cursor.fetchall()
    return {"activities": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{person_id}/activities")
async def add_activity(
    person_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    row_id = str(uuid.uuid4())
    activity_type = body.get("activity_type", "note")
    description = body.get("description", "")
    source = body.get("source", "manual")
    venue = body.get("venue")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    await db.execute(
        "INSERT INTO person_activities (id, person_id, user_id, activity_type, description, source, venue) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row_id, person_id, user_id, activity_type, description, source, venue),
    )
    # Increment contact_count and update last_contacted_at
    await db.execute(
        "UPDATE people SET contact_count = contact_count + 1, last_contacted_at = ?, updated_at = NOW() "
        "WHERE id = ? AND user_id = ?",
        (datetime.utcnow().isoformat() + "Z", person_id, user_id),
    )
    await db.commit()
    await _recalc_health(db, person_id, user_id)
    await db.commit()
    await broadcaster.broadcast("all", "people:updated", {"person_id": person_id})
    return {"ok": True, "id": row_id}


# ── Important dates ───────────────────────────────────────────────────────────

@router.get("/{person_id}/important-dates")
async def list_important_dates(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    cursor = await db.execute(
        "SELECT * FROM person_important_dates WHERE person_id = ? AND user_id = ? ORDER BY month, day",
        (person_id, user_id),
    )
    rows = await cursor.fetchall()
    return {"dates": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{person_id}/important-dates")
async def add_important_date(
    person_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    row_id = str(uuid.uuid4())
    label = body.get("label", "")
    date_type = body.get("date_type", "birthday")
    month = body.get("month")
    day = body.get("day")
    year = body.get("year")
    reminder_days_before = body.get("reminder_days_before", 7)
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    await db.execute(
        "INSERT INTO person_important_dates (id, person_id, user_id, label, date_type, month, day, year, reminder_days_before) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row_id, person_id, user_id, label, date_type, month, day, year, reminder_days_before),
    )
    await db.commit()
    await _recalc_health(db, person_id, user_id)
    await db.commit()
    return {"ok": True, "id": row_id}


# ── Gift ideas ────────────────────────────────────────────────────────────────

@router.get("/{person_id}/gift-ideas")
async def list_gift_ideas(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    cursor = await db.execute(
        "SELECT * FROM person_gift_ideas WHERE person_id = ? AND user_id = ? ORDER BY created_at DESC",
        (person_id, user_id),
    )
    rows = await cursor.fetchall()
    return {"gifts": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{person_id}/gift-ideas")
async def add_gift_idea(
    person_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    row_id = str(uuid.uuid4())
    description = body.get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    await db.execute(
        "INSERT INTO person_gift_ideas (id, person_id, user_id, description, status, price_hint, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row_id, person_id, user_id, description,
         body.get("status", "idea"), body.get("price_hint"), body.get("source", "manual")),
    )
    await db.commit()
    return {"ok": True, "id": row_id}


@router.put("/{person_id}/gift-ideas/{gift_id}")
async def update_gift_status(
    person_id: str,
    gift_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    status = body.get("status", "idea")
    await db.execute(
        "UPDATE person_gift_ideas SET status = ? WHERE id = ? AND person_id = ? AND user_id = ?",
        (status, gift_id, person_id, user_id),
    )
    await db.commit()
    return {"ok": True}


# ── Bucket list ───────────────────────────────────────────────────────────────

@router.get("/{person_id}/bucket-list")
async def list_bucket(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    cursor = await db.execute(
        "SELECT * FROM person_bucket_list WHERE person_id = ? AND user_id = ? ORDER BY created_at DESC",
        (person_id, user_id),
    )
    rows = await cursor.fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{person_id}/bucket-list")
async def add_bucket_item(
    person_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    row_id = str(uuid.uuid4())
    description = body.get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    await db.execute(
        "INSERT INTO person_bucket_list (id, person_id, user_id, description) VALUES (?, ?, ?, ?)",
        (row_id, person_id, user_id, description),
    )
    await db.commit()
    return {"ok": True, "id": row_id}


@router.put("/{person_id}/bucket-list/{item_id}/done")
async def mark_bucket_done(
    person_id: str,
    item_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    now = datetime.utcnow().isoformat() + "Z"
    await db.execute(
        "UPDATE person_bucket_list SET done = 1, done_at = ? WHERE id = ? AND person_id = ? AND user_id = ?",
        (now, item_id, person_id, user_id),
    )
    await db.commit()
    return {"ok": True}
