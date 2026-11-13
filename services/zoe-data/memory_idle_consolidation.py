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

from user_filters import is_unidentified

logger = logging.getLogger(__name__)



def _is_real_user(user_id: Any) -> bool:
    """True only for a concrete, non-guest identity."""
    return bool(user_id) and not is_unidentified(user_id)


def _speaker_rejected(meta: Any) -> bool:
    """True when the voice speaker gate REJECTED this turn (``metadata.speaker_verified is false``, written by
    routers/chat.py). Only a rejection is recorded; no verdict reads as not-rejected. Unparseable metadata is
    not a rejection (same as self_review / memory_digest)."""
    if not meta:
        return False
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (ValueError, TypeError):
            return False
    return isinstance(meta, dict) and meta.get("speaker_verified") is False


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
# (alembic/versions/0014_memory_consolidation_state.py). This runtime path is a
# backward-compat / dev-convenience fallback only — it just CREATEs the table on
# an unmigrated dev/test DB. Run once per process, not on every 60s sweep. If the
# schema ever needs to evolve, do it through a new migration — keep the DDL below
# byte-for-byte in sync (or drop it).
#
# CRITICAL (least-privilege roles): we must NOT issue `CREATE TABLE` once the
# table exists. In PostgreSQL `CREATE TABLE IF NOT EXISTS` still requires the
# CREATE privilege even when the table already exists (the privilege is checked
# before the existence short-circuit), so a read/write-only runtime role would
# fail on the "no-op". So: check existence FIRST and only CREATE when the table
# is genuinely absent.
_state_table_ready = False


def _is_insufficient_privilege(exc: BaseException) -> bool:
    """True if exc is a Postgres insufficient-privilege error (SQLSTATE 42501)."""
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if sqlstate == "42501":
        return True
    name = type(exc).__name__.lower()
    return "insufficientprivilege" in name or "permission denied" in str(exc).lower()


async def _state_table_exists(conn) -> bool:
    """Whether memory_consolidation_state exists, without needing CREATE privilege.

    Postgres: ``to_regclass`` resolves the name in the search_path (NULL if absent)
    and needs no special privilege. Falls back to ``sqlite_master`` for the SQLite
    dev path (where ``to_regclass`` doesn't exist).
    """
    try:
        reg = await conn.fetchval("SELECT to_regclass('memory_consolidation_state')")
        return reg is not None
    except Exception:
        # Non-Postgres (e.g. SQLite dev): no to_regclass — use the catalog.
        row = await conn.fetchval(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='memory_consolidation_state'"
        )
        return row is not None


async def _ensure_state_table(conn) -> None:
    global _state_table_ready
    if _state_table_ready:
        return
    # Existence check FIRST: a least-privilege role (no schema CREATE) must never
    # issue a CREATE once Alembic 0014 has created the table.
    if await _state_table_exists(conn):
        _state_table_ready = True
        return
    # Table genuinely absent — unmigrated dev/test DB. Create it.
    try:
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
    except Exception as exc:
        # Defense-in-depth: if the role lacks CREATE but a concurrent creator
        # (the migration or another process) won the race so the table now
        # exists, continue — consolidation can proceed. If it's truly absent,
        # consolidation genuinely can't work: log and re-raise.
        if _is_insufficient_privilege(exc) and await _state_table_exists(conn):
            logger.warning(
                "memory_consolidation_state: no CREATE privilege but table already "
                "exists (created by Alembic 0014); continuing without CREATE: %s", exc,
            )
        else:
            logger.error(
                "memory_consolidation_state missing and could not be created "
                "(run Alembic migration 0014): %s", exc,
            )
            raise
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
               st.last_consolidated_at AS since,
               (
                   SELECT array_agg(m2.metadata ORDER BY m2.created_at::timestamptz)
                   FROM chat_messages m2
                   WHERE m2.session_id = c.session_id
                     AND m2.metadata IS NOT NULL
               ) AS metas
        FROM candidate c
        LEFT JOIN chat_sessions s ON s.id = c.session_id
        LEFT JOIN memory_consolidation_state st ON st.session_id = c.session_id
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
            # Consolidation watermark for this session (NULL on first pass). Folded
            # into the batch query so the sweep needs no per-session fetchval.
            "since": r["since"],
        })
    return out


