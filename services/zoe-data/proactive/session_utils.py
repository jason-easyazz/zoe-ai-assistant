"""
Helpers for deferred / lazy session creation for proactive notifications.

When a push notification is tapped:
  1. The client opens chat.html?p={pending_id}
  2. The front-end calls POST /api/proactive/pending/{id} (claim)
  3. claim_pending() creates a real chat session seeded with the notification
     message, marks the pending row as claimed, and returns the session_id so
     the front-end can redirect to ?session={session_id}.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone


from db_compat import get_compat_db as _get_compat_db

log = logging.getLogger(__name__)


async def create_pending(
    user_id: str,
    message: str,
    trigger_type: str,
    item_id: str = "",
    context: dict | None = None,
    ttl_hours: int = 4,
) -> str:
    """
    Insert a proactive_pending row and return its id.
    The push notification's deep link will point to /chat.html?p={id}.
    """
    pid = str(uuid.uuid4())
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with _get_compat_db() as db:
        await db.execute(
            """INSERT INTO proactive_pending
               (id, user_id, message, trigger_type, item_id, trigger_context, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pid, user_id, message, trigger_type, item_id,
             json.dumps(context or {}), expires),
        )
        await db.commit()
    log.debug("Created pending %s for user %s", pid, user_id)
    return pid


async def claim_pending(pending_id: str) -> dict | None:
    """
    Mark a pending notification as claimed and create a chat session seeded
    with the notification text.  Returns {"session_id": ..., "message": ...}
    or None if not found / already claimed / expired.
    """
    async with _get_compat_db() as db:
        # Claim ATOMICALLY first: a single conditional UPDATE is the source of
        # truth for who owns this pending row. Concurrent taps race here and
        # exactly one matches `claimed = 0`; the losers get no RETURNING row and
        # bail out, so we never create duplicate chat sessions for one tap.
        # (db.commit() is a no-op on the asyncpg compat shim without an explicit
        # transaction, so the old SELECT-then-UPDATE had no atomicity at all.)
        async with db.execute(
            "UPDATE proactive_pending SET claimed = 1 "
            "WHERE id = ? AND claimed = 0 RETURNING *",
            (pending_id,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            # Not found, expired-and-removed, or already claimed by a racer.
            return None

        # Expiry check AFTER winning the claim. If expired, release the claim so
        # the row stays consistent (claimed = 0) for the cleanup loop and the
        # caller sees "not claimable".
        try:
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                log.warning("Pending %s expired; ignoring claim", pending_id)
                await _release_claim(db, pending_id)
                return None
        except Exception:
            pass

        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Seed the session + first message inside ONE explicit transaction, so a
        # partial failure can never leave an orphan session without its message
        # (or vice versa). On any failure we roll back and compensate by
        # releasing the claim, so the notification can be retried rather than
        # being silently lost behind claimed = 1.
        try:
            async with db.transaction():
                await db.execute(
                    """INSERT INTO chat_sessions (id, user_id, title, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, row["user_id"], "Proactive notification", now, now),
                )
                await db.execute(
                    """INSERT INTO chat_messages
                       (id, session_id, role, content, created_at)
                       VALUES (?, ?, 'assistant', ?, ?)""",
                    (str(uuid.uuid4()), session_id, row["message"], now),
                )
        except Exception:
            log.exception(
                "claim_pending: session seed failed for %s; releasing claim", pending_id
            )
            await _release_claim(db, pending_id)
            return None

        log.info("Claimed pending %s → session %s", pending_id, session_id)
        return {"session_id": session_id, "message": row["message"]}


async def _release_claim(db, pending_id: str) -> None:
    """Best-effort release of a claim so a row can be retried/cleaned up."""
    try:
        await db.execute(
            "UPDATE proactive_pending SET claimed = 0 WHERE id = ?",
            (pending_id,),
        )
    except Exception:
        log.error("claim_pending: failed to release claim for %s", pending_id)
