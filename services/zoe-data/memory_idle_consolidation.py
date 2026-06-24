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
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


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


# Run the bootstrap DDL once per process, not on every 60s sweep. (The table is a
# local watermark store; if a future increment needs to evolve its schema, do that
# through the migration framework, not this CREATE-IF-NOT-EXISTS.)
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

    Joins chat_sessions for the owning user_id (skips guest/unknown). created_at is
    stored as text → cast to timestamptz for the time math.
    """
    rows = await conn.fetch(
        """
        SELECT m.session_id AS session_id,
               s.user_id    AS user_id,
               max(m.created_at::timestamptz) AS last_at,
               count(*)     AS n
        FROM chat_messages m
        JOIN chat_sessions s ON s.id = m.session_id
        LEFT JOIN memory_consolidation_state st ON st.session_id = m.session_id
        WHERE m.created_at::timestamptz > now() - ($1::int * interval '1 second')
          AND (st.last_consolidated_at IS NULL
               OR m.created_at::timestamptz > st.last_consolidated_at)
          AND s.user_id IS NOT NULL AND s.user_id NOT IN ('guest', '')
        GROUP BY m.session_id, s.user_id
        HAVING max(m.created_at::timestamptz) < now() - ($2::int * interval '1 second')
           AND count(*) >= $3::int
        """,
        LOOKBACK_SECONDS, IDLE_SECONDS, MIN_TURNS,
    )
    return [dict(r) for r in rows]


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
        SELECT role, content, created_at::timestamptz AS at
        FROM chat_messages
        WHERE session_id = $1
          AND ($2::timestamptz IS NULL OR created_at::timestamptz > $2)
        ORDER BY created_at::timestamptz
        """,
        session_id, since,
    )
    if len(rows) < MIN_TURNS:
        return 0
    transcript = "\n".join(f"{r['role']}: {r['content']}" for r in rows if r["content"])

    from memory_digest import _extract_facts_with_gemma
    facts = await _extract_facts_with_gemma(transcript)

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
