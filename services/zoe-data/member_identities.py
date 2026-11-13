"""member_identities - a member's own email addresses and phone numbers (migration 0078; record ``docs/knowledge/connected-accounts.md``).

So a calendar attendee or a mail recipient can be recognised as a household member ("am I invited?", "this mail is to Ben"). Values are
stored normalised (``normalise``); one value belongs to at most one member, and ``add`` never moves a value from one member to another.
Private to the member and admins: nothing here lists another member's identities; ``member_for`` answers with a member id only.
Erased with the member (``forget_user``, called from ``MemoryService.delete_user``) under the member's erase gate.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import user_erase_gate as erase_gate
from connected_accounts import _no_store

logger = logging.getLogger(__name__)

KINDS = ("email", "phone")
SOURCES = ("member", "admin", "account")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise(kind: str, value: str) -> Optional[str]:
    """Email: trimmed, lower-cased. Phone: ``+`` and digits only (no country is guessed). None when it is not one."""
    v = (value or "").strip()
    if kind == "email":
        v = v.lower()
        return v if _EMAIL.match(v) else None
    if kind == "phone":
        digits = re.sub(r"\D", "", v)
        return (("+" if v.startswith("+") else "") + digits) if 6 <= len(digits) <= 15 else None
    return None


async def add(user_id: str, kind: str, value: str, *, source: str = "member", verified: bool = False,
              scheduled_at: Optional[int] = None) -> Optional[str]:
    """Record one of the member's own identities. The id; None when the value is not valid, already another member's, or the member
    is being (or was since scheduled) erased. A value already the member's is updated (``verified`` only ever goes up)."""
    norm = normalise(kind, value)
    if norm is None or source not in SOURCES or not user_id:
        return None
    gen = erase_gate.generation(user_id) if scheduled_at is None else scheduled_at
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with erase_gate.writing(user_id, gen) as live:
        if not live:
            return None
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            cur = await db.execute("SELECT id, user_id FROM member_identities WHERE kind = ? AND value_norm = ?", (kind, norm))
            row = await cur.fetchone()
            if row is not None:
                if str(row[1]) != user_id:
                    logger.info("IDENTITIES event=refused reason=another_member kind=%s", kind)
                    return None
                if verified:
                    await db.execute("UPDATE member_identities SET verified = 1, updated_at = ? WHERE id = ?", (now, row[0]))
                    await db.commit()
                return str(row[0])
            iid = f"ident_{uuid.uuid4().hex}"
            await db.execute("INSERT INTO member_identities (id, user_id, kind, value_norm, verified, source, created_at, updated_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (iid, user_id, kind, norm, 1 if verified else 0, source, now, now))
            await db.commit()
    return iid


async def member_for(kind: str, value: str) -> Optional[str]:
    """The member whose identity this is, or None. Fails soft (None) on a missing table or bad value."""
    norm = normalise(kind, value)
    if norm is None:
        return None
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            cur = await db.execute("SELECT user_id FROM member_identities WHERE kind = ? AND value_norm = ?", (kind, norm))
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return None
        raise
    return str(row[0]) if row else None


async def forget_user(user_id: str) -> int:
    """Right to be forgotten: every identity of the member. Raises on any failure but "nothing stored" (the caller fails closed)."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with erase_gate.erasing(uid):
            from db_compat import get_compat_db
            async with get_compat_db() as db:
                cur = await db.execute("DELETE FROM member_identities WHERE user_id = ?", (uid,))
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("IDENTITIES forget failed (%s): rows NOT erased", type(exc).__name__)
        raise
    return int(getattr(cur, "rowcount", 0) or 0)
