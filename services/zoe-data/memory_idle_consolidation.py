"""Idle-triggered conversation consolidation — the "live → idle → store" loop.

Every chat/voice turn is already stored VERBATIM in `chat_messages` the instant it
happens (immediate working/episodic memory). This module adds the second stage of
Jason's model: when a conversation goes IDLE for a few minutes, re-read the whole
conversation ONCE, extract the durable facts, and save them properly through the
write-quality gate. Event-driven (idle), NOT a nightly batch — so a thing told in
the morning is consolidated minutes later and recallable in the afternoon.

Why idle + whole-conversation (not per-turn): the model sees the full exchange, so
a fact mentioned early and corrected late becomes ONE clean fact, not fragments —
which is also why this produces less junk than per-turn extraction.

Safety: flagged OFF by default (ZOE_IDLE_CONSOLIDATION_ENABLED) — lab-prove on the
zoe-core Samantha tests before enabling in prod. Never blocks a turn (runs in a
background loop). Reuses the existing Gemma extractor + write-quality gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import Counter
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GUEST_USERS = ("guest", "voice-daemon", "")


def _is_real_user(user_id: Any) -> bool:
    """True only for a concrete, non-guest identity."""
    return bool(user_id) and str(user_id).strip() not in _GUEST_USERS


def _user_from_metadata(meta: Any) -> Optional[str]:
    """Extract a user_id from a chat_messages.metadata value (text JSON or dict)."""
    if not meta:
        return None
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (ValueError, TypeError):
            return None
    if isinstance(meta, dict):
        uid = meta.get("user_id")
        return str(uid) if _is_real_user(uid) else None
    return None


def _resolve_owner(rows: list, session_user_id: Any = None) -> Optional[str]:
    """Resolve the conversation owner from the per-turn metadata.

    Auth is recorded per-turn in chat_messages.metadata {"user_id": ...} even
    though chat_sessions.user_id stays 'guest'. Pick the most-recent non-guest
    user, breaking ties by frequency. Falls back to the session user_id only
    when it is itself a real (non-guest) identity. Returns None when no real
    user can be resolved — caller skips the session.
    """
    counts: Counter = Counter()
    most_recent: Optional[str] = None
    # rows arrive oldest→newest, so the last real user seen wins recency.
    for r in rows:
        uid = _user_from_metadata(r["metadata"] if "metadata" in r else None)
        if uid:
            counts[uid] += 1
            most_recent = uid
    if most_recent:
        # If one user dominates by count, prefer them; else use most recent.
        top, n = counts.most_common(1)[0]
        if n > counts.get(most_recent, 0):
            return top
        return most_recent
    if _is_real_user(session_user_id):
        return str(session_user_id)
    return None


def _enabled() -> bool:
    return os.environ.get("ZOE_IDLE_CONSOLIDATION_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


# A conversation is "idle" after this many seconds of no new turn.
IDLE_SECONDS = _int_env("ZOE_IDLE_CONSOLIDATION_IDLE_S", 180)
# Don't reach back further than this (avoid reprocessing ancient sessions on boot).
LOOKBACK_SECONDS = _int_env("ZOE_IDLE_CONSOLIDATION_LOOKBACK_S", 3600)
# How often the loop checks for newly-idle conversations.
CHECK_INTERVAL = _int_env("ZOE_IDLE_CONSOLIDATION_CHECK_S", 60)
# Need a real exchange before it's worth consolidating.
MIN_TURNS = _int_env("ZOE_IDLE_CONSOLIDATION_MIN_TURNS", 2)


# Schema source of truth is the Alembic migration
# (alembic/versions/0014_memory_consolidation_state.py). This runtime CREATE is a
# backward-compat / dev-convenience fallback only: `IF NOT EXISTS` makes it a
# harmless no-op once the migration has run, so it never errors and never drifts
# from the migration. It also keeps unmigrated dev/test DBs working. Run once per
# process, not on every 60s sweep. If the schema ever needs to evolve, do it
# through a new migration — keep this DDL byte-for-byte in sync (or drop it).
_state_table_ready = False


async def _ensure_state_table(conn) -> None:
    global _state_table_ready
    if _state_table_ready:
        return
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_consolidation_state (
            session_id text PRIMARY KEY,
            user_id text NOT NULL,
            last_consolidated_at timestamptz NOT NULL DEFAULT now(),
            turns_consolidated int NOT NULL DEFAULT 0
        )
        """
    )
    _state_table_ready = True


