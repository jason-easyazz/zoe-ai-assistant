"""The ONE door for contacts: every write of the ``people`` table by anything a person does.

WHERE CONTACTS LIVE. ``people``: one row per contact. ``user_id`` is the member whose address book it is in; ``visibility`` is
``family`` (the household shares it: every member sees it and, today, any member may change or delete it) or ``personal`` (its
owner's alone). ``person_relationships`` holds the edges between them; ITS one door is ``people_graph.py``.

Each entry point had its own statement (different columns, placeholders and WHERE); unifying them is a separate, later decision.
So every statement is kept here exactly as it was, under a key naming the entry point it serves (``SQL``; the three whose SET
list is built by the caller are ``_BUILT``), and ``write_contact`` issues it the way that caller did: the pooled ``?`` handle
(``execute(sql, params)``) or a raw asyncpg connection (``execute(sql, *params)``, ``raw=True``). Callers keep their own
lookups, refusals, notices, memory mirror and commits.

NOT HERE, and why (``tests/test_people_one_door.py`` names each by path):
  * shared mechanics, used by automatic work AND by a member's action: ``contacts_store.py`` (the address-book store: its create
    and fill serve the card import, voice learning, the day review's approval and a voice tag), ``person_merge.py`` (a merge);
  * automatic makers, nobody acting: ``person_extractor.py``, ``named_relations.py`` (extractors), ``person_health.py`` (a score);
  * the eraser and its undo: ``memory_forget_cascade.py``;
  * two counters the screen bumps (mark-read, log-activity) stay in ``routers/people.py``: not yet behind the door.
"""
from __future__ import annotations

#: entry point -> its statement, kept to the byte
SQL = {
    # the screen (routers/people.py)
    "screen_create": """
        INSERT INTO people (id, user_id, name, relationship, circle, context, email, phone, birthday,
                            notes, preferences, visibility, is_partial, how_we_met, first_met_date,
                            introduced_by_person_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
    "screen_delete": "UPDATE people SET deleted = 1, updated_at = NOW() WHERE id = ?",
    "screen_relation_stub": "INSERT INTO people (id, user_id, name, circle, context, visibility, is_partial) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
    "screen_relation_context": "UPDATE people SET context = ? WHERE id = ? AND user_id = ?",
    # the brain's tools (mcp_server.py) and the spoken "add a contact" (intent_router.py)
    "tool_create": "INSERT INTO people (id, user_id, name, relationship, birthday, phone, email, notes, visibility, circle, context) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    "tool_delete": "UPDATE people SET deleted=1, updated_at=NOW() WHERE id=? AND user_id=?",
    "said_create": "INSERT INTO people (id, user_id, name, relationship, birthday, phone, email,"
                   " notes, visibility, circle, context) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    "said_rename": "UPDATE people SET name = ?, relationship = COALESCE(?, relationship), is_partial = 0,"
                   " updated_at = ? WHERE id = ? AND user_id = ?",
    # Skybridge (a raw asyncpg handle) and the panel's "introduce" (routers/voice_tts.py)
    "spoken_create": """
        INSERT INTO people (id, user_id, name, relationship, circle, context, visibility, is_partial)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
    "introduced": "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility) VALUES (?,?,?,?,?,?,?)",
    # an accepted "add X as a contact" offer (pending_suggestions.py; raw asyncpg, inside the suggestion's transaction)
    "offer_promote_with_relation": "UPDATE people SET is_partial=0, relationship=$1 WHERE id=$2",
    "offer_promote": "UPDATE people SET is_partial=0 WHERE id=$1",
    "offer_upgrade": "UPDATE people SET name=$1, relationship=COALESCE($2, relationship), is_partial=0,"
                     " updated_at=$3 WHERE id=$4 AND user_id=$5",
    "offer_create": "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility, is_partial)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7,0)",
    # a correction the member spoke (correction_apply.py)
    "corrected_pet_pg": "UPDATE people SET relationship=$1, updated_at=$2 WHERE id=$3 AND user_id=$4",
    "corrected_pet": "UPDATE people SET relationship=?, updated_at=? WHERE id=? AND user_id=?",
    "corrected_name": "UPDATE people SET name = ?, updated_at = ? WHERE id = ? AND user_id = ?",
    # the pendant day review: an approved relationship, and its undo (omi_review_apply.py)
    "reviewed_relation": "UPDATE people SET relationship = ? WHERE id = ? AND user_id = ? AND relationship IS NULL "
                         "AND (deleted = 0 OR deleted IS NULL)",
    "reviewed_relation_undo": "UPDATE people SET relationship = NULL WHERE id = ? AND user_id = ? AND relationship = ?",
}

#: entry point -> how its statement is built from the caller's SET fragments (and, for Skybridge, its parameter count)
_BUILT = {
    "screen_update": lambda sets, n: f"UPDATE people SET {', '.join(sets)}, updated_at = NOW() WHERE id = ?",
    "tool_update": lambda sets, n: f"UPDATE people SET {','.join(sets)} WHERE id=? AND user_id=?",
    "spoken_fact": lambda sets, n: f"UPDATE people SET {', '.join(sets)} WHERE id = ${n-1} AND user_id = ${n}",
}


def statement(entry: str, sets=None, n: int = 0) -> str:
    return _BUILT[entry](sets, n) if entry in _BUILT else SQL[entry]


async def write_contact(db, entry: str, params, *, sets=None, raw: bool = False):
    """Issue ``entry``'s statement with ``params`` and hand back what the handle returned (a cursor, or asyncpg's command tag)."""
    sql = statement(entry, sets, len(params))
    if raw:
        return await db.execute(sql, *params)
    return await db.execute(sql, params)


async def write_contact_either(db, pg_entry: str, entry: str, args: tuple) -> bool:
    """A statement tried in asyncpg's form, then in the pooled form (``correction_apply`` holds either kind of handle): True
    when one of them ran."""
    for key, dollar in ((pg_entry, True), (entry, False)):
        try:
            await write_contact(db, key, args, raw=dollar)
            return True
        except Exception:  # noqa: BLE001 — try the other placeholder style
            continue
    return False
