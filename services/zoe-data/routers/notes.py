"""
FastAPI router for notes.
Mounted at prefix="/api/notes" with tag "notes".
"""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from models import NoteCreate, NoteUpdate
from push import broadcaster

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _row_to_dict(row) -> dict:
    """Convert aiosqlite Row to dict, parsing tags JSON."""
    if row is None:
        return None
    d = dict(row)
    tags = d.get("tags")
    if tags is not None and isinstance(tags, str):
        try:
            d["tags"] = json.loads(tags) if tags.startswith("[") else [t.strip() for t in tags.split(",") if t.strip()]
        except (json.JSONDecodeError, AttributeError):
            d["tags"] = None
    if "deleted" in d and d["deleted"] is not None:
        d["deleted"] = bool(d["deleted"])
    return d


def _visibility_filter_sql() -> str:
    """SQL fragment: (visibility='family' OR user_id=?) AND deleted=0"""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


async def _store_note_memory(db, user_id: str, note: dict, action: str):
    """Write a note-derived fact to MemPalace through MemoryService.

    `db` is kept in the signature for call-site compatibility but is no
    longer used — the SQLite `memory_items` mirror has been retired in
    favour of MemPalace as the single source of truth.
    """
    title = note.get("title") or "Note"
    content = (note.get("content") or "")[:800]
    if not content:
        return
    fact = f"User {action} note titled '{title}': {content[:300]}"
    try:
        from memory_service import MemoryServiceError, get_memory_service
        try:
            await get_memory_service().ingest(
                fact,
                user_id=user_id,
                source=f"note_{action}",
                memory_type="note",
                confidence=0.75,
                status="approved",
                tags=["note", action],
                entity_type="note",
                entity_id=note.get("id"),
            )
        except MemoryServiceError as exc:
            # MemoryService rejections (PII, empty text, missing user) are
            # explicit; log and move on.
            import logging as _lg
            _lg.getLogger(__name__).info("notes: memory ingest skipped: %s", exc)
    except Exception:
        pass


@router.get("/", response_model=dict)
async def list_notes(
    category: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List notes with optional category filter. Returns {notes: [...], count}."""
    await require_feature_access(db, user, feature="notes", action="read")
    user_id = user["user_id"]
    conditions = [_visibility_filter_sql()]
    params: list = [user_id]

    if category:
        conditions.append("category = ?")
        params.append(category)

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM notes WHERE {where} ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    notes = [_row_to_dict(r) for r in rows]

    count_sql = f"SELECT COUNT(*) FROM notes WHERE {where}"
    count_params = [user_id]
    if category:
        count_params.append(category)
    count_cursor = await db.execute(count_sql, count_params)
    count_row = await count_cursor.fetchone()
    count = count_row[0] if count_row else 0

    return {"notes": notes, "count": count}


@router.post("/", response_model=dict)
async def create_note(
    payload: NoteCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new note."""
    await require_feature_access(db, user, feature="notes", action="create")
    user_id = user["user_id"]
    note_id = str(uuid.uuid4())
    tags_json = json.dumps(payload.tags) if payload.tags else None

    await db.execute(
        """INSERT INTO notes (id, user_id, title, content, category, tags, visibility, deleted)
         VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            note_id,
            user_id,
            payload.title,
            payload.content,
            payload.category,
            tags_json,
            payload.visibility,
        ),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM notes WHERE id = ?", [note_id])
    row = await cursor.fetchone()
    note = _row_to_dict(row)
    await _store_note_memory(db, user_id, note, "created")
    await db.commit()

    await broadcaster.broadcast("notes", "note_created", note)
    return note


@router.put("/{note_id}", response_model=dict)
async def update_note(
    note_id: str,
    payload: NoteUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing note."""
    await require_feature_access(db, user, feature="notes", action="update")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM notes WHERE " + where,
        [user_id, note_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "tags":
            updates.append("tags = ?")
            params.append(json.dumps(value) if value else None)
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        return _row_to_dict(row)

    updates.append("updated_at = datetime('now')")
    params.append(note_id)
    await db.execute(
        f"UPDATE notes SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM notes WHERE id = ?", [note_id])
    row = await cursor.fetchone()
    note = _row_to_dict(row)
    await _store_note_memory(db, user_id, note, "updated")
    await db.commit()

    await broadcaster.broadcast("notes", "note_updated", note)
    return note


@router.delete("/{note_id}", response_model=dict)
async def delete_note(
    note_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a note."""
    await require_feature_access(db, user, feature="notes", action="delete")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM notes WHERE " + where,
        [user_id, note_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")

    await db.execute(
        "UPDATE notes SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        [note_id],
    )
    await db.commit()

    await broadcaster.broadcast("notes", "note_deleted", {"id": note_id})
    return {"ok": True, "id": note_id}