async def find_idle_sessions(conn) -> list[dict]:
    """Sessions that have gone idle and carry NEW turns since the last consolidation.

    The owning user is resolved from the per-turn message metadata (auth is recorded
    per-turn even though chat_sessions.user_id stays 'guest'), falling back to a real
    chat_sessions.user_id only when present. Sessions with no resolvable real user are
    skipped. created_at is stored as text → cast to timestamptz for the time math.
    """
    rows = await conn.fetch(
        """
        WITH candidate AS (
            SELECT m.session_id AS session_id,
                   max(m.created_at::timestamptz) AS last_at,
                   count(*)     AS n
            FROM chat_messages m
            LEFT JOIN memory_consolidation_state st ON st.session_id = m.session_id
            WHERE m.created_at::timestamptz > now() - ($1::int * interval '1 second')
              AND (st.last_consolidated_at IS NULL
                   OR m.created_at::timestamptz > st.last_consolidated_at)
            GROUP BY m.session_id
            HAVING max(m.created_at::timestamptz) < now() - ($2::int * interval '1 second')
               AND count(*) >= $3::int
        )
        SELECT c.session_id AS session_id,
               s.user_id    AS session_user_id,
               c.last_at    AS last_at,
               c.n          AS n,
               (
                   SELECT array_agg(m2.metadata ORDER BY m2.created_at::timestamptz)
                   FROM chat_messages m2
                   WHERE m2.session_id = c.session_id
                     AND m2.metadata IS NOT NULL
               ) AS metas
        FROM candidate c
        LEFT JOIN chat_sessions s ON s.id = c.session_id
        """,
        LOOKBACK_SECONDS, IDLE_SECONDS, MIN_TURNS,
    )
    out: list[dict] = []
    for r in rows:
        metas = r["metas"] or []
        meta_rows = [{"metadata": m} for m in metas]
        owner = _resolve_owner(meta_rows, r["session_user_id"])
        if not owner:
            continue  # no real user → don't know whose memory to write
        out.append({
            "session_id": r["session_id"],
            "user_id": owner,
            "last_at": r["last_at"],
            "n": r["n"],
        })
    return out


def _fact_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("fact") or item.get("text") or item.get("content") or "").strip()
    return str(item or "").strip()


