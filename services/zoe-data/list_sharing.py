"""Share a list with the house ("share it to the house") and tell the other members once.

The ONE place a list's visibility flips by voice. Visibility is the existing ``lists.visibility`` column (``personal`` = its owner
only, ``family`` = every member and the panel); the Lists UI and the touch panel already filter on it, so a share needs no UI change.

* Only the list's OWNER may share or unshare it. A guest owns nothing, so a guest can neither. A private list of someone else is
  never confirmed to exist (``not_found``), so a share request cannot probe for it.
* The recipient heads-up rides the existing ``pending_suggestions`` table (``action_type='list_shared'``, one row per recipient,
  ``description`` = the list id). It is delivered once into the recipient's next brain turn (``claim_heads_ups``, called from
  ``zoe_flue_client``), never to the sharer or a guest, and is dropped when the list is unshared (or deleted) first.
  ``resolved``: 0 pending, 1 dropped, 2 delivered. Docs: docs/knowledge/voice-list-create-share.md.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import panel_principal
import typed_env
from user_filters import is_synthetic_user, is_unidentified

logger = logging.getLogger(__name__)

HEADS_UP_ACTION = "list_shared"
HEADS_UP_SESSION = "list_share"
PENDING, DROPPED, DELIVERED = 0, 1, 2
#: A heads-up the recipient has not heard in this long is stale ("shared two weeks ago" is no news).
HEADS_UP_MAX_AGE = timedelta(days=14)
MAX_PER_TURN = 2
_GUEST_ROLES = frozenset({"guest", "kiosk"})


def share_enabled() -> bool:
    """ZOE_VOICE_LIST_SHARE (default ON): the spoken share / unshare intents. Off => the phrases are not list commands at all."""
    return bool(typed_env.flag("ZOE_VOICE_LIST_SHARE"))


def heads_up_enabled() -> bool:
    """ZOE_LIST_SHARE_HEADSUP (default ON): the one-time "X has shared a list with you" mention. Off => nothing is queued or said."""
    return bool(typed_env.flag("ZOE_LIST_SHARE_HEADSUP"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_inline(value: str, cap: int = 60) -> str:
    """A name headed into a quoted brain directive: whitespace folded, quotes / brackets / structure characters removed."""
    v = re.sub(r"\s+", " ", value or "").strip()
    return re.sub(r"[#`*_\[\]\n\r{}\"'‘’“”]", "", v)[:cap].strip()


def _changed(status) -> int:
    """Rows a driver reported changing (asyncpg's "UPDATE 1"); a driver that reports nothing is read as one row."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except (ValueError, TypeError):
        return 1


async def set_list_visibility(db, *, list_id: str, user_id: str, share: bool) -> dict:
    """Share (``share=True``) or make private the list ``list_id`` for ``user_id``.

    Returns ``{"status", "name", "list_type"}``; status is one of ``shared`` / ``unshared`` / ``already`` / ``not_owner`` /
    ``not_found`` / ``guest`` / ``locked`` (the household shopping list is shared by definition and is never made private).
    """
    if panel_principal.is_guest_id(user_id) or user_id == panel_principal.HOUSEHOLD_VIEWER:
        return {"status": "guest", "name": "", "list_type": ""}
    rows = await db.fetch(
        "SELECT id, user_id, name, list_type, visibility FROM lists WHERE id = $1 AND deleted = 0", list_id)
    if not rows:
        return {"status": "not_found", "name": "", "list_type": ""}
    row = dict(rows[0])
    name, ltype = str(row.get("name") or ""), str(row.get("list_type") or "")
    family = str(row.get("visibility") or "") == "family"
    if not family and row.get("user_id") != user_id:
        return {"status": "not_found", "name": "", "list_type": ""}      # someone else's private list: never confirmed
    if row.get("user_id") != user_id:
        return {"status": "not_owner", "name": name, "list_type": ltype}
    if share:
        if family:
            return {"status": "already", "name": name, "list_type": ltype}
        changed = _changed(await db.execute(
            "UPDATE lists SET visibility = 'family', updated_at = NOW() "
            "WHERE id = $1 AND user_id = $2 AND deleted = 0 AND visibility <> 'family'", list_id, user_id))
        if not changed:
            return {"status": "already", "name": name, "list_type": ltype}
        if heads_up_enabled():
            await queue_heads_up(db, list_id=list_id, list_name=name, sharer_id=user_id)
        return {"status": "shared", "name": name, "list_type": ltype}
    if ltype == "shopping":
        return {"status": "locked", "name": name, "list_type": ltype}
    if not family:
        return {"status": "already", "name": name, "list_type": ltype}
    changed = _changed(await db.execute(
        "UPDATE lists SET visibility = 'personal', updated_at = NOW() "
        "WHERE id = $1 AND user_id = $2 AND deleted = 0 AND visibility = 'family'", list_id, user_id))
    if changed:
        await drop_heads_ups(db, list_id)
    return {"status": "unshared", "name": name, "list_type": ltype}


async def household_recipients(db, *, exclude_user_id: str) -> list[tuple[str, str]]:
    """``[(user_id, name)]`` of every household member except the sharer: not a guest role / sentinel, not a synthetic id."""
    rows = await db.fetch("SELECT id, name, role FROM users ORDER BY name, id")
    out = []
    for r in rows:
        uid, role = str(r["id"]), str(r["role"] or "").lower()
        if uid == exclude_user_id or role in _GUEST_ROLES or is_unidentified(uid) or is_synthetic_user(uid):
            continue
        out.append((uid, str(r["name"] or "")))
    return out


async def queue_heads_up(db, *, list_id: str, list_name: str, sharer_id: str, now: Optional[datetime] = None) -> int:
    """One pending heads-up per OTHER household member for this share. A member who already HEARD about this list is not told
    again on a re-share; one with a still-pending row is not given a second. Returns how many were queued. Never raises."""
    try:
        sharer = await db.fetch("SELECT name FROM users WHERE id = $1", sharer_id)
        who = safe_inline(str(dict(sharer[0]).get("name") or "") if sharer else "") or "Someone"
        phrase_name = safe_inline(list_name, 80) or "a list"
        stamp = (now or _utcnow()).isoformat()
        queued = 0
        for uid, _name in await household_recipients(db, exclude_user_id=sharer_id):
            seen = await db.fetch(
                "SELECT resolved FROM pending_suggestions WHERE user_id = $1 AND action_type = $2 AND description = $3 "
                "AND resolved IN (0, 2) LIMIT 1", uid, HEADS_UP_ACTION, list_id)
            if seen:
                continue
            await db.execute(
                "INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, list_type, when_hint, "
                "amount_hint, offer_phrase, pre_filled_slots, created_at, turns_elapsed, expire_after_turns, resolved) "
                "VALUES ($1, $2, $3, $4, $5, NULL, NULL, NULL, $6, $7, $8, 0, 0, 0)",
                str(uuid.uuid4()), uid, HEADS_UP_SESSION, HEADS_UP_ACTION, list_id,
                f"{who} has shared a list with you: {phrase_name}.",
                json.dumps({"list_id": list_id, "sharer_id": sharer_id, "sharer_name": who}), stamp)
            queued += 1
        return queued
    except Exception as exc:  # noqa: BLE001 - the share itself has happened; a missed heads-up must not undo or fail it
        logger.warning("list share heads-up not queued: %s", type(exc).__name__)
        return 0


async def drop_heads_ups(db, list_id: str) -> None:
    """The list was made private (or deleted) before they heard: nobody is told. Delivered rows stay as the record."""
    try:
        await db.execute(
            "UPDATE pending_suggestions SET resolved = 1 WHERE action_type = $1 AND description = $2 AND resolved = 0",
            HEADS_UP_ACTION, list_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list share heads-ups not dropped (%s) - they are re-checked against the list at delivery", type(exc).__name__)


def _parse_stamp(value) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def claim_heads_ups(db, user_id: str, *, now: Optional[datetime] = None,
                          limit: int = MAX_PER_TURN) -> list[str]:
    """The heads-up sentences to say to ``user_id`` NOW, each claimed (marked delivered) so it is said exactly once.

    Delivered only to an identified member. A row whose list is no longer shared (unshared, deleted, or never family-visible),
    or that went stale, is dropped instead. The sharer's name is the one stored at share time from the users table. Never raises.
    """
    if is_unidentified(user_id) or panel_principal.is_guest_id(user_id) or not heads_up_enabled():
        return []
    out: list[str] = []
    try:
        at = now or _utcnow()
        rows = await db.fetch(
            "SELECT id, description, pre_filled_slots, created_at FROM pending_suggestions "
            "WHERE user_id = $1 AND action_type = $2 AND resolved = 0 ORDER BY created_at ASC LIMIT 10",
            user_id, HEADS_UP_ACTION)
        for r in rows:
            row = dict(r)
            made = _parse_stamp(row.get("created_at"))
            lists = await db.fetch("SELECT name, visibility, user_id FROM lists WHERE id = $1 AND deleted = 0",
                                   row["description"])
            live = bool(lists) and str(dict(lists[0]).get("visibility") or "") == "family" \
                and dict(lists[0]).get("user_id") != user_id
            if not live or made is None or at - made > HEADS_UP_MAX_AGE:
                await db.execute("UPDATE pending_suggestions SET resolved = 1 WHERE id = $1 AND resolved = 0", row["id"])
                continue
            if len(out) >= limit:
                continue
            if not _changed(await db.execute(
                    "UPDATE pending_suggestions SET resolved = 2 WHERE id = $1 AND resolved = 0", row["id"])):
                continue            # a concurrent turn claimed it first
            try:
                slots = json.loads(row.get("pre_filled_slots") or "{}")
            except json.JSONDecodeError:
                slots = {}
            who = safe_inline(str(slots.get("sharer_name") or "")) or "Someone"
            out.append(f"{who} has shared a list with you: {safe_inline(str(dict(lists[0]).get('name') or ''), 80)}.")
    except Exception as exc:  # noqa: BLE001 - a heads-up never breaks a turn
        logger.warning("list share heads-up claim failed: %s", type(exc).__name__)
        return out
    return out


async def heads_up_block(user_id: str, *, open_line: str, close_line: str,
                         db_ctx: Optional[Callable] = None) -> str:
    """The brain-prompt block carrying this member's due heads-ups, in the delimiters the sidecar already elides from older turns
    (the contact-offer block's). "" when nothing is due (and no DB work beyond one indexed read)."""
    if not heads_up_enabled() or is_unidentified(user_id):
        return ""
    if db_ctx is None:
        from db_pool import get_db_ctx as db_ctx
    async with db_ctx() as db:
        lines = await claim_heads_ups(db, user_id)
    if not lines:
        return ""
    body = "\n".join(f'- After answering, tell the user exactly: "{line}"' for line in lines)
    return f"{open_line}\n{body}\n{close_line}"
