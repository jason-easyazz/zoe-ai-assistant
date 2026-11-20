"""The ONE door for the journal: every write of the ``journal_entries`` table by anything a person does.

WHERE THE JOURNAL LIVES. One Postgres table, ``journal_entries``: ``user_id`` is the entry's WRITER. An entry is always
``visibility = 'personal'`` (every writer says so, and every read is ``user_id = me``): a journal is private to its writer.

Each entry point had its own statement; each is kept statement for statement under the name of the entry point it serves.
Callers keep their own lookups, refusals, notices, memory mirror and commits. Nothing else writes ``journal_entries``: there is
no automatic maker and no per-entry eraser.
"""
from __future__ import annotations


async def create_entry_record(db, *, entry_id: str, user_id: str, title, content, mood, mood_score, tags_json, weather, location,
                              photos_json, privacy_level):
    """The screen's create (``POST /api/journal/entries``)."""
    return await db.execute(
        """INSERT INTO journal_entries (
            id, user_id, title, content, mood, mood_score, tags, weather,
            location, photos, privacy_level, visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'personal', 0)""",
        (
            entry_id,
            user_id,
            title,
            content,
            mood,
            mood_score,
            tags_json,
            weather,
            location,
            photos_json,
            privacy_level,
        ),
    )


async def update_entry_record(db, *, entry_id: str, updates: list, params: list):
    """The screen's edit: the SET fragments and values the route built, by id (the route found the entry as the writer's own)."""
    return await db.execute(
        f"UPDATE journal_entries SET {', '.join(updates)} WHERE id = ?",
        params,
    )


async def delete_entry_record(db, *, entry_id: str):
    """The screen's soft delete (the route found the entry as the writer's own)."""
    return await db.execute(
        "UPDATE journal_entries SET deleted = 1, updated_at = NOW() WHERE id = ?",
        [entry_id],
    )


async def tool_entry_record(db, *, entry_id: str, user_id: str, content, title, mood, mood_score, tags_json):
    """The brain's ``journal_create_entry`` tool."""
    return await db.execute(
        """INSERT INTO journal_entries (
                id, user_id, content, title, mood, mood_score, tags, visibility, deleted
            ) VALUES (?,?,?,?,?,?,?,'personal',0)""",
        (entry_id, user_id, content, title, mood,
         mood_score, tags_json),
    )


async def said_entry_record(db, *, entry_id: str, user_id: str, content, title, mood, mood_score, tags_json):
    """The spoken "journal that ..." (``intent_router``)."""
    return await db.execute(
        "INSERT INTO journal_entries (id, user_id, content, title, mood, mood_score,"
        " tags, visibility, deleted) VALUES (?,?,?,?,?,?,?,'personal',0)",
        (entry_id, user_id, content, title, mood, mood_score, tags_json),
    )
