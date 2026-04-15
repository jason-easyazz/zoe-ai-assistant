"""
People/contacts API router.
"""

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from models import (
    PersonCreate,
    PersonUpdate,
    PeopleFieldDefinitionCreate,
    PeopleFieldDefinitionUpdate,
)
from push import broadcaster

router = APIRouter(prefix="/api/people", tags=["people"])


def _row_to_person(row) -> dict:
    """Convert DB row to person dict with preferences as parsed JSON."""
    pref = row["preferences"]
    if pref and isinstance(pref, str):
        try:
            pref = json.loads(pref)
        except json.JSONDecodeError:
            pref = None
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "relationship": row["relationship"],
        "email": row["email"],
        "phone": row["phone"],
        "birthday": row["birthday"],
        "notes": row["notes"],
        "preferences": pref,
        "visibility": row["visibility"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
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
        value = row["value_json"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        by_person.setdefault(row["person_id"], {})[row["field_key"]] = value
    return by_person


async def _upsert_custom_fields(db, person_id: str, custom_fields: Optional[dict]) -> None:
    if not custom_fields:
        return
    for field_key, value in custom_fields.items():
        payload = json.dumps(value)
        await db.execute(
            """INSERT INTO people_field_values (id, person_id, field_key, value_json, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(person_id, field_key)
               DO UPDATE SET value_json=excluded.value_json, updated_at=datetime('now')""",
            (str(uuid.uuid4()), person_id, field_key, payload),
        )


async def _store_person_memory(db, user_id: str, person: dict, action: str):
    import asyncio
    summary = f"{person.get('name')} ({person.get('relationship') or 'contact'})"
    await db.execute(
        """INSERT INTO memory_items
           (id, user_id, memory_type, title, content, entity_type, entity_id, confidence,
            source_type, source_id, source_excerpt, visibility, status)
           VALUES (?, ?, 'person', ?, ?, 'person', ?, 0.85, 'people', ?, ?, ?, 'approved')""",
        (
            str(uuid.uuid4()),
            user_id,
            f"{action.title()} person profile",
            summary,
            person.get("id"),
            person.get("id"),
            summary[:180],
            person.get("visibility") or "family",
        ),
    )
    # Mirror to MemPalace so agent memory stays current
    try:
        from pi_agent import _mempalace_add  # type: ignore[import]
        notes = person.get("notes") or ""
        fact = f"Person in contacts: {summary}. {notes[:200]}".strip().rstrip(".")
        asyncio.ensure_future(_mempalace_add(fact, user_id=user_id, tags=["person", action]))
    except Exception:
        pass


def _visibility_filter_sql() -> str:
    """SQL fragment: family OR user matches."""
    return "(visibility = 'family' OR user_id = ?)"


@router.get("/")
async def list_people(
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List people with optional search, limit, offset."""
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    params = [user_id]

    if search:
        sql = f"""
            SELECT * FROM people
            WHERE deleted = 0 AND {vis}
            AND (name LIKE ? OR email LIKE ? OR phone LIKE ? OR relationship LIKE ?)
            ORDER BY name
            LIMIT ? OFFSET ?
        """
        q = f"%{search}%"
        params.extend([q, q, q, q, limit, offset])
    else:
        sql = f"""
            SELECT * FROM people
            WHERE deleted = 0 AND {vis}
            ORDER BY name
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    count_sql = f"SELECT COUNT(*) FROM people WHERE deleted = 0 AND {vis}"
    count_params = [user_id]
    if search:
        q = f"%{search}%"
        count_sql += " AND (name LIKE ? OR email LIKE ? OR phone LIKE ? OR relationship LIKE ?)"
        count_params.extend([q, q, q, q])
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
    user_id = user["user_id"]
    person_id = str(uuid.uuid4())
    pref = json.dumps(body.preferences) if body.preferences is not None else None

    await db.execute(
        """
        INSERT INTO people (id, user_id, name, relationship, email, phone, birthday, notes, preferences, visibility)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id,
            user_id,
            body.name,
            body.relationship,
            body.email,
            body.phone,
            body.birthday,
            body.notes,
            pref,
            body.visibility,
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


@router.get("/{person_id}")
async def get_person(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single person by ID."""
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    async with db.execute(
        f"SELECT * FROM people WHERE id = ? AND deleted = 0 AND {vis}",
        (person_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    person = _row_to_person(dict(row))
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
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    async with db.execute(
        f"SELECT * FROM people WHERE id = ? AND deleted = 0 AND {vis}",
        (person_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    updates = []
    params = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.relationship is not None:
        updates.append("relationship = ?")
        params.append(body.relationship)
    if body.email is not None:
        updates.append("email = ?")
        params.append(body.email)
    if body.phone is not None:
        updates.append("phone = ?")
        params.append(body.phone)
    if body.birthday is not None:
        updates.append("birthday = ?")
        params.append(body.birthday)
    if body.notes is not None:
        updates.append("notes = ?")
        params.append(body.notes)
    if body.preferences is not None:
        updates.append("preferences = ?")
        params.append(json.dumps(body.preferences))
    if body.visibility is not None:
        updates.append("visibility = ?")
        params.append(body.visibility)
    if body.custom_fields is not None:
        await _upsert_custom_fields(db, person_id, body.custom_fields)

    if not updates:
        async with db.execute("SELECT * FROM people WHERE id = ?", (person_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_person(dict(row))

    params.append(person_id)
    sql = f"UPDATE people SET {', '.join(updates)}, updated_at = datetime('now') WHERE id = ?"
    await db.execute(sql, params)
    await db.commit()

    async with db.execute("SELECT * FROM people WHERE id = ?", (person_id,)) as cur:
        row = await cur.fetchone()
    person = _row_to_person(dict(row))
    custom_fields = await _load_custom_fields(db, [person_id])
    person["custom_fields"] = custom_fields.get(person_id, {})
    await _store_person_memory(db, user_id, person, "updated")
    await db.commit()
    await broadcaster.broadcast("all", "people:updated", person)
    return person


@router.delete("/{person_id}")
async def delete_person(
    person_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a person."""
    user_id = user["user_id"]
    vis = _visibility_filter_sql()
    async with db.execute(
        f"SELECT * FROM people WHERE id = ? AND deleted = 0 AND {vis}",
        (person_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    await db.execute(
        "UPDATE people SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        (person_id,),
    )
    await db.commit()

    await broadcaster.broadcast("all", "people:deleted", {"id": person_id})
    return {"ok": True, "id": person_id}


@router.get("/fields")
async def list_people_fields(
    active_only: bool = Query(True),
    db=Depends(get_db),
):
    sql = """SELECT * FROM people_field_definitions
             WHERE (? = 0 OR is_active = 1)
             ORDER BY sort_order, label"""
    cursor = await db.execute(sql, (1 if active_only else 0,))
    rows = await cursor.fetchall()
    fields = []
    for row in rows:
        options = row["options_json"]
        if options and isinstance(options, str):
            try:
                options = json.loads(options)
            except json.JSONDecodeError:
                options = None
        fields.append(
            {
                "id": row["id"],
                "field_key": row["field_key"],
                "label": row["label"],
                "field_type": row["field_type"],
                "required": bool(row["required"]),
                "options": options,
                "scope": row["scope"],
                "sort_order": row["sort_order"],
                "visibility": row["visibility"],
                "is_active": bool(row["is_active"]),
            }
        )
    return {"fields": fields, "count": len(fields)}


@router.post("/fields")
async def create_people_field(
    body: PeopleFieldDefinitionCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if user.get("role") not in {"admin", "owner"} and user.get("user_id") != "family-admin":
        raise HTTPException(status_code=403, detail="Only admins can create field definitions")
    field_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO people_field_definitions
           (id, field_key, label, field_type, required, options_json, scope, sort_order, visibility, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            field_id,
            body.field_key,
            body.label,
            body.field_type,
            1 if body.required else 0,
            json.dumps(body.options) if body.options is not None else None,
            body.scope,
            body.sort_order,
            body.visibility,
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
    if user.get("role") not in {"admin", "owner"} and user.get("user_id") != "family-admin":
        raise HTTPException(status_code=403, detail="Only admins can update field definitions")
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
    updates.append("updated_at = datetime('now')")
    params.append(field_key)
    cursor = await db.execute(
        f"UPDATE people_field_definitions SET {', '.join(updates)} WHERE field_key = ?",
        params,
    )
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Field definition not found")
    return {"ok": True, "updated": True}