async def consolidate_session(conn, session_id: str, user_id: str,
                              since: Optional[Any] = None) -> int:
    """Read the conversation's turns since `since`, extract durable facts over the
    WHOLE exchange, gate + store them, and advance the consolidation watermark.
    Returns the number of facts stored. Best-effort; never raises out."""
    rows = await conn.fetch(
        """
        SELECT role, content, metadata, created_at::timestamptz AS at
        FROM chat_messages
        WHERE session_id = $1
          AND ($2::timestamptz IS NULL OR created_at::timestamptz > $2)
        ORDER BY created_at::timestamptz
        """,
        session_id, since,
    )
    if len(rows) < MIN_TURNS:
        return 0

    # Re-resolve the owner from per-turn metadata (the session's user_id is the
    # caller's fallback). Skip if no real user can be determined — we must never
    # write one user's facts under 'guest' or someone else.
    owner = _resolve_owner(rows, user_id)
    if not owner:
        logger.debug("idle consolidation: no real user for session=%s — skipped", session_id)
        return 0
    user_id = owner

    transcript = "\n".join(f"{r['role']}: {r['content']}" for r in rows if r["content"])

    try:
        from memory_digest import _extract_facts_with_gemma
        facts = await _extract_facts_with_gemma(transcript)
    except Exception as exc:
        # Honour the "never raises out" contract: a Gemma failure (OOM/timeout/
        # import) must not abort the whole sweep — leave the watermark un-advanced
        # so this session is retried next pass, and move on.
        logger.warning("idle consolidation: fact extraction failed session=%s: %s",
                       session_id, exc)
        return 0

    stored = 0
    try:
        from memory_service import get_memory_service
        from memory_quality import is_storable_fact
        from expert_dispatch import _ingest_or_supersede
        svc = get_memory_service()
    except Exception as exc:
        logger.warning("idle consolidation: deps unavailable: %s", exc)
        return 0

    for item in facts or []:
        text = _fact_text(item)
        if not text:
            continue
        try:
            ok, _reason = is_storable_fact(text)
        except Exception:
            ok = True
        if not ok:
            continue
        # Stable dedup id so a re-run of the same session before the watermark
        # advances collapses to one row (mirrors the voice path's hash behaviour).
        turn_id = "idle:" + hashlib.sha1(f"{session_id}:{text}".encode("utf-8")).hexdigest()[:16]
        try:
            await _ingest_or_supersede(
                svc, text, user_id=user_id, source="idle_consolidation",
                session_id=session_id, user_turn_id=turn_id,
                memory_type="fact", confidence=0.8, tags=["idle", "self"],
            )
            stored += 1
        except Exception as exc:
            logger.debug("idle consolidation ingest failed: %s", exc)

    last_at = rows[-1]["at"]
    await conn.execute(
        """
        INSERT INTO memory_consolidation_state(session_id, user_id, last_consolidated_at, turns_consolidated)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (session_id) DO UPDATE SET
            last_consolidated_at = excluded.last_consolidated_at,
            turns_consolidated   = memory_consolidation_state.turns_consolidated + excluded.turns_consolidated
        """,
        session_id, user_id, last_at, len(rows),
    )
    logger.info("MEMORY_IDLE_CONSOLIDATE session=%s user=%s turns=%d stored=%d",
                session_id, user_id, len(rows), stored)
    return stored


async def run_idle_consolidation_sweep() -> dict:
    """One pass: find idle conversations and consolidate each. Returns counts."""
    if not _enabled():
        return {"enabled": False}
    result = {"enabled": True, "sessions": 0, "stored": 0}
    from db_pool import get_db_ctx
    async with get_db_ctx() as conn:
        await _ensure_state_table(conn)
        sessions = await find_idle_sessions(conn)
        for s in sessions:
            # Isolate per-session failures: a Gemma OOM/timeout or a watermark write
            # error on one session must not abort the rest of the batch. The failing
            # session's watermark is left un-advanced, so it retries next sweep.
            try:
                since = await conn.fetchval(
                    "SELECT last_consolidated_at FROM memory_consolidation_state WHERE session_id=$1",
                    s["session_id"],
                )
                stored = await consolidate_session(conn, s["session_id"], s["user_id"], since)
                result["sessions"] += 1
                result["stored"] += stored
            except Exception as exc:
                logger.warning("idle consolidation: session %s failed (skipping): %s",
                               s.get("session_id"), exc)
    return result


async def start_idle_consolidation_loop() -> None:
    """Background loop: every CHECK_INTERVAL, consolidate any newly-idle conversation.
    Self-gates on the enable flag EACH iteration so the task persists and picks up a
    dynamically-flipped ZOE_IDLE_CONSOLIDATION_ENABLED without a service restart."""
    logged_disabled = False
    while True:
        if not _enabled():
            if not logged_disabled:
                logger.info("idle consolidation disabled (ZOE_IDLE_CONSOLIDATION_ENABLED=0); loop idling")
                logged_disabled = True
            await asyncio.sleep(CHECK_INTERVAL)
            continue
        if logged_disabled:
            logger.info("idle consolidation re-enabled (idle=%ds check=%ds lookback=%ds)",
                        IDLE_SECONDS, CHECK_INTERVAL, LOOKBACK_SECONDS)
            logged_disabled = False
        try:
            res = await run_idle_consolidation_sweep()
            if res.get("sessions"):
                logger.info("idle consolidation sweep: %s", res)
        except Exception as exc:
            logger.warning("idle consolidation sweep failed (non-fatal): %s", exc)
        await asyncio.sleep(CHECK_INTERVAL)
