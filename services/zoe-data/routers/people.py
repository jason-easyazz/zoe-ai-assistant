"""
People/contacts API router — v3 (People CRM with relationships).

Changes from v2:
- context (personal|work) + circle now 3 tiers (inner/circle/public)
- person_relationships CRUD: GET/POST/PUT/DELETE /{person_id}/relationships
- GET /relationship-types for UI dropdowns
- is_partial, how_we_met, first_met_date, introduced_by_person_id fields
- _store_person_memory updated to emit context+circle in fact text
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_admin
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

_VALID_CIRCLES = {"inner", "circle", "public"}
_VALID_CONTEXTS = {"personal", "work"}

# Typed relationship vocabulary — no extra DB table needed.
# Each entry: (rel_type_key, label_a_to_b, label_b_to_a)
RELATIONSHIP_TYPES: dict[str, list[tuple[str, str, str]]] = {
    "love": [
        ("partner",    "Partner",    "Partner"),
        ("spouse",     "Spouse",     "Spouse"),
        ("ex",         "Ex-partner", "Ex-partner"),
    ],
    "family": [
        ("parent",      "Parent",      "Child"),
        ("sibling",     "Sibling",     "Sibling"),
        ("grandparent", "Grandparent", "Grandchild"),
        ("aunt_uncle",  "Aunt/Uncle",  "Niece/Nephew"),
        ("cousin",      "Cousin",      "Cousin"),
        ("in_law",      "In-law",      "In-law"),
    ],
    "friend": [
        ("friend",      "Friend",       "Friend"),
        ("best_friend", "Best friend",  "Best friend"),
        ("met_through", "Met through",  "Introduced to"),
    ],
    "work": [
        ("colleague",   "Colleague",    "Colleague"),
        ("boss",        "Boss",         "Report"),
        ("mentor",      "Mentor",       "Mentee"),
        ("client",      "Client",       "Provider"),
    ],
}
# Groups whose members imply context='personal' vs 'work'
_PERSONAL_GROUPS = {"love", "family", "friend"}
_WORK_GROUPS = {"work"}


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
        "circle": d.get("circle", "circle"),
        "context": d.get("context", "personal"),
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
        "is_partial": bool(d.get("is_partial", 0)),
        "how_we_met": d.get("how_we_met"),
        "first_met_date": d.get("first_met_date"),
        "introduced_by_person_id": d.get("introduced_by_person_id"),
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
    circle = person.get("circle", "circle")
    context = person.get("context", "personal")
    fact = f"Person in contacts: {summary}. Context: {context}, Tier: {circle}. {notes[:200]}".strip().rstrip(".")
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
    context: Optional[str] = Query(None),
    include_partial: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List people with optional search, circle/context filter, limit, offset."""
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    params = [user_id]
    filters = [f"deleted = 0 AND {vis}"]

    if not include_partial:
        filters.append("(is_partial = 0 OR is_partial IS NULL)")

    if circle and circle in _VALID_CIRCLES:
        filters.append("circle = ?")
        params.append(circle)

    if context and context in _VALID_CONTEXTS:
        filters.append("context = ?")
        params.append(context)

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
    circle = getattr(body, "circle", "circle") or "circle"
    if circle not in _VALID_CIRCLES:
        circle = "circle"
    context = getattr(body, "context", "personal") or "personal"
    if context not in _VALID_CONTEXTS:
        context = "personal"

    await db.execute(
        """
        INSERT INTO people (id, user_id, name, relationship, circle, context, email, phone, birthday,
                            notes, preferences, visibility, is_partial, how_we_met, first_met_date,
                            introduced_by_person_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id, user_id, body.name, body.relationship, circle, context,
            body.email, body.phone, body.birthday, body.notes, pref, body.visibility,
            1 if getattr(body, "is_partial", False) else 0,
            getattr(body, "how_we_met", None),
            getattr(body, "first_met_date", None),
            getattr(body, "introduced_by_person_id", None),
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

    await broadcaster.broadcast("all", "people:created", person, user_id=user_id)
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


# ── /relationship-types (static, no DB) ───────────────────────────────────────

@router.get("/relationship-types")
async def get_relationship_types(
    user: dict = Depends(get_current_user),
):
    """Return grouped relationship type vocabulary for UI dropdowns."""
    return {
        "types": {
            group: [
                {"key": key, "label_a": lbl_a, "label_b": lbl_b}
                for key, lbl_a, lbl_b in entries
            ]
            for group, entries in RELATIONSHIP_TYPES.items()
        }
    }


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
    _admin: dict = Depends(require_admin),
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
    _admin: dict = Depends(require_admin),
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


@router.delete("/fields/{field_key}", dependencies=[Depends(require_admin)])
async def delete_field_schema(field_key: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="manage_fields")
    cursor = await db.execute("DELETE FROM people_field_definitions WHERE field_key = ?", (field_key,))
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Field definition not found")
    return {"deleted": True}


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
            circle = "circle"
        updates.append("circle = ?"); params.append(circle)
    context = getattr(body, "context", None)
    if context is not None:
        if context not in _VALID_CONTEXTS:
            context = "personal"
        updates.append("context = ?"); params.append(context)
    if getattr(body, "how_we_met", None) is not None:
        updates.append("how_we_met = ?"); params.append(body.how_we_met)
    if getattr(body, "first_met_date", None) is not None:
        updates.append("first_met_date = ?"); params.append(body.first_met_date)
    if getattr(body, "introduced_by_person_id", None) is not None:
        updates.append("introduced_by_person_id = ?"); params.append(body.introduced_by_person_id)
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
    await broadcaster.broadcast("all", "people:updated", {"id": person_id}, user_id=user_id)
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

    await broadcaster.broadcast("all", "people:deleted", {"id": person_id}, user_id=user_id)
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
    await broadcaster.broadcast("all", "people:updated", {"person_id": person_id}, user_id=user_id)
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


# ── Person relationships ───────────────────────────────────────────────────────

def _rel_lookup(rel_type: str) -> tuple[str, str, str] | None:
    """Find (rel_type, label_a_to_b, label_b_to_a) for a given rel_type key."""
    for group, entries in RELATIONSHIP_TYPES.items():
        for key, lbl_a, lbl_b in entries:
            if key == rel_type:
                return (group, lbl_a, lbl_b)
    return None


@router.get("/{person_id}/relationships")
async def list_relationships(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List all relationship edges for a person, both sides resolved."""
    await require_feature_access(db, user, feature="people", action="read")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)

    cursor = await db.execute(
        "SELECT * FROM person_relationships WHERE user_id = ? AND (person_a_id = ? OR person_b_id = ?)",
        (user_id, person_id, person_id),
    )
    rows = await cursor.fetchall()

    grouped: dict[str, list] = {"love": [], "family": [], "friend": [], "work": []}
    for row in rows:
        d = dict(row)
        is_a = d["person_a_id"] == person_id
        other_id = d["person_b_id"] if is_a else d["person_a_id"]
        label = d["rel_a_to_b"] if is_a else d["rel_b_to_a"]

        # Resolve name
        async with db.execute(
            "SELECT name, is_partial, circle, context FROM people WHERE id = ? AND deleted = 0",
            (other_id,),
        ) as cur:
            other_row = await cur.fetchone()
        if not other_row:
            continue
        od = dict(other_row)
        group = d.get("rel_group", "friend")
        if group not in grouped:
            grouped[group] = []
        grouped[group].append({
            "rel_id": d["id"],
            "person_id": other_id,
            "name": od["name"],
            "label": label,
            "rel_type": d["rel_type"],
            "rel_group": group,
            "is_partial": bool(od.get("is_partial", 0)),
            "circle": od.get("circle"),
            "context": od.get("context"),
            "notes": d.get("notes"),
        })

    return {"relationships": grouped}


