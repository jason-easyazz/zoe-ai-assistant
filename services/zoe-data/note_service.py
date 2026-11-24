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

from typing import Optional

import kernel_door

# ── The Zoe OS notes door (ZOE_OS_NOTES=kernel; docs/knowledge/zoe-os-notes-journal-door.md) ─────────────────────────────────
# Every function below asks the kernel first ("may ``actor`` do this to ``owner``'s note?"), writes, then reports how the write
# ended. A kernel refusal raises ``NoteRefused``: FINAL, nothing written. Kernel down or slow: the change proceeds, logged
# ``not_checked``. With the flag off nothing of this runs but a flag read. Only ids cross. READING is never asked.


class NoteRefused(kernel_door.Refused):
    """The kernel refused this note change. FINAL: nothing was written. ``reason`` is a closed code; ``line()`` the plain sentence."""

    area = "notes"
    lines = {
        "not_owner": "That note is someone else's, so I can't change it.",
        "adults_only": "Only the person who wrote that note, or an adult, can change it.",
        "unknown_person": "I don't know who is asking, so I can't change your notes.",
    }
    fallback = "I'm not allowed to change that note."


def stamps_now() -> Optional[dict]:
    """A caller's first statement, before anything it awaits; handed back as ``stamps_at``. None unless the NOTES door is on."""
    return kernel_door.stamps_now("notes")


def _gate(verb: str, *, actor, note_id: str, owner, visibility, stamps_at):
    """``owner`` is the note's author (``notes.user_id``); ``visibility`` ``family`` is a household note."""
    return kernel_door.gate("notes", verb, actor=actor, target=note_id, owner=owner, household=str(visibility or "") == "family",
                            stamps_at=stamps_at, refused=NoteRefused)



async def create_note_record(db, *, note_id: str, user_id: str, title, content, category, tags_json, visibility,
                             actor: Optional[str] = None, stamps_at: Optional[dict] = None):
    """The screen's create (``POST /api/notes/``): the author chooses the visibility."""
    asked = await _gate("note.create", actor=actor, note_id=note_id, owner=user_id, visibility=visibility, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
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
    ))


async def update_note_record(db, *, note_id: str, updates: list, params: list, actor: Optional[str] = None, owner=None,
                             visibility=None, stamps_at: Optional[dict] = None):
    """The screen's edit: the SET fragments and values the route built, by id (the route refused anyone but the author).
    ``owner`` / ``visibility``: the stored row's, as the route read them."""
    asked = await _gate("note.edit", actor=actor, note_id=note_id, owner=owner, visibility=visibility, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        f"UPDATE notes SET {', '.join(updates)} WHERE id = ?",
        params,
    ))


async def delete_note_record(db, *, note_id: str, actor: Optional[str] = None, owner=None, visibility=None,
                             stamps_at: Optional[dict] = None):
    """The screen's soft delete (the route refused anyone but the author). ``owner`` / ``visibility``: the stored row's."""
    asked = await _gate("note.delete", actor=actor, note_id=note_id, owner=owner, visibility=visibility, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        "UPDATE notes SET deleted = 1, updated_at = NOW() WHERE id = ?",
        [note_id],
    ))


async def said_note_record(db, *, note_id: str, user_id: str, title, content, category, stamps_at: Optional[dict] = None):
    """A note made in conversation (the brain's ``note_create`` tool and the spoken "note that ..."): always personal, and
    the speaker's own."""
    asked = await _gate("note.create", actor=user_id, note_id=note_id, owner=user_id, visibility="personal", stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        "INSERT INTO notes (id, user_id, title, content, category, visibility) VALUES (?,?,?,?,?,?)",
        (note_id, user_id, title, content, category, "personal"),
    ))


async def tool_update_note_record(db, *, note_id: str, user_id: str, updates: list, params: list, stamps_at: Optional[dict] = None):
    """The brain's ``note_update`` tool: the member's OWN note only (the statement says so, and the tool found the note as
    theirs first). The tool reads only the note's id, so the ask says ``private`` whatever the note's visibility: the author
    may change either kind, and the door adds no read to find out."""
    asked = await _gate("note.edit", actor=user_id, note_id=note_id, owner=user_id, visibility="personal", stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(f"UPDATE notes SET {','.join(updates)} WHERE id=? AND user_id=?", params))


async def tool_delete_note_record(db, *, note_id: str, user_id: str, stamps_at: Optional[dict] = None):
    """The brain's ``note_delete`` tool: the member's OWN note only (told as ``private``, as the update is)."""
    asked = await _gate("note.delete", actor=user_id, note_id=note_id, owner=user_id, visibility="personal", stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute("UPDATE notes SET deleted=1, updated_at=NOW() WHERE id=? AND user_id=?",
                                                     (note_id, user_id)))


async def suggested_note_record(conn, *, note_id: str, user_id: str, title, content, stamps_at: Optional[dict] = None):
    """An accepted note suggestion (a raw asyncpg connection, inside the suggestion's own transaction, so the kernel hears
    ``done`` only once THAT has committed): a personal note."""
    asked = await _gate("note.create", actor=user_id, note_id=note_id, owner=user_id, visibility="personal", stamps_at=stamps_at)
    return await kernel_door.write(asked, conn.execute(
        "INSERT INTO notes (id, user_id, title, content, category, visibility, deleted) VALUES ($1,$2,$3,$4,$5,$6,0)",
        note_id,
        user_id,
        title,
        content,
        "general",
        "personal",
    ))