def _fact_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("fact") or item.get("text") or item.get("content") or "").strip()
    return str(item or "").strip()


def _turn_epoch(row) -> Optional[float]:
    """Epoch seconds of one turn's ``at`` column, or None when it cannot be read."""
    try:
        at = row["at"]
        return at.timestamp() if hasattr(at, "timestamp") else None
    except Exception:  # noqa: BLE001
        return None


def _first_turn_epoch(rows) -> Optional[float]:
    """Epoch seconds of the oldest turn in ``rows``, or None when none can be read."""
    times = [t for t in (_turn_epoch(r) for r in rows or []) if t is not None]
    return min(times) if times else None


def _fact_said_at(item: Any, fact: str, rows) -> Optional[float]:
    """When the member said the words a mined ``fact`` came from, for the value-correction staleness check.

    The rule: the time of the NEWEST user turn that supports the fact - a turn holding the model's verbatim ``quote``
    (``fact_anchor``) or a turn whose own sentence entails the fact, whichever is newer (subject, value and attribute together:
    ``memory_authority.supporting_span``). A correction made after that turn makes the fact stale; a value restated after the
    correction is the latest word and is kept. When no supporting turn can be identified, the session's FIRST turn time: the
    fact may come from before a correction made mid-session, so any correction after the session began applies (the session's
    LAST turn time would let a pre-correction value through whenever the chat carried on after the correction)."""
    from memory_digest import fact_anchor
    import memory_authority

    quoted = isinstance(item, dict) and bool(str(item.get("quote") or "").strip())
    best = None
    for r in rows or []:
        if str(r["role"]) != "user" or not r["content"]:
            continue
        content = str(r["content"])
        # BOTH sets count: a turn holding the quote, and a turn whose own sentence entails the fact. With the quote alone, an
        # early quoted turn would beat a newer restatement and make the fact look older than it is.
        try:
            ok = quoted and fact_anchor(item, content) is not None
        except Exception:  # noqa: BLE001 - an unreadable turn supports nothing
            ok = False
        if not ok:
            try:
                ok = bool(memory_authority.supporting_span(fact, content))
            except Exception:  # noqa: BLE001
                ok = False
        ts = _turn_epoch(r) if ok else None
        if ts is not None and (best is None or ts > best):
            best = ts
    return best if best is not None else _first_turn_epoch(rows)


async def _fetch_transcript_rows(conn, session_id: str, since: Optional[Any]) -> list:
    """Read the session's turns since `since` from a (short-lived) pooled conn."""
    return await conn.fetch(
        """
        SELECT role, content, metadata, created_at::timestamptz AS at
        FROM chat_messages
        WHERE session_id = $1
          AND ($2::timestamptz IS NULL OR created_at::timestamptz > $2)
          AND COALESCE(metadata, '') NOT LIKE '%"off_record": true%'
        ORDER BY created_at::timestamptz
        """,
        session_id, since,
    )


async def _write_watermark(conn, session_id: str, user_id: str,
                           last_at: Any, turns: int) -> None:
    """Advance the consolidation watermark on a (short-lived) pooled conn."""
    await conn.execute(
        """
        INSERT INTO memory_consolidation_state(session_id, user_id, last_consolidated_at, turns_consolidated)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (session_id) DO UPDATE SET
            last_consolidated_at = excluded.last_consolidated_at,
            turns_consolidated   = memory_consolidation_state.turns_consolidated + excluded.turns_consolidated
        """,
        session_id, user_id, last_at, turns,
    )


# Consecutive all-zero passes this process has seen (reset by any pass that touched a fact).
_zero_streak = 0


