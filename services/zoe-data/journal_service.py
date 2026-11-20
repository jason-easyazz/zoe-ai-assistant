"""The ONE door for the journal: every write of the ``journal_entries`` table by anything a person does.

WHERE THE JOURNAL LIVES. One Postgres table, ``journal_entries``: ``user_id`` is the entry's WRITER. An entry is always
``visibility = 'personal'`` (every writer says so, and every read is ``user_id = me``): a journal is private to its writer.

Each entry point had its own statement; each is kept statement for statement under the name of the entry point it serves.
Callers keep their own lookups, refusals, notices, memory mirror and commits. Nothing else writes ``journal_entries``: there is
no automatic maker and no per-entry eraser.
"""
from __future__ import annotations

from typing import Optional

import kernel_door

# ── The Zoe OS journal door (ZOE_OS_JOURNAL=kernel; docs/knowledge/zoe-os-notes-journal-door.md) ─────────────────────────────
# Every function below asks the kernel first ("may ``actor`` do this to ``owner``'s journal?"), writes, then reports how the
# write ended. A kernel refusal raises ``JournalRefused``: FINAL, nothing written. Kernel down or slow: the change proceeds,
# logged ``not_checked``. With the flag off nothing of this runs but a flag read. Only ids cross. READING is never asked.


class JournalRefused(kernel_door.Refused):
    """The kernel refused this journal change. FINAL: nothing was written. ``reason`` is a closed code; ``line()`` the plain sentence."""

    area = "journal"
    lines = {
        "not_owner": "That journal is someone else's, so I can't change it.",
        "unknown_person": "I don't know who is asking, so I can't write in a journal.",
    }
    fallback = "I'm not allowed to change that journal entry."


def stamps_now() -> Optional[dict]:
    """A caller's first statement, before anything it awaits; handed back as ``stamps_at``. None unless the JOURNAL door is on."""
    return kernel_door.stamps_now("journal")


def _gate(verb: str, *, actor, entry_id: str, owner, stamps_at):
    """``owner`` is the entry's writer (``journal_entries.user_id``). An entry is never the house's: always told private."""
    return kernel_door.gate("journal", verb, actor=actor, target=entry_id, owner=owner, household=False, stamps_at=stamps_at,
                            refused=JournalRefused)



async def create_entry_record(db, *, entry_id: str, user_id: str, title, content, mood, mood_score, tags_json, weather, location,
                              photos_json, privacy_level, actor: Optional[str] = None, stamps_at: Optional[dict] = None):
    """The screen's create (``POST /api/journal/entries``)."""
    asked = await _gate("journal.create", actor=actor, entry_id=entry_id, owner=user_id, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
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
    ))


async def update_entry_record(db, *, entry_id: str, updates: list, params: list, actor: Optional[str] = None, owner=None,
                              stamps_at: Optional[dict] = None):
    """The screen's edit: the SET fragments and values the route built, by id (the route found the entry as the writer's own).
    ``owner``: the stored row's writer, as the route read it."""
    asked = await _gate("journal.edit", actor=actor, entry_id=entry_id, owner=owner, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        f"UPDATE journal_entries SET {', '.join(updates)} WHERE id = ?",
        params,
    ))


async def delete_entry_record(db, *, entry_id: str, actor: Optional[str] = None, owner=None, stamps_at: Optional[dict] = None):
    """The screen's soft delete (the route found the entry as the writer's own). ``owner``: the stored row's writer."""
    asked = await _gate("journal.delete", actor=actor, entry_id=entry_id, owner=owner, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        "UPDATE journal_entries SET deleted = 1, updated_at = NOW() WHERE id = ?",
        [entry_id],
    ))


async def tool_entry_record(db, *, entry_id: str, user_id: str, content, title, mood, mood_score, tags_json,
                            stamps_at: Optional[dict] = None):
    """The brain's ``journal_create_entry`` tool: the member's own journal."""
    asked = await _gate("journal.create", actor=user_id, entry_id=entry_id, owner=user_id, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        """INSERT INTO journal_entries (
                id, user_id, content, title, mood, mood_score, tags, visibility, deleted
            ) VALUES (?,?,?,?,?,?,?,'personal',0)""",
        (entry_id, user_id, content, title, mood,
         mood_score, tags_json),
    ))


async def said_entry_record(db, *, entry_id: str, user_id: str, content, title, mood, mood_score, tags_json,
                            stamps_at: Optional[dict] = None):
    """The spoken "journal that ..." (``intent_router``): the speaker's own journal."""
    asked = await _gate("journal.create", actor=user_id, entry_id=entry_id, owner=user_id, stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(
        "INSERT INTO journal_entries (id, user_id, content, title, mood, mood_score,"
        " tags, visibility, deleted) VALUES (?,?,?,?,?,?,?,'personal',0)",
        (entry_id, user_id, content, title, mood, mood_score, tags_json),
    ))
