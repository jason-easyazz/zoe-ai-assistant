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
    retried or double settle inserts nothing. A new row is open (``outcome`` NULL).
  * **Sweep** — ``sweep`` (engine slow loop) closes rows from what chat PERSISTED, so the
    brain lanes are untouched (they are voice-path files: any edit there needs a Jetson
    replay-gate run). Both lanes persist the reply the person HEARD to ``chat_messages``
    (``routers.chat._save_chat_message``; the streaming voice lane saves what was spoken),
    and the user's turn either before the stream (chat, streaming voice) or together with the
    reply after it (non-streaming voice). So the exchange's REPLY is the first assistant row
    of the delivery's session STRICTLY AFTER the settle (never before it: that would be the
    previous turn's reply; auxiliary rows saved within seconds of it count as part of it), and
    the person's "next turn" is their first user row strictly after that reply at MICROSECOND
    precision, skipping any copy of the triggering utterance (``trigger_key``): the
    non-streaming voice lane saves a second copy of it milliseconds before the reply.
      - ``voiced``: the reply carries one of the item's anchor words (1) or none (0). 0 closes
        the row ``undelivered`` — the injected-but-dropped case. NULL (no anchors) is
        unverifiable: ``unknown``, never ``undelivered``, because nothing proves it was not
        voiced (the ``arrival.evaluate_pending_responses`` discipline).
      - an ``event`` (Notify: information, no answer expected) is ``accepted`` once voiced.
      - anything else (a Question) waits ``RESPONSE_WINDOW_S`` after the reply, then the
        member's FIRST next turn decides: a deterministic command, or a turn sharing no anchor
        word, is ``ignored``; one that shares an anchor is ``accepted``; no turn at all is
        ``ignored``; an unverified item that was not taken up is ``unknown``.
      - no persisted reply yet, or a chat read / intent-router error: the row stays open and
        is retried next tick; unjudged by its ``expires_at`` it closes ``unknown`` — nothing
        strands. Re-running is a no-op (``WHERE outcome IS NULL``).

The chat reads are Postgres SQL in the shape of ``arrival._first_user_turn`` (member-wide
turns; session-bound reply). Logs carry counts, item kinds and outcomes — never item text,
reply text or utterances; reply text is checked and discarded, never stored. Flag off: every
entry point returns before any DB access.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
RESPONSE_WINDOW_S = 600       # record §3.5: the person's next turn "within … the next 10 min"
JUDGE_BY = timedelta(hours=24)  # a row the sweep could not judge by then closes ``unknown``
_REPLY_WITHIN = timedelta(minutes=5)  # the reply is persisted soon after the settle; a later one is another turn's
_AUX_WITHIN = timedelta(seconds=5)    # assistant rows saved this close behind the reply are part of it
_US_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SWEEP_BATCH = 200
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def ledger_enabled() -> bool:
    return (os.environ.get("ZOE_PROACTIVE_LEDGER", "") or "").strip().lower() in _TRUTHY


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _iso_us(dt: datetime) -> str:
    """Microsecond-precision UTC stamp: a chat bound truncated to the second would let a
    row saved milliseconds BEFORE a reply sort after it."""
    return dt.astimezone(timezone.utc).strftime(_US_FMT)


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


def _stem(token: str) -> str:
    """Cheap singular form so number and possessives never decide voiced: appointments ->
    appointment, surgeries -> surgery, boxes -> box, dentist's -> dentist."""
    t = re.sub(r"'s?$", "", token.strip("'"))
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 4 and t.endswith(("sses", "xes", "ches", "shes")):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in re.findall(r"[a-z0-9':-]+", (text or "").lower())}


def voiced_in(reply: str | None, cue_words: str) -> int | None:
    """1 / 0 when the reply can be checked against the item's anchors, else None. Both sides
    are stemmed (a plural anchor must match a singular reply and the reverse)."""
    cues = {_stem(c) for c in (cue_words or "").lower().split() if c}
    if reply is None or not cues:
        return None
    return 1 if _stems(reply) & cues else 0


def idem_key(user_id: str, session_id: str, kind: str, source_ref: str, delivered_by: str,
             day: str) -> str:
    """One delivery event. ``day`` (household local date) is part of it: a permanent session
    (Telegram) or a raise again after the cooldown is a LATER delivery, not a retry."""
    return "|".join((user_id, session_id, kind, source_ref, delivered_by, day))


def _local_day(now: datetime) -> str:
    from time_utils import zoe_timezone

    return now.astimezone(zoe_timezone()).date().isoformat()


# ── writer ────────────────────────────────────────────────────────────────────
async def record(db, *, user_id: str, candidate_id: str | None, kind: str, source_ref: str,
                 shape: str, delivered_by: str, session_id: str, cue_words: str,
                 now: datetime, trigger: str = "") -> bool:
    """Insert the delivery, open (idempotent). True when a NEW row was written. Never raises."""
    if not ledger_enabled():
        return False
    try:
        stamp = _iso_us(now)
        cur = await db.execute(
            """INSERT INTO proactive_deliveries (id, idem_key, user_id, candidate_id, kind,
                   source_ref, shape, delivered_by, session_id, cue_words, trigger_key,
                   surfaced_at, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (idem_key) DO NOTHING""",
            (uuid.uuid4().hex,
             idem_key(user_id, session_id, kind, source_ref, delivered_by, _local_day(now)),
             user_id, candidate_id, kind, source_ref, shape, delivered_by, session_id,
             cue_words or "", trigger or "", stamp, _iso_us(now + JUDGE_BY), stamp),
        )
        new = (getattr(cur, "rowcount", 1) or 0) > 0
    except Exception as exc:  # noqa: BLE001 — the ledger must never break a settle
        logger.warning("proactive-ledger: record failed user=%s: %r", user_id, exc)
        return False
    if new:
        logger.info("PROACTIVE_LEDGER user=%s kind=%s shape=%s by=%s", user_id, kind, shape,
                    delivered_by)
    return new