def _warn_if_all_zero(session_id: str, user_id: str, turns: int, user_in: int, user_kept: int,
                      user_chars: int, facts: int, stored: int, rejected: int, skipped: int,
                      dropped: int, failed: int) -> None:
    """A pass over N>0 turns that stored / rejected / skipped / dropped / failed NOTHING is logged as a WARNING that names
    WHY, with a running streak. A row of zeros looked like "nothing to extract" for ten days (2026-11-01..11) with no way
    to tell that from "the session never reached the extractor". Counts only: never turn text. Never raises."""
    global _zero_streak
    try:
        if stored or rejected or skipped or dropped or failed or turns <= 0:
            _zero_streak = 0
            return
        if user_in == 0:
            reason = "no_user_turns"            # only assistant rows: nothing of the owner's to read
        elif user_kept == 0:
            reason = "all_user_turns_filtered"  # own_words (pasted / quoted) or the forget ledger took every turn
        elif facts == 0:
            reason = "extractor_returned_no_facts"   # the model read the owner's words and stated none
        else:
            reason = "facts_without_text"       # the model answered but every item had an empty fact
        _zero_streak += 1
        logger.warning(
            "MEMORY_IDLE_ALLZERO session=%s user=%s reason=%s turns=%d user_turns=%d->%d user_chars=%d "
            "extracted=%d zero_streak=%d",
            session_id, user_id, reason, turns, user_in, user_kept, user_chars, facts, _zero_streak)
    except Exception:  # noqa: BLE001 - observability must never cost the pass
        pass


