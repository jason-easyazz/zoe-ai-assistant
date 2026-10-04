"""Delivery ledger — flag-dark (``ZOE_PROACTIVE_LEDGER``, default OFF, read per call).

PR 1 of the pull-not-push inbox (docs/research/pull-not-push-inbox-2026-10-04.md §3.1,
§3.5, §5). ``proactive_candidates`` records that a ``[RAISE …]`` / ``[Today]`` block went
out with a reply — not whether the reply VOICED it (PR #1821 found the brain voicing a
greeting raise 0/5 times, indistinguishable from success in production), and not what the
person did next. This module is that evidence, and nothing else: it changes no reply, no
block, no spacing rule, no candidate row, and never speaks.

  * **Writer** — ``record`` / ``record_for_candidate``: one ``proactive_deliveries`` row per
    item a conversation carried, from ``selector._settle`` (a raise) and
    ``selector.mark_brief_surfaced`` (a brief line). IDEMPOTENT: ``idem_key`` UNIQUE, so a
    retried or double settle inserts nothing. ``voiced`` is decided at write time from the
    lane's reply text: one of the item's anchor words in the reply = 1, none = 0 (outcome
    ``undelivered`` at once), nothing to check (no anchors, or no reply passed) = NULL —
    ``unknown``, never ``undelivered``, because nothing proves it was not voiced (the
    ``arrival.evaluate_pending_responses`` discipline).
  * **Sweep** — ``sweep``: closes rows (called from the engine slow loop). An event (Notify:
    information, no answer expected) is ``accepted`` when voiced. Anything else (a Question)
    waits ``RESPONSE_WINDOW_S``, then the member's FIRST next turn decides: a deterministic
    command, or a turn sharing no anchor word, is ``ignored``; one that shares an anchor is
    ``accepted``; no turn at all is ``ignored``. A row the sweep cannot judge by its
    ``expires_at`` closes ``unknown`` — nothing strands. Re-running is a no-op
    (``WHERE outcome IS NULL``).

The sweep reads chat turns with the same SQL shape as ``arrival._first_user_turn``
(member-wide, not session-bound: a voice session id is not always a chat session id).
Logs carry counts, ids' kinds and outcomes — never item text, reply text or utterances.
Flag off: every entry point returns before any DB access.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
RESPONSE_WINDOW_S = 600       # record §3.5: the person's next turn "within … the next 10 min"
JUDGE_BY = timedelta(hours=24)  # a row the sweep could not judge by then closes ``unknown``
_REPLY_CAP = 4000             # chars of reply kept for the voiced check; never stored
_SWEEP_BATCH = 200
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def ledger_enabled() -> bool:
    return (os.environ.get("ZOE_PROACTIVE_LEDGER", "") or "").strip().lower() in _TRUTHY


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _parse(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── the reply the lane streamed (collected only with the flag on) ─────────────
class ReplyTap:
    """Collects a turn's real reply text for the voiced check. Inactive (flag off) it
    holds nothing and ``kwargs()`` is ``{}``, so the lanes' settle calls are unchanged."""

    __slots__ = ("active", "_parts", "_n")

    def __init__(self, active: bool) -> None:
        self.active = active
        self._parts: list[str] = []
        self._n = 0

    def add(self, delta: str) -> None:
        if self.active and self._n < _REPLY_CAP:
            self._parts.append(delta)
            self._n += len(delta)

    def kwargs(self) -> dict:
        return {"reply": "".join(self._parts)[:_REPLY_CAP]} if self.active else {}


def reply_tap() -> ReplyTap:
    return ReplyTap(ledger_enabled())


# ── voiced? ───────────────────────────────────────────────────────────────────
def voiced_in(reply: str | None, cue_words: str) -> int | None:
    """1 / 0 when the reply can be checked against the item's anchors, else None."""
    cues = {c for c in (cue_words or "").split() if c}
    if reply is None or not cues:
        return None
    from proactive.selector import _words

    return 1 if _words(reply) & cues else 0


def idem_key(user_id: str, session_id: str, kind: str, source_ref: str, delivered_by: str) -> str:
    return "|".join((user_id, session_id, kind, source_ref, delivered_by))


# ── writer ────────────────────────────────────────────────────────────────────
async def record(db, *, user_id: str, candidate_id: str | None, kind: str, source_ref: str,
                 shape: str, delivered_by: str, session_id: str, cue_words: str,
                 now: datetime, reply: str | None) -> bool:
    """Insert the delivery (idempotent). True when a NEW row was written. Never raises."""
    if not ledger_enabled():
        return False
    try:
        voiced = voiced_in(reply, cue_words)
        undelivered = voiced == 0
        stamp = _iso(now)
        cur = await db.execute(
            """INSERT INTO proactive_deliveries (id, idem_key, user_id, candidate_id, kind,
                   source_ref, shape, delivered_by, session_id, cue_words, voiced, surfaced_at,
                   expires_at, outcome, outcome_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (idem_key) DO NOTHING""",
            (uuid.uuid4().hex, idem_key(user_id, session_id, kind, source_ref, delivered_by),
             user_id, candidate_id, kind, source_ref, shape, delivered_by, session_id,
             cue_words or "", voiced, stamp, _iso(now + JUDGE_BY),
             "undelivered" if undelivered else None, stamp if undelivered else None, stamp),
        )
        new = (getattr(cur, "rowcount", 1) or 0) > 0
    except Exception as exc:  # noqa: BLE001 — the ledger must never break a settle
        logger.warning("proactive-ledger: record failed user=%s: %r", user_id, exc)
        return False
    if new:
        logger.info("PROACTIVE_LEDGER user=%s kind=%s shape=%s by=%s voiced=%s%s", user_id, kind,
                    shape, delivered_by, "?" if voiced is None else voiced,
                    " outcome=undelivered" if undelivered else "")
    return new