async def record_for_candidate(db, *, candidate_id: str, user_id: str, session_id: str,
                               shape: str, now: datetime, trigger: str = "") -> bool:
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
        now=now, trigger=trigger)


# ── sweep: what chat persisted ────────────────────────────────────────────────
async def _replies_after(db, session_id: str, since: str,
                         until: str) -> list[tuple[str, datetime]]:
    """Assistant rows of ``session_id`` in ``(since, until]``, oldest first (a few): the
    reply this delivery rode in on (the text the person heard) and when each was persisted.
    ``since`` is the settle itself: a row at or before it is the PREVIOUS turn's, never this
    one's; ``until`` keeps a lost save from borrowing a LATER turn's reply. Postgres SQL."""
    async with db.execute(
        """SELECT cm.content, cm.created_at::timestamptz AS reply_at
           FROM chat_messages cm
           WHERE cm.session_id = ? AND cm.role = 'assistant'
             AND cm.created_at::timestamptz > ?::timestamptz
             AND cm.created_at::timestamptz <= ?::timestamptz
           ORDER BY cm.created_at::timestamptz ASC
           LIMIT 5""",
        (session_id, since, until),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        at = _parse(r[1])
        if at:
            out.append((str(r[0] or ""), at))
    return out


async def _user_turns(db, user_id: str, start: str, end: str, limit: int = 4) -> list[str]:
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


def judge_turn(turns: list[str], cue_words: str, voiced: int | None, trigger: str = "") -> str:
    """The outcome of a Question from the member's first turn after the reply (the intent
    router decides "command"; it RAISES on an unreadable turn and the sweep leaves the row
    open). ``ignored`` needs a voiced item; an unverified one that was not taken up is
    ``unknown``."""
    from brief_first_turn import trigger_key

    cues = {_stem(c) for c in (cue_words or "").lower().split() if c}
    # A later copy of the utterance that triggered the delivery is not the member's answer.
    turns = [t for t in turns if not (trigger and trigger_key(t) == trigger)]
    if turns:
        text = turns[0]
        if not _is_command(text) and cues and _stems(text) & cues:
            return "accepted"
    return "ignored" if voiced == 1 else "unknown"


async def _decide(db, row: tuple, now: datetime) -> tuple[str | None, int | None]:
    """(outcome or None = still open, voiced) for one open row. May raise on an unreadable
    chat read or turn: the caller retries next tick."""
    _rid, uid, kind, sid, cues, surfaced_at, _expires, trigger = row
    surfaced = _parse(surfaced_at)
    if surfaced is None:
        return "unknown", None
    rows = await _replies_after(db, sid, _iso_us(surfaced), _iso_us(surfaced + _REPLY_WITHIN))
    if not rows:
        return None, None  # not persisted yet (or never was): wait, expiry closes it
    first_at = rows[0][1]
    # The reply, plus any auxiliary assistant rows saved right behind it (card / follow-up text).
    mine = [(t, at) for t, at in rows if at - first_at <= _AUX_WITHIN]
    reply, reply_at = " ".join(t for t, _ in mine), max(at for _, at in mine)
    voiced = voiced_in(reply, cues or "")
    if voiced == 0:
        return "undelivered", 0  # injected and settled; the reply never mentioned it
    if kind == "event":  # Notify: information, nothing to answer
        return ("accepted" if voiced == 1 else "unknown"), voiced
    end = reply_at + timedelta(seconds=RESPONSE_WINDOW_S)
    if end > now:
        return None, voiced  # the window is still open
    turns = await _user_turns(db, uid, _iso_us(reply_at), _iso_us(end))
    return judge_turn(turns, cues or "", voiced, trigger or ""), voiced


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
                "SELECT id, user_id, kind, session_id, cue_words, surfaced_at, expires_at, trigger_key "
                "FROM proactive_deliveries WHERE outcome IS NULL ORDER BY surfaced_at LIMIT ?",
                (_SWEEP_BATCH,),
            ) as cur:
                rows = [tuple(r) for r in await cur.fetchall()]
            for row in rows:
                rid, uid, kind = row[0], row[1], row[2]
                judge_by = _parse(row[6]) or ((_parse(row[5]) or now) + JUDGE_BY)
                outcome, voiced = None, None
                try:
                    outcome, voiced = await _decide(db, row, now)
                except Exception as exc:  # noqa: BLE001 — unreadable: retry next tick
                    logger.debug("proactive-ledger: judge deferred id=%s: %r", rid, exc)
                if outcome is None and now >= judge_by:
                    outcome = "unknown"  # never strands
                if outcome is None:
                    continue
                cur = await db.execute(
                    "UPDATE proactive_deliveries SET outcome = ?, outcome_at = ?, voiced = ? "
                    "WHERE id = ? AND outcome IS NULL", (outcome, _iso(now), voiced, rid))
                if (getattr(cur, "rowcount", 1) or 0) > 0:
                    closed += 1
                    logger.info("PROACTIVE_LEDGER_OUTCOME user=%s kind=%s outcome=%s voiced=%s",
                                uid, kind, outcome, "?" if voiced is None else voiced)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive-ledger: sweep failed: %r", exc)
    return closed
