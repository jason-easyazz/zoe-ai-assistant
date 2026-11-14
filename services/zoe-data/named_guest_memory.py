"""A named guest's own memory, and erasing a guest (``named_guests.py`` owns who they are; this module owns what Zoe keeps).

``named_guest_turns`` (migration 0072) holds what Zoe and ONE named guest said to each other at the panel, keyed by the member layer
the guest lives in (user_id, people_id). Read ONLY for a turn the server matched to that guest by VOICE (``named_guests.accept``
-> ``named_guests.current()``); never served to any other speaker, the member who enrolled them included (default: never - no owner
rule says otherwise). A guest cannot reach it by saying a name.

Erasing (fail closed, a failure RAISES): "forget everything about Quinn" (the entity forget -> ``contacts_store.forget_voiceprints``
-> ``omi_voices.purge_people`` -> ``erase_people``), "forget Quinn's voice" / "forget Quinn" said by the member who enrolled them
(``named_guests.tier`` -> ``erase``), and ``DELETE /api/voice/guests/{id}``. The forget-reach inventory row is ``forget_reach.py``;
the forget-completeness probe row is ``tests/test_forget_completeness_probe.py`` (layer ``named_guest_turns``).

Flags: ``ZOE_NAMED_GUEST_MEMORY`` (default off; needs ``ZOE_NAMED_GUESTS=on``), ``ZOE_NAMED_GUEST_RECALL_TURNS`` (default 6).
"""
from __future__ import annotations

import logging
import uuid
from typing import Iterable, Optional

from named_guests import CHANNEL, Guest, _safe, mode
from typed_env import flag

logger = logging.getLogger(__name__)

_MAX_STORED_CHARS = 600
_RECALL_CHARS = 1400


def memory_on() -> bool:
    return mode() == "on" and bool(flag("ZOE_NAMED_GUEST_MEMORY"))


async def has_print(db, guest: Guest) -> bool:
    """Does this guest still hold a panel print? Nothing about a guest is written once it is gone."""
    async with db.execute("SELECT 1 FROM learned_voiceprints WHERE owner_user_id = ? AND people_id = ? AND channel = ? LIMIT 1",
                          (guest.owner_user_id, guest.person_id, CHANNEL)) as cur:
        return await cur.fetchone() is not None


async def remember(guest: Optional[Guest], session_id: str, user_text: str, reply: str) -> bool:
    """Keep one exchange in that guest's space. Never raises; False when not kept (flag off, nothing said, a failure)."""
    if guest is not None:
        import named_guest_disclosure     # this is the one post-turn call of a guest turn: was the guest told what is kept?

        await named_guest_disclosure.after_turn(guest, reply)       # never raises; off = no work
    if guest is None or not memory_on() or not (user_text or "").strip():
        return False
    try:
        import contacts_store as cs
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            if not await has_print(db, guest):
                return False      # erased during this very turn ("forget me", or the member's forget): the turn is not kept
            await db.execute(
                "INSERT INTO named_guest_turns (id, user_id, people_id, session_id, user_text, reply, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), guest.owner_user_id, guest.person_id, str(session_id or "")[:120],
                 str(user_text).strip()[:_MAX_STORED_CHARS], str(reply or "").strip()[:_MAX_STORED_CHARS], cs.now_iso()))
            await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: turn not kept: %s", type(exc).__name__)
        return False


async def recall_lines(guest: Guest) -> list[str]:
    """That guest's own recent exchanges, oldest first, as prompt lines. Only ever called for a turn matched to THIS guest."""
    if not memory_on():
        return []
    n = max(0, min(int(flag("ZOE_NAMED_GUEST_RECALL_TURNS")), 20))
    if not n:
        return []
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        async with db.execute(
            "SELECT user_text, reply, created_at FROM named_guest_turns WHERE user_id = ? AND people_id = ? "
            "ORDER BY created_at DESC LIMIT ?", (guest.owner_user_id, guest.person_id, n)) as cur:
            rows = await cur.fetchall()
    import memory_forgotten as mf

    # the serve wall: an exchange that names something the member forgot is never recalled (STRICT: an outage raises - no recall)
    rows, _dropped = await mf.keep_unforgotten(guest.owner_user_id, rows, text_of=lambda r: f"{r[0]} {r[1]}", strict=True)
    out, used = [], 0
    for said, reply, at in rows:
        line = f"- {str(at)[:10]}: {guest.name} said \"{_safe(said, 200)}\"; you said \"{_safe(reply, 200)}\""
        if used + len(line) > _RECALL_CHARS:
            break
        out.append(line)
        used += len(line)
    return list(reversed(out))


