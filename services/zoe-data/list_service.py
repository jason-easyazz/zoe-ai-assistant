"""Single canonical list writer.

One INSERT site each for the `lists` and `list_items` tables, shared by every
Wave-3 writer (the voice/direct executor in ``intent_router``, the
``list_add_item`` MCP tool, and the ``/api/lists`` router). Callers keep their
own feature gating, notification channel (``_notify_ui`` vs the push
broadcaster), response formatting, and commit/re-read — those DIFFER per caller
and preserving them is how observable behaviour stays identical. This module
owns ONLY the row writes plus the shared resolve-or-create mechanics.

Both schemas (see alembic 0001_initial_schema.py) are written as the full
superset of the trio's columns; voice-path callers that only supply a subset
get the same NULLs / defaults their narrower INSERTs produced (``priority``
defaults to the DB's own ``'normal'``).

Follow-up (out of scope here): skybridge_service.py and pending_suggestions.py
still carry their own list INSERTs.
"""

from __future__ import annotations

import uuid
from typing import Optional

# The voice/MCP rule for a freshly auto-created list's visibility. Callers pass
# the result explicitly so the choice stays visible at the call site.
PERSONAL_LIST_TYPES = frozenset({"personal", "tasks", "shopping"})


def default_visibility(list_type: str) -> str:
    """Visibility the voice/MCP add paths give a list they auto-create."""
    return "personal" if list_type in PERSONAL_LIST_TYPES else "family"


async def create_list_record(
    db,
    *,
    user_id: str,
    name: str,
    list_type: str,
    visibility: str,
    description: Optional[str] = None,
) -> dict:
    """Insert one row into ``lists`` and return a record dict.

    Takes an already-open ``db`` handle (AsyncpgCompat / aiosqlite style) and
    issues the single canonical INSERT with ``?`` placeholders. Does NOT gate
    access, notify the UI, or commit (asyncpg auto-commits; ``db.commit()`` is
    a no-op) — those are the caller's job. Callers that re-read the row for
    their response may ignore the returned dict.
    """
    list_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO lists (id, user_id, name, list_type, description, visibility)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (list_id, user_id, name, list_type, description, visibility),
    )
    return {
        "id": list_id,
        "user_id": user_id,
        "name": name,
        "list_type": list_type,
        "description": description,
        "visibility": visibility,
    }


async def create_item_record(
    db,
    *,
    list_id: str,
    text: str,
    priority: str = "normal",
    category: Optional[str] = None,
    quantity: Optional[str] = None,
    parent_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
) -> dict:
    """Insert one row into ``list_items`` and return a record dict.

    Same contract as :func:`create_list_record`: row write only, no access
    checks, no notify, no commit. ``priority`` defaults to ``'normal'`` — the
    DB default the narrower voice/MCP INSERTs relied on by omitting the column.
    """
    item_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO list_items (id, list_id, text, priority, category, quantity, parent_id, assigned_to)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (item_id, list_id, text, priority, category, quantity, parent_id, assigned_to),
    )
    return {
        "id": item_id,
        "list_id": list_id,
        "text": text,
        "priority": priority,
        "category": category,
        "quantity": quantity,
        "parent_id": parent_id,
        "assigned_to": assigned_to,
    }


async def find_list_id(db, *, user_id: str, list_type: str, name: str) -> Optional[str]:
    """Resolve a list by type + exact name, family lists first.

    The shared voice/MCP resolution: the user's own lists and family-visible
    lists both match; a family list wins a name tie so household adds converge
    on the shared list.
    """
    cursor = await db.execute(
        "SELECT id FROM lists WHERE list_type=? AND name=? AND deleted=0"
        " AND (user_id=? OR visibility='family')"
        " ORDER BY CASE WHEN visibility='family' THEN 0 ELSE 1 END LIMIT 1",
        (list_type, name, user_id),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def _find_recent_duplicate(db, *, list_id: str, text: str, window_s: int) -> Optional[str]:
    # created_at is a TEXT column, so it must be cast before the timestamp
    # comparison — a bare `created_at > now() - interval` throws `operator does
    # not exist: text > timestamp` on Postgres, which silently dropped the whole
    # direct add to the mcporter fallback (live 2026-07-08: 69 such errors).
    cursor = await db.execute(
        "SELECT id FROM list_items WHERE list_id=? AND lower(text)=lower(?)"
        f" AND deleted=0 AND created_at::timestamptz > now() - interval '{int(window_s)} seconds' LIMIT 1",
        (list_id, text),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def add_item_to_list(
    db,
    *,
    user_id: str,
    list_type: str,
    list_name: str,
    text: str,
    quantity: Optional[str] = None,
    category: Optional[str] = None,
    new_list_visibility: str,
    dedup_window_s: int = 0,
) -> dict:
    """Resolve-or-create a list and add one item — the shared voice/MCP add.

    Behaviour choices stay with the caller: ``new_list_visibility`` sets the
    auto-created list's visibility (pass :func:`default_visibility`'s result to
    keep the current rule), ``dedup_window_s`` > 0 treats an identical
    (case-insensitive) item added within that window as already done — the
    voice retry-idempotency guard; 0 disables it. Notification is NOT sent
    here.

    When the list does not exist, the list row and its first item land in ONE
    transaction (asyncpg auto-commits each statement, so without it a failed
    item insert orphans an empty list — the MCP-path bug this module fixes).

    Returns ``{"list_id", "item_id", "created_list", "deduped"}``; on a dedup
    hit ``item_id`` is the existing duplicate row's id and nothing is written.
    """
    list_id = await find_list_id(db, user_id=user_id, list_type=list_type, name=list_name)
    if list_id is not None:
        if dedup_window_s > 0:
            dup_id = await _find_recent_duplicate(
                db, list_id=list_id, text=text, window_s=dedup_window_s
            )
            if dup_id is not None:
                return {"list_id": list_id, "item_id": dup_id, "created_list": False, "deduped": True}
        # Existing list: a single INSERT is already atomic.
        item = await create_item_record(
            db, list_id=list_id, text=text, quantity=quantity, category=category
        )
        return {"list_id": list_id, "item_id": item["id"], "created_list": False, "deduped": False}

    async def _write_new_list_and_item() -> dict:
        new_list = await create_list_record(
            db,
            user_id=user_id,
            name=list_name,
            list_type=list_type,
            visibility=new_list_visibility,
        )
        item = await create_item_record(
            db, list_id=new_list["id"], text=text, quantity=quantity, category=category
        )
        return {
            "list_id": new_list["id"],
            "item_id": item["id"],
            "created_list": True,
            "deduped": False,
        }

    txn = getattr(db, "transaction", None)
    if callable(txn):
        async with txn():
            return await _write_new_list_and_item()
    # Fallback (e.g. a DB shim without transaction support).
    return await _write_new_list_and_item()
