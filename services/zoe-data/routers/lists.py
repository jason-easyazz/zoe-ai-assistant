"""
FastAPI router for lists and list items.
Mounted at prefix="/api/lists" with tag "lists".
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from models import ListCreate, ListUpdate, ListItemCreate, ListItemUpdate
from push import broadcaster

router = APIRouter(prefix="/api/lists", tags=["lists"])

VALID_LIST_TYPES = ["shopping", "personal", "work", "bucket", "tasks", "work_todos", "personal_todos"]

_LIST_TYPE_ALIASES = {"work_todos": "work", "personal_todos": "personal"}


def _normalize_list_type(lt: str) -> str:
    return _LIST_TYPE_ALIASES.get(lt, lt)


def _row_to_dict(row) -> dict:
    """Convert aiosqlite Row to dict."""
    if row is None:
        return None
    return dict(row)


@router.get("/types")
async def get_list_types(user: dict = Depends(get_current_user)):
    """Return available list types."""
    return {"types": VALID_LIST_TYPES}


@router.get("/{list_type}")
async def list_lists(
    list_type: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List all lists of a type. Only shows lists where visibility='family' or user_id matches."""
    await require_feature_access(db, user, feature="lists", action="read")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at
        FROM lists
        WHERE list_type = ? AND deleted = 0
          AND (visibility = 'family' OR user_id = ?)
        ORDER BY updated_at DESC
        """,
        (list_type, user_id),
    )
    rows = await cursor.fetchall()
    lists = [_row_to_dict(r) for r in rows]
    return {"lists": lists}


@router.post("/{list_type}")
async def create_list(
    list_type: str,
    body: ListCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new list."""
    await require_feature_access(db, user, feature="lists", action="create")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    list_id = str(uuid.uuid4())
    user_id = user["user_id"]

    await db.execute(
        """
        INSERT INTO lists (id, user_id, name, list_type, description, visibility)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            list_id,
            user_id,
            body.name,
            list_type,
            body.description or "",
            body.visibility,
        ),
    )
    await db.commit()

    cursor = await db.execute(
        "SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at FROM lists WHERE id = ?",
        (list_id,),
    )
    row = await cursor.fetchone()
    result = _row_to_dict(row)

    await broadcaster.broadcast("lists", "list_updated", {"action": "created", "list": result})
    return result


@router.get("/{list_type}/{list_id}")
async def get_list(
    list_type: str,
    list_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single list with its items. Items sorted by sort_order, then created_at."""
    await require_feature_access(db, user, feature="lists", action="read")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at
        FROM lists
        WHERE id = ? AND list_type = ? AND deleted = 0
          AND (visibility = 'family' OR user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    list_data = _row_to_dict(row)

    items_cursor = await db.execute(
        """
        SELECT id, list_id, text, completed, priority, category, quantity, sort_order,
               parent_id, assigned_to, created_at, updated_at
        FROM list_items
        WHERE list_id = ? AND deleted = 0
        ORDER BY sort_order ASC, created_at ASC
        """,
        (list_id,),
    )
    item_rows = await items_cursor.fetchall()
    list_data["items"] = [_row_to_dict(r) for r in item_rows]

    return list_data


@router.put("/{list_type}/{list_id}")
async def update_list(
    list_type: str,
    list_id: str,
    body: ListUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update a list."""
    await require_feature_access(db, user, feature="lists", action="update")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT id FROM lists
        WHERE id = ? AND list_type = ? AND deleted = 0
          AND (visibility = 'family' OR user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    updates = []
    params = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.description is not None:
        updates.append("description = ?")
        params.append(body.description)
    if body.visibility is not None:
        updates.append("visibility = ?")
        params.append(body.visibility)

    if not updates:
        cursor = await db.execute(
            "SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at FROM lists WHERE id = ?",
            (list_id,),
        )
        row = await cursor.fetchone()
        return _row_to_dict(row)

    updates.append("updated_at = datetime('now')")
    params.append(list_id)
    await db.execute(
        f"UPDATE lists SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()

    cursor = await db.execute(
        "SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at FROM lists WHERE id = ?",
        (list_id,),
    )
    row = await cursor.fetchone()
    result = _row_to_dict(row)
    await broadcaster.broadcast("lists", "list_updated", {"action": "updated", "list": result})
    return result


@router.delete("/{list_type}/{list_id}")
async def delete_list(
    list_type: str,
    list_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a list."""
    await require_feature_access(db, user, feature="lists", action="delete")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT id FROM lists
        WHERE id = ? AND list_type = ? AND deleted = 0
          AND (visibility = 'family' OR user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    await db.execute(
        "UPDATE lists SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        (list_id,),
    )
    await db.commit()

    await broadcaster.broadcast("lists", "list_updated", {"action": "deleted", "list_id": list_id})
    return {"ok": True}


@router.post("/{list_type}/{list_id}/items")
async def add_item(
    list_type: str,
    list_id: str,
    body: ListItemCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Add an item to a list."""
    await require_feature_access(db, user, feature="lists", action="add_item")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT id FROM lists
        WHERE id = ? AND list_type = ? AND deleted = 0
          AND (visibility = 'family' OR user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    item_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO list_items (id, list_id, text, priority, category, quantity, parent_id, assigned_to)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            list_id,
            body.text,
            body.priority,
            body.category or "",
            body.quantity or "",
            body.parent_id,
            body.assigned_to,
        ),
    )
    await db.commit()

    cursor = await db.execute(
        """
        SELECT id, list_id, text, completed, priority, category, quantity, sort_order,
               parent_id, assigned_to, created_at, updated_at
        FROM list_items WHERE id = ?
        """,
        (item_id,),
    )
    row = await cursor.fetchone()
    result = _row_to_dict(row)

    await broadcaster.broadcast(
        "lists",
        "list_updated",
        {"action": "item_added", "list_id": list_id, "item": result},
    )
    return result


@router.get("/{list_type}/{list_id}/items")
async def list_items(
    list_type: str,
    list_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List items of a list. Sorted by sort_order, then created_at."""
    await require_feature_access(db, user, feature="lists", action="read")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT l.id FROM lists l
        WHERE l.id = ? AND l.list_type = ? AND l.deleted = 0
          AND (l.visibility = 'family' OR l.user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    items_cursor = await db.execute(
        """
        SELECT id, list_id, text, completed, priority, category, quantity, sort_order,
               parent_id, assigned_to, created_at, updated_at
        FROM list_items
        WHERE list_id = ? AND deleted = 0
        ORDER BY sort_order ASC, created_at ASC
        """,
        (list_id,),
    )
    item_rows = await items_cursor.fetchall()
    items = [_row_to_dict(r) for r in item_rows]
    return {"items": items}


@router.put("/{list_type}/{list_id}/items/{item_id}")
async def update_item(
    list_type: str,
    list_id: str,
    item_id: str,
    body: ListItemUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update a list item."""
    await require_feature_access(db, user, feature="lists", action="update_item")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT l.id FROM lists l
        WHERE l.id = ? AND l.list_type = ? AND l.deleted = 0
          AND (l.visibility = 'family' OR l.user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    cursor = await db.execute(
        "SELECT id FROM list_items WHERE id = ? AND list_id = ? AND deleted = 0",
        (item_id, list_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    updates = []
    params = []
    if body.text is not None:
        updates.append("text = ?")
        params.append(body.text)
    if body.completed is not None:
        updates.append("completed = ?")
        params.append(1 if body.completed else 0)
    if body.priority is not None:
        updates.append("priority = ?")
        params.append(body.priority)
    if body.category is not None:
        updates.append("category = ?")
        params.append(body.category)
    if body.quantity is not None:
        updates.append("quantity = ?")
        params.append(body.quantity)
    if body.sort_order is not None:
        updates.append("sort_order = ?")
        params.append(body.sort_order)
    if body.assigned_to is not None:
        updates.append("assigned_to = ?")
        params.append(body.assigned_to)

    if not updates:
        cursor = await db.execute(
            """
            SELECT id, list_id, text, completed, priority, category, quantity, sort_order,
                   parent_id, assigned_to, created_at, updated_at
            FROM list_items WHERE id = ?
            """,
            (item_id,),
        )
        row = await cursor.fetchone()
        return _row_to_dict(row)

    updates.append("updated_at = datetime('now')")
    params.extend([item_id, list_id])
    await db.execute(
        f"UPDATE list_items SET {', '.join(updates)} WHERE id = ? AND list_id = ?",
        params,
    )
    await db.commit()

    cursor = await db.execute(
        """
        SELECT id, list_id, text, completed, priority, category, quantity, sort_order,
               parent_id, assigned_to, created_at, updated_at
        FROM list_items WHERE id = ?
        """,
        (item_id,),
    )
    row = await cursor.fetchone()
    result = _row_to_dict(row)
    await broadcaster.broadcast(
        "lists",
        "list_updated",
        {"action": "item_updated", "list_id": list_id, "item": result},
    )
    return result


@router.delete("/{list_type}/{list_id}/items/{item_id}")
async def delete_item(
    list_type: str,
    list_id: str,
    item_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a list item."""
    await require_feature_access(db, user, feature="lists", action="delete_item")
    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown list type: {list_type}")
    list_type = _normalize_list_type(list_type)

    user_id = user["user_id"]
    cursor = await db.execute(
        """
        SELECT l.id FROM lists l
        WHERE l.id = ? AND l.list_type = ? AND l.deleted = 0
          AND (l.visibility = 'family' OR l.user_id = ?)
        """,
        (list_id, list_type, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    cursor = await db.execute(
        "SELECT id FROM list_items WHERE id = ? AND list_id = ? AND deleted = 0",
        (item_id, list_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    await db.execute(
        "UPDATE list_items SET deleted = 1, updated_at = datetime('now') WHERE id = ? AND list_id = ?",
        (item_id, list_id),
    )
    await db.commit()

    await broadcaster.broadcast(
        "lists",
        "list_updated",
        {"action": "item_deleted", "list_id": list_id, "item_id": item_id},
    )
    return {"ok": True}