@router.post("/{person_id}/relationships")
async def add_relationship(
    person_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a typed relationship edge.

    Body:
      other_person_id   — UUID of existing contact, OR
      other_person_name — name string (creates a partial contact if create_partial=true)
      rel_type          — relationship type key (see /relationship-types)
      notes             — optional
      create_partial    — bool, default false
    """
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)

    rel_type = body.get("rel_type", "")
    lookup = _rel_lookup(rel_type)
    if not lookup:
        raise HTTPException(status_code=400, detail=f"Unknown rel_type: {rel_type!r}")
    group, lbl_a, lbl_b = lookup

    # Resolve other person
    other_id = body.get("other_person_id")
    if not other_id:
        name = (body.get("other_person_name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="other_person_id or other_person_name required")
        if body.get("create_partial", False):
            # Create a partial stub
            other_id = str(uuid.uuid4())
            inferred_context = "work" if group in _WORK_GROUPS else "personal"
            await db.execute(
                "INSERT INTO people (id, user_id, name, circle, context, visibility, is_partial) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (other_id, user_id, name, "circle", inferred_context, "family", 1),
            )
            await db.commit()
        else:
            # Search by name
            cursor = await db.execute(
                "SELECT id FROM people WHERE user_id = ? AND name = ? AND deleted = 0 LIMIT 1",
                (user_id, name),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Person '{name}' not found. Use create_partial=true to create a stub.")
            other_id = dict(row)["id"]
    else:
        # Verify person B is owned by this user or is family-visible
        async with db.execute(
            "SELECT user_id, visibility FROM people WHERE id = ? AND deleted = 0",
            (other_id,),
        ) as cur:
            person_b_row = await cur.fetchone()
        if not person_b_row:
            raise HTTPException(status_code=404, detail="Linked person not found")
        person_b = dict(person_b_row)
        if person_b["user_id"] != user_id and person_b.get("visibility") != "family":
            raise HTTPException(status_code=403, detail="You do not have permission to link to that person")

    # Prevent self-link
    if other_id == person_id:
        raise HTTPException(status_code=400, detail="Cannot link a person to themselves")

    rel_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    try:
        await db.execute(
            """INSERT INTO person_relationships
               (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, rel_group, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rel_id, user_id, person_id, other_id, rel_type, lbl_a, lbl_b, group,
             body.get("notes"), now, now),
        )
        await db.commit()
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Relationship already exists")
        raise

    # Auto-update context of the other person based on rel_group
    inferred_ctx = "work" if group in _WORK_GROUPS else "personal"
    await db.execute(
        "UPDATE people SET context = ? WHERE id = ? AND user_id = ?",
        (inferred_ctx, other_id, user_id),
    )
    await db.commit()

    await broadcaster.broadcast("all", "people:updated", {"id": person_id})
    return {"ok": True, "rel_id": rel_id, "other_person_id": other_id}