async def turns_since(owner_user_id: str, person_id: str, since_epoch: float) -> list[tuple]:
    """``(id, epoch, user_text, reply)`` of one guest's kept exchanges since ``since_epoch``, oldest first. The night pass that makes
    ``conversation_notes`` reads this; it is the guest's own layer, read for that guest's notes only."""
    from db_compat import get_compat_db
    from memory_episodes import _epoch

    async with get_compat_db() as db:
        async with db.execute("SELECT id, created_at, user_text, reply FROM named_guest_turns WHERE user_id = ? AND people_id = ? "
                              "ORDER BY created_at ASC", (owner_user_id, person_id)) as cur:
            rows = await cur.fetchall()
    out = []
    for r in rows:
        at = _epoch(r[1])
        if at is not None and at >= since_epoch:
            out.append((str(r[0]), at, str(r[2] or ""), str(r[3] or "")))
    return out


async def erase_people(db, owner_user_id: str, person_ids: Iterable[str]) -> int:
    """Every remembered turn of these guests in this member's layer (the forget path). RAISES on a failure, except a missing
    table (before 0072 there is nothing to erase)."""
    ids = sorted(set(person_ids))
    if not ids:
        return 0
    try:      # their saved corpus clips go with them (voice_clips; counts only, never raises, nothing when none were labelled)
        import voice_clips

        voice_clips.erase_guests(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: saved clips of a forgotten guest not removed (%s)", type(exc).__name__)
    try:
        cur = await db.execute(f"DELETE FROM named_guest_turns WHERE user_id = ? AND people_id IN ({', '.join('?' * len(ids))})",
                               (owner_user_id, *ids))
    except Exception as exc:  # noqa: BLE001
        if "named_guest_turns" in str(exc) and ("no such table" in str(exc) or "does not exist" in str(exc)):
            return 0
        raise
    notes = 0
    try:      # the notes made from those turns are theirs too (conversation_notes; raises, except a missing table)
        import conversation_notes

        notes = await conversation_notes.erase_people(db, owner_user_id, ids)
    except ImportError:
        pass
    import named_guest_disclosure     # the told-mark goes too: a guest who is taught again is told again

    return int(getattr(cur, "rowcount", 0) or 0) + notes + await named_guest_disclosure.erase_people(db, owner_user_id, ids)


async def list_for(owner_user_id: str, *, admin: bool = False) -> list[dict]:
    """The named guests an owner can see (their own; an admin sees the household's). Never an embedding."""
    from db_compat import get_compat_db

    sql = ("SELECT v.people_id, p.name, v.owner_user_id, v.created_at FROM learned_voiceprints v JOIN people p ON p.id = v.people_id "
           "WHERE v.channel = ? AND (p.deleted = 0 OR p.deleted IS NULL)")
    params: tuple = (CHANNEL,)
    if not admin:
        sql += " AND v.owner_user_id = ?"
        params = (CHANNEL, owner_user_id)
    async with get_compat_db() as db:
        async with db.execute(sql + " ORDER BY p.name", params) as cur:
            rows = await cur.fetchall()
    return [dict(guest_id=r[0], name=r[1], owner_user_id=r[2], enrolled_at=r[3]) for r in rows]


async def erase(owner_user_id: str, person_id: str) -> bool:
    """"Forget <name>'s voice" / the owner's erase: the panel voiceprint AND every remembered turn of that guest in this member's
    layer. The person row (the member's contact) stays. RAISES on a failure (the caller says it could not)."""
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        cur = await db.execute("DELETE FROM learned_voiceprints WHERE owner_user_id = ? AND people_id = ? AND channel = ?",
                               (owner_user_id, person_id, CHANNEL))
        gone = int(getattr(cur, "rowcount", 0) or 0)
        turns = await erase_people(db, owner_user_id, [person_id])
        await db.commit()
    return bool(gone or turns)
