"""The ONE door for notes: every write of the ``notes`` table by anything a person does.

WHERE NOTES LIVE. One Postgres table, ``notes``: ``user_id`` is the note's AUTHOR (there is no other owner), ``visibility`` is
``personal`` (the default, and the only value the spoken path, the brain's tool and an accepted suggestion write) or ``family``
(the screen may choose it: every member then SEES the note; only its author may change or delete it).

Each entry point had its own statement (different columns, placeholders and WHERE); unifying them is a separate, later decision,
so each is kept statement for statement under the name of the entry point it serves. Callers keep their own lookups, refusals,
notices, memory mirror and commits. Nothing else writes ``notes``: there is no automatic maker and no per-note eraser (a note
goes when its author deletes it, or with the member's account).
"""
from __future__ import annotations


async def create_note_record(db, *, note_id: str, user_id: str, title, content, category, tags_json, visibility):
    """The screen's create (``POST /api/notes/``): the author chooses the visibility."""
    return await db.execute(
        """INSERT INTO notes (id, user_id, title, content, category, tags, visibility, deleted)
         VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            note_id,
            user_id,
            title,
            content,
            category,
            tags_json,
            visibility,
        ),
    )


async def update_note_record(db, *, note_id: str, updates: list, params: list):
    """The screen's edit: the SET fragments and values the route built, by id (the route refused anyone but the author)."""
    return await db.execute(
        f"UPDATE notes SET {', '.join(updates)} WHERE id = ?",
        params,
    )


async def delete_note_record(db, *, note_id: str):
    """The screen's soft delete (the route refused anyone but the author)."""
    return await db.execute(
        "UPDATE notes SET deleted = 1, updated_at = NOW() WHERE id = ?",
        [note_id],
    )


async def said_note_record(db, *, note_id: str, user_id: str, title, content, category):
    """A note made in conversation (the brain's ``note_create`` tool and the spoken "note that ..."): always personal."""
    return await db.execute(
        "INSERT INTO notes (id, user_id, title, content, category, visibility) VALUES (?,?,?,?,?,?)",
        (note_id, user_id, title, content, category, "personal"),
    )


async def tool_update_note_record(db, *, note_id: str, user_id: str, updates: list, params: list):
    """The brain's ``note_update`` tool: the member's OWN note only (the statement says so)."""
    return await db.execute(f"UPDATE notes SET {','.join(updates)} WHERE id=? AND user_id=?", params)


async def tool_delete_note_record(db, *, note_id: str, user_id: str):
    """The brain's ``note_delete`` tool: the member's OWN note only."""
    return await db.execute("UPDATE notes SET deleted=1, updated_at=NOW() WHERE id=? AND user_id=?", (note_id, user_id))


async def suggested_note_record(conn, *, note_id: str, user_id: str, title, content):
    """An accepted note suggestion (a raw asyncpg connection, inside the suggestion's own transaction): a personal note."""
    return await conn.execute(
        "INSERT INTO notes (id, user_id, title, content, category, visibility, deleted) VALUES ($1,$2,$3,$4,$5,$6,0)",
        note_id,
        user_id,
        title,
        content,
        "general",
        "personal",
    )