@router.put("/{person_id}/relationships/{rel_id}")
async def update_relationship(
    person_id: str,
    rel_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update relationship notes or type."""
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)

    updates = []
    params = []
    if "notes" in body:
        updates.append("notes = ?"); params.append(body["notes"])
    if "rel_type" in body:
        lookup = _rel_lookup(body["rel_type"])
        if not lookup:
            raise HTTPException(status_code=400, detail=f"Unknown rel_type: {body['rel_type']!r}")
        group, lbl_a, lbl_b = lookup
        updates.extend(["rel_type = ?", "rel_a_to_b = ?", "rel_b_to_a = ?", "rel_group = ?"])
        params.extend([body["rel_type"], lbl_a, lbl_b, group])

    if not updates:
        return {"ok": True, "updated": False}

    updates.append("updated_at = NOW()")
    params.extend([rel_id, user_id])
    await db.execute(
        f"UPDATE person_relationships SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        params,
    )
    await db.commit()
    return {"ok": True, "updated": True}


@router.delete("/{person_id}/relationships/{rel_id}")
async def delete_relationship(
    person_id: str,
    rel_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Remove a relationship edge."""
    await require_feature_access(db, user, feature="people", action="update")
    user_id = user["user_id"]
    await _get_person_or_404(db, person_id, user_id)
    await db.execute(
        "DELETE FROM person_relationships WHERE id = ? AND user_id = ?",
        (rel_id, user_id),
    )
    await db.commit()
    return {"ok": True}


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
