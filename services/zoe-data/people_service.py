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

from typing import Optional

import kernel_door
import os_shadow

# ── The Zoe OS contacts door (ZOE_OS_PEOPLE=kernel; docs/knowledge/zoe-os-people-door.md) ─────────────────────────────────────
# A person's change to a contact asks the kernel first ("may ``actor`` do this to a contact in ``owner``'s book?"), is made, then
# reported. A kernel refusal raises ``ContactRefused``: FINAL, nothing written. Kernel down or slow: the change proceeds, logged
# ``not_checked``. With the flag off nothing of this runs but a flag read. Only ids cross. READING is never asked.
#
# Two ways in. ``write_contact`` asks for the statement it issues (the caller says who is acting and whose the contact is). A
# change made of several statements, or made through the shared mechanics (a relationship edge, a merge, the address-book
# store), asks ONCE up front (``ask_contact`` / ``ask_about``) and its statements then say ``under=True``.


class ContactRefused(kernel_door.Refused):
    """The kernel refused this contact change. FINAL: nothing was written. ``reason`` is a closed code; ``line()`` the plain sentence."""

    area = "people"
    lines = {
        "not_owner": "That contact is someone else's, so I can't change it.",
        "adults_only": "Only an adult can change a contact the whole house shares.",
        "unknown_person": "I don't know who is asking, so I can't change your contacts.",
    }
    fallback = "I'm not allowed to change that contact."


def stamps_now() -> Optional[dict]:
    """A caller's first statement, before anything it awaits; handed back as ``stamps_at``. None unless the CONTACTS door is on."""
    return kernel_door.stamps_now("people")


async def ask_contact(verb: str, *, actor: Optional[str], person_id: str, owner: object, visibility: object,
                      stamps_at: Optional[dict]) -> "Optional[kernel_door.Decision]":
    """The ask at the door. None with the flag off. ``owner`` is ``people.user_id`` (whose address book the contact is in);
    ``visibility`` ``family`` is a household contact. Raises ``ContactRefused`` on a refusal."""
    return await kernel_door.gate("people", verb, actor=actor, target=person_id, owner=owner,
                                  household=str(visibility or "") == "family", stamps_at=stamps_at, refused=ContactRefused)


async def ask_about(db, verb: str, *, actor: Optional[str], person_id: str, stamps_at: Optional[dict]) -> "Optional[kernel_door.Decision]":
    """``ask_contact`` for a STORED contact whose row the caller has not read (the screen's delete and merge read only ids): with
    the door on, the door reads whose it is, in the scope the screen itself uses (the household's and the caller's own). A
    contact the caller cannot see is not asked about: the caller's own lookup answers "not found", as it always did. A fault in
    this read RAISES (the change is not made unasked). Flag off: a flag read and nothing else."""
    if stamps_at is None or not os_shadow.area_on("people"):
        return None
    cur = await db.execute("SELECT user_id, visibility FROM people WHERE id = ? AND deleted = 0 AND (visibility = 'family' OR user_id = ?)",
                           (person_id, actor))
    row = await cur.fetchone()
    if row is None:
        os_shadow.door_unchecked(verb, "no_row", "people")
        return None
    row = dict(row)
    return await ask_contact(verb, actor=actor, person_id=person_id, owner=row.get("user_id"), visibility=row.get("visibility"),
                             stamps_at=stamps_at)

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


#: entry point -> what the kernel is asked (an entry that is not a create or a delete is an edit)
_CREATES = frozenset({"screen_create", "screen_relation_stub", "tool_create", "said_create", "spoken_create", "introduced", "offer_create"})
_DELETES = frozenset({"screen_delete", "tool_delete"})


def verb_of(entry: str) -> str:
    return "contact.create" if entry in _CREATES else "contact.delete" if entry in _DELETES else "contact.edit"


async def write_contact(db, entry: str, params, *, sets=None, raw: bool = False, actor: Optional[str] = None,
                        person_id: Optional[str] = None, owner: object = None, visibility: object = None,
                        stamps_at: Optional[dict] = None, under: bool = False):
    """Issue ``entry``'s statement with ``params`` and hand back what the handle returned (a cursor, or asyncpg's command tag).

    The door: unless ``under`` (this statement is part of a change its caller already asked about), the kernel is asked first
    about ``person_id`` in ``owner``'s book, and told how the write ended."""
    sql = statement(entry, sets, len(params))
    if under:
        return await (db.execute(sql, *params) if raw else db.execute(sql, params))
    asked = await ask_contact(verb_of(entry), actor=actor, person_id=str(person_id or ""), owner=owner, visibility=visibility,
                              stamps_at=stamps_at)
    return await kernel_door.write(asked, db.execute(sql, *params) if raw else db.execute(sql, params))


async def write_contact_either(db, pg_entry: str, entry: str, args: tuple, *, under: bool = True) -> bool:
    """A statement tried in asyncpg's form, then in the pooled form (``correction_apply`` holds either kind of handle): True
    when one of them ran. Always part of a change its caller asked about (``under``): a correction asks once, up front."""
    for key, dollar in ((pg_entry, True), (entry, False)):
        try:
            await write_contact(db, key, args, raw=dollar, under=True)
            return True
        except Exception:  # noqa: BLE001 — try the other placeholder style
            continue
    return False
