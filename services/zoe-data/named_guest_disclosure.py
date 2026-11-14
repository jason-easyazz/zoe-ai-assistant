"""Telling a named guest what Zoe keeps about them, once (``named_guests.py`` owns who a guest is; runbook
``docs/knowledge/named-guests.md``).

THE GAP. A guest's voice can become known without the guest hearing a word about it: a member names a saved clip while the guest is
not there, the owner backfills from old clips, or Zoe learns the voice from conversation and only asks a name. Even at a panel
enrolment the guest hears "I keep a voice signature" and nothing about their conversations being remembered, or how to undo it.

THE RULE. On a turn the server matched to a named guest who has not been told (no ``named_guest_disclosures`` row, migration 0083),
the guest's brain block carries one more line: say, once, what is kept (the voice; the conversations when memory is on; panel
recordings when those are saved) and how it is forgotten. It is built from the flags as they are, so it never promises a thing that
is off. The guest counts as TOLD only when the reply that went out actually spoke of forgetting (``after_turn``); otherwise the line
rides the next turn again. Erasing the guest erases the row, so a guest who is taught again is told again.

A failed read means "not told" (the line repeats: loud), never "told" (silent).

Flag: ``ZOE_NAMED_GUEST_DISCLOSURE`` (default off; needs ``ZOE_NAMED_GUESTS=on``). Pinned by ``tests/test_named_guest_consent.py``.
"""
from __future__ import annotations

import logging

from named_guests import Guest, mode
from typed_env import flag

logger = logging.getLogger(__name__)


def on() -> bool:
    return mode() == "on" and bool(flag("ZOE_NAMED_GUEST_DISCLOSURE"))


def self_forget_on() -> bool:
    """The flag alone: both callers (the tier, the telling) already run only with ``ZOE_NAMED_GUESTS=on``."""
    return bool(flag("ZOE_NAMED_GUEST_SELF_FORGET"))


async def due(guest: Guest) -> bool:
    """Has this guest still to be told? False with the flag off. Never raises: a read that fails is "not told"."""
    if not on():
        return False
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute("SELECT 1 FROM named_guest_disclosures WHERE user_id = ? AND people_id = ? LIMIT 1",
                                  (guest.owner_user_id, guest.person_id)) as cur:
                return await cur.fetchone() is None
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: disclosure state unreadable (telling again): %s", type(exc).__name__)
        return True


def line(guest: Guest) -> str:
    """The brain's instruction for the telling. Only what is true under the flags as they are now."""
    import named_guest_memory
    import voice_clips

    keeps = ["you know their voice"]
    if named_guest_memory.memory_on():
        keeps.append("you remember your conversations with them, so you can pick them up next time")
    if bool(flag("ZOE_VOICE_SAVE_AUDIO")):
        keeps.append("short recordings of what they say at the panel are kept" + (
            f" for up to {voice_clips.retention_days()} days" if voice_clips.retention_enabled() else ""))
    undo = ('they can say "forget me" at any time and you will erase all of it' if self_forget_on()
            else "anyone who lives here can ask you to forget them at any time, and all of it is erased")
    return (f"{guest.name} has not been told what you keep about them. In this reply, as well as answering, tell them once, briefly "
            f"and kindly, in plain words: {'; '.join(keeps)}; and {undo}. Use the word \"forget\" when you tell them.")


def said(reply: str) -> bool:
    """Did the reply that went out speak of forgetting? The line asks for the word, so its absence means the telling did not happen."""
    return "forget" in str(reply or "").lower()


async def after_turn(guest: Guest, reply: str) -> bool:
    """Record that the guest was told, if this turn's reply told them. True when a row was written. Never raises."""
    try:
        if not said(reply) or not await due(guest):
            return False
        import contacts_store as cs
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            import named_guest_memory

            if not await named_guest_memory.has_print(db, guest):
                return False                     # erased during this turn (their own "forget me"): nothing about them is written
            await db.execute("INSERT INTO named_guest_disclosures (user_id, people_id, told_at) VALUES (?, ?, ?)",
                             (guest.owner_user_id, guest.person_id, cs.now_iso()))
            await db.commit()
        logger.info("NAMED_GUEST told=1")
        return True
    except Exception as exc:  # noqa: BLE001 - not recorded: they are simply told again
        logger.warning("named_guests: disclosure not recorded: %s", type(exc).__name__)
        return False


async def erase_people(db, owner_user_id: str, person_ids) -> int:
    """The told-marks of these guests (the forget path: a guest taught again is told again). RAISES, except on a missing table."""
    ids = sorted(set(person_ids))
    if not ids:
        return 0
    try:
        cur = await db.execute(f"DELETE FROM named_guest_disclosures WHERE user_id = ? AND people_id IN ({', '.join('?' * len(ids))})",
                               (owner_user_id, *ids))
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "UndefinedTableError" or "no such table" in str(exc).lower():
            return 0
        raise
    return int(getattr(cur, "rowcount", 0) or 0)