async def consolidate_session(session_id: str, user_id: str,
                              since: Optional[Any] = None,
                              *, get_ctx=None) -> int:
    """Read the conversation's turns since `since`, extract durable facts over the
    WHOLE exchange, gate + store them, and advance the consolidation watermark.
    Returns the number of facts stored. Best-effort; never raises out.

    POOL DISCIPLINE: a pooled connection is held ONLY for the two discrete DB steps
    (read the transcript rows, then write the watermark) — NEVER across the Gemma
    extraction or the memory-service ingest, both of which can block for tens of
    seconds. Each step opens its own short-lived `get_db_ctx()` and releases before
    the slow work runs, so one idle session can no longer pin a pool slot for the
    duration of its LLM call.
    """
    if get_ctx is None:
        from db_pool import get_db_ctx as get_ctx

    # ── Step 1: short-lived conn — read the transcript rows, then release ──────
    async with get_ctx() as conn:
        rows = await _fetch_transcript_rows(conn, session_id, since)
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

    # USER turns only, to the extractor AND as the anchor: the extraction prompt says "user
    # turns only", and the old "role: content" transcript fed it the assistant's lines too, so
    # a fact the assistant SAID became a fact about the user (memory fidelity audit V5).
    # A turn the speaker gate REJECTED (someone at the member's panel who is not them) is never the member's own
    # words: it is left out, never mined as theirs (memory_digest's emotional pass does the same).
    user_rows_all = [r for r in rows if r["content"] and str(r["role"]) == "user"]
    user_turn_list = [str(r["content"]) for r in user_rows_all
                      if not _speaker_rejected(r["metadata"] if "metadata" in r else None)]
    if len(user_rows_all) != len(user_turn_list):
        logger.info("idle consolidation: session=%s skipped %d speaker-rejected turn(s)",
                    session_id, len(user_rows_all) - len(user_turn_list))
    user_rows_in = len(user_turn_list)   # for the all-zero reason (below)
    # Pasted content / another person's quoted speech is not the owner's own voice: only the owner's part of each
    # turn is mined (own_words; ZMB I1/I2/I4).
    try:
        import own_words
        user_turn_list = own_words.filter_turns(user_turn_list, "idle_consolidation")
    except Exception as exc:  # noqa: BLE001 - fail-open: ingest's anchored check is the second wall
        logger.debug("idle consolidation: own-words filter unavailable: %s", type(exc).__name__)
    # A turn naming an entity the user asked Zoe to forget is skipped, not re-mined (memory_forgotten, ZMB F3):
    # the chat rows are not erased, so without this the forgotten name returns at the next idle pass.
    # FAIL-CLOSED: a ledger outage returns BEFORE the watermark moves, so these turns are re-read on the next sweep.
    try:
        from memory_digest import _skip_forgotten_turns
        user_turn_list = await _skip_forgotten_turns(user_id, user_turn_list, "idle_consolidation")
    except Exception as exc:  # noqa: BLE001 - LedgerUnavailable (or any failure to consult the ledger): defer
        logger.warning("idle consolidation: forget ledger unavailable session=%s (%s) - deferred, watermark kept",
                       session_id, type(exc).__name__)
        return 0
    user_turns = "\n".join(user_turn_list)
    transcript = user_turns
    user_turns_kept = len(user_turn_list)

    # ── Step 2: NO pooled connection held across Gemma extraction + ingest ─────
    try:
        from memory_digest import _extract_facts_with_gemma, fact_anchor
        # every turn skipped (all named a forgotten entity) = nothing to mine, watermark still advances
        facts = await _extract_facts_with_gemma(transcript) if transcript.strip() else []
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

    rejected = skipped = dropped = failed = 0
    for item in facts or []:
        text = _fact_text(item)
        if not text:
            continue
        try:
            ok, _reason = is_storable_fact(text)
        except Exception:
            ok, _reason = True, ""
        if not ok:
            rejected += 1
            try:
                from memory_reject_ledger import record_reject
                record_reject("idle_consolidation", _reason)
            except Exception:
                pass
            continue
        try:
            import value_correction

            # the time of the turns that SUPPORT this fact (the first turn when none can be told): see _fact_said_at
            stale = value_correction.superseded_by_correction(user_id, text, said_at=_fact_said_at(item, text, rows))
        except Exception:  # noqa: BLE001 - the check never costs the session its facts
            stale = False
        if stale:
            # the member corrected this value AFTER these turns were said (value_correction): the old value is not re-filed
            skipped += 1
            logger.info("VALUE_CORRECTION_STALE lane=idle_consolidation user=%s session=%s", user_id, session_id)
            continue
        # Stable dedup id so a re-run of the same session before the watermark
        # advances collapses to one row (mirrors the voice path's hash behaviour).
        turn_id = "idle:" + hashlib.sha1(f"{session_id}:{text}".encode("utf-8")).hexdigest()[:16]
        try:
            outcome = await _ingest_or_supersede(
                svc, text, user_id=user_id, source="idle_consolidation",
                session_id=session_id, user_turn_id=turn_id,
                memory_type="fact", confidence=0.8, tags=["idle", "self"],
                anchor_text=fact_anchor(item, user_turns) or "",
            )
            # ``stored`` used to count every call that did not raise — including an
            # equivalent-fact "skip" and a PII/opt-out "dropped" — so the line overstated what
            # reached the palace (log 09-28: stored=4, audit: 3 ingests). Count only writes.
            if outcome == "skip":
                skipped += 1
            elif outcome == "dropped":
                dropped += 1
            else:  # "stored" (or a legacy None from an older stub)
                stored += 1
        except Exception as exc:
            failed += 1
            logger.warning("idle consolidation ingest failed: %s", exc)

    # ── Step 3: fresh short-lived conn — advance the watermark, then release ───
    last_at = rows[-1]["at"]
    async with get_ctx() as conn:
        await _write_watermark(conn, session_id, user_id, last_at, len(rows))
    _warn_if_all_zero(session_id, user_id, len(rows), user_rows_in, user_turns_kept,
                      len(user_turns), len(facts or []), stored, rejected, skipped, dropped, failed)
    logger.info("MEMORY_IDLE_CONSOLIDATE session=%s user=%s turns=%d stored=%d "
                "rejected=%d skipped=%d dropped=%d failed=%d",
                session_id, user_id, len(rows), stored, rejected, skipped, dropped, failed)
    return stored


async def run_idle_consolidation_sweep() -> dict:
    """One pass: find idle conversations and consolidate each. Returns counts."""
    if not _enabled():
        return {"enabled": False}
    result = {"enabled": True, "sessions": 0, "stored": 0}
    from db_pool import get_db_ctx
    # Short-lived listing connection: ensure the state table + list idle sessions,
    # then RELEASE it before doing any per-session work. No pooled connection is
    # held across the batch loop — each session opens its own short-lived conns.
    async with get_db_ctx() as conn:
        await _ensure_state_table(conn)
        sessions = await find_idle_sessions(conn)
    for s in sessions:
        # Isolate per-session failures: a Gemma OOM/timeout or a watermark write
        # error on one session must not abort the rest of the batch. The failing
        # session's watermark is left un-advanced, so it retries next sweep.
        try:
            # `since` comes straight from the batch query (no per-session round-trip).
            stored = await consolidate_session(s["session_id"], s["user_id"], s["since"])
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
            # type name first: a bare TimeoutError / CancelledError has an EMPTY str() (21 of 55 live lines were blank)
            logger.warning("idle consolidation sweep failed (non-fatal): %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(CHECK_INTERVAL)