async def record_for_candidate(db, *, candidate_id: str, user_id: str, session_id: str,
                               shape: str, now: datetime, reply: str | None) -> bool:
    """A raise just settled: read the candidate's identity and anchors, record the delivery."""
    if not ledger_enabled():
        return False
    try:
        async with db.execute(
            "SELECT kind, source_ref, cue_words FROM proactive_candidates WHERE id = ?",
            (candidate_id,),
        ) as cur:
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive-ledger: candidate read failed user=%s: %r", user_id, exc)
        return False
    if not row:
        return False  # the candidate went away: nothing to identify the item by
    return await record(
        db, user_id=user_id, candidate_id=candidate_id, kind=str(row[0]), source_ref=str(row[1]),
        shape=shape, delivered_by="turn", session_id=session_id, cue_words=str(row[2] or ""),
        now=now, reply=reply)


# ── sweep ─────────────────────────────────────────────────────────────────────
async def _user_turns(db, user_id: str, start: str, end: str, limit: int = 1) -> list[str]:
    """The member's own chat turns in ``(start, end]``, oldest first. The SQL shape of
    ``arrival._first_user_turn`` (Postgres: ``::timestamptz``, ``message_owner_expr``)."""
    from user_filters import message_owner_expr

    async with db.execute(
        f"""SELECT cm.content
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
              AND cm.created_at::timestamptz > ?::timestamptz
              AND cm.created_at::timestamptz <= ?::timestamptz
              AND ({message_owner_expr()}) = ?
            ORDER BY cm.created_at::timestamptz ASC
            LIMIT {int(limit)}""",
        (start, end, user_id),
    ) as cur:
        rows = await cur.fetchall()
    return [str(r[0] or "") for r in rows]


def _is_command(text: str) -> bool:
    """True for a deterministic intent hit. RAISES on an unknown shape: the caller leaves
    the row open rather than label a turn it could not read."""
    from intent_router import detect_intent

    return detect_intent(text, log_miss=False) is not None


def judge_turn(turns: list[str], cue_words: str, voiced: int | None) -> str:
    """The outcome of a Question from the member's first turn after it (the intent router
    decides "command"; it RAISES on an unreadable turn and the sweep leaves the row open).
    ``ignored`` needs a voiced item; an unverified one that was not taken up is ``unknown``."""
    from proactive.selector import _words

    cues = {c for c in (cue_words or "").split() if c}
    if turns:
        text = turns[0]
        if not _is_command(text) and cues and _words(text) & cues:
            return "accepted"
    return "ignored" if voiced == 1 else "unknown"


async def sweep(*, now: datetime | None = None) -> int:
    """Close the ledger rows whose evidence is in. Returns how many closed. A no-op (no
    DB) with the flag off. Never raises."""
    if not ledger_enabled():
        return 0
    from db_compat import get_compat_db

    closed = 0
    now = now or datetime.now(timezone.utc)
    try:
        async with get_compat_db() as db:
            async with db.execute(
                "SELECT id, user_id, kind, cue_words, voiced, surfaced_at, expires_at "
                "FROM proactive_deliveries WHERE outcome IS NULL ORDER BY surfaced_at LIMIT ?",
                (_SWEEP_BATCH,),
            ) as cur:
                rows = [tuple(r) for r in await cur.fetchall()]
            for rid, uid, kind, cues, voiced, surfaced_at, expires_at in rows:
                surfaced = _parse(surfaced_at)
                judge_by = _parse(expires_at) or (surfaced or now) + JUDGE_BY
                outcome: str | None = None
                if surfaced is None:
                    outcome = "unknown"
                elif kind == "event":  # Notify: information, nothing to answer
                    outcome = "accepted" if voiced == 1 else "unknown"
                elif surfaced + timedelta(seconds=RESPONSE_WINDOW_S) <= now:
                    try:
                        turns = await _user_turns(
                            db, uid, _iso(surfaced),
                            _iso(surfaced + timedelta(seconds=RESPONSE_WINDOW_S)))
                        outcome = judge_turn(turns, cues or "", voiced)
                    except Exception as exc:  # noqa: BLE001 — unreadable: retry next tick
                        logger.debug("proactive-ledger: judge deferred id=%s: %r", rid, exc)
                if outcome is None and now >= judge_by:
                    outcome = "unknown"  # never strands
                if outcome is None:
                    continue
                cur = await db.execute(
                    "UPDATE proactive_deliveries SET outcome = ?, outcome_at = ? "
                    "WHERE id = ? AND outcome IS NULL", (outcome, _iso(now), rid))
                if (getattr(cur, "rowcount", 1) or 0) > 0:
                    closed += 1
                    logger.info("PROACTIVE_LEDGER_OUTCOME user=%s kind=%s outcome=%s", uid, kind,
                                outcome)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive-ledger: sweep failed: %r", exc)
    return closed
