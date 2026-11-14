"""The same-turn PENDING-CORRECTION marker (memory redesign step 1, ZOE_CORRECTION_PENDING_MARKER, default OFF).

After a member corrects a fact, the recall packet can serve the OLD stored row (next turn, or a new session on another panel)
until a rewrite lands, and a shape the rewriter misses never closes that gap. Post-turn, a correction-shaped turn of an identified
member leaves a marker (``note_turn``); the member's own packet then carries that turn's words, fetched from ``chat_messages`` BY
TURN ID at read time (``texts_for_packet``). No write power, so over-triggering is fine.

Holds ``{turn_id, said_at, hashed subject keys}`` per member, in process only: NO words, TTL 2 h, <= 16 per member, lost on
restart. Cleared by any forget (``forget_user``), by a completed rewrite (``clear``; the future claim pass), on TTL, and when the
turn is gone. Logs: counts and ids only.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

TTL_S, MAX_PER_USER, MAX_USERS, MAX_SERVED, TEXT_CAP = 2 * 3600.0, 16, 64, 3, 240


class Marker(NamedTuple):
    turn_id: str
    said_at: float
    keys: tuple = ()            # sha256(folded subject)[:12], never the subject


_LOCK = threading.Lock()
_by_user: "OrderedDict[str, OrderedDict[str, Marker]]" = OrderedDict()


def enabled() -> bool:
    from typed_env import flag

    return bool(flag("ZOE_CORRECTION_PENDING_MARKER"))


def _log(event: str, reason: str, user_id: str, n: int) -> None:
    if n:
        logger.info("PENDING_CORRECTION %s reason=%s user=%s n=%d", event, reason, user_id, n)


def correction_shaped(words: str) -> tuple:
    """(correction-shaped?, subject keys): ``value_correction.statement`` or ``memory_quality.looks_like_correction``."""
    import memory_quality
    import value_correction

    corr = value_correction.statement(words)
    if corr is not None:
        return True, (hashlib.sha256(" ".join(corr.subject.lower().split()).encode()).hexdigest()[:12],)
    return bool(memory_quality.looks_like_correction(words)), ()


def pending(user_id: str, *, now: Optional[float] = None) -> list:
    """The member's live markers, newest first (expired ones are dropped here)."""
    t = time.time() if now is None else now
    with _LOCK:
        marks = _by_user.get(user_id) or OrderedDict()
        old = [k for k, m in marks.items() if t - m.said_at > TTL_S]
        for k in old:
            marks.pop(k)
        live = sorted(marks.values(), key=lambda m: -m.said_at)
    _log("cleared", "ttl", user_id, len(old))
    return live


def put(user_id: str, turn_id: str, *, keys: tuple = (), now: Optional[float] = None) -> None:
    t = time.time() if now is None else now
    pending(user_id, now=t)
    with _LOCK:
        marks = _by_user.setdefault(user_id, OrderedDict())
        marks.pop(turn_id, None)
        marks[turn_id] = Marker(turn_id, t, tuple(keys))
        bound = max(0, len(marks) - MAX_PER_USER)
        for _ in range(bound):
            marks.popitem(last=False)
        _by_user.move_to_end(user_id)
        while len(_by_user) > MAX_USERS:
            _by_user.popitem(last=False)
    _log("cleared", "bound", user_id, bound)
    _log("set", "correction_turn", user_id, 1)


def clear(user_id: str, turn_id: str, reason: str = "rewritten") -> bool:
    """A rewrite for this turn completed (the claim pass calls this). True when a marker went."""
    with _LOCK:
        gone = (_by_user.get(user_id) or {}).pop(turn_id, None) is not None
    _log("cleared", reason, user_id, int(gone))
    return gone


def forget_user(user_id: str, reason: str = "forget") -> int:
    """ANY forget for the member drops every marker of theirs. Sync; never raises."""
    with _LOCK:
        n = len(_by_user.pop(user_id, None) or ())
    _log("cleared", reason, user_id, n)
    return n


def reset() -> None:  # tests
    with _LOCK:
        _by_user.clear()


# ── chat_messages seams (Postgres; the tests replace them) ──────────────────────────────────────────────────────────────

def _sql(tail: str) -> str:
    from memory_provenance import off_record_sql
    from user_filters import message_owner_expr

    return ("SELECT cm.id, cm.content FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id WHERE cm.role = 'user' "
            f"AND {message_owner_expr()} = ? AND {off_record_sql('cm')} {tail}")


async def _rows(sql: str, params: tuple) -> list:
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        rows = await (await db.execute(sql, params)).fetchall()
    return [(str(r["id"]), r["content"] or "") if isinstance(r, dict) else (str(tuple(r)[0]), tuple(r)[1] or "") for r in rows]


async def _find_turn_id(user_id: str, session_id: str, words: str) -> Optional[str]:
    """``chat_messages.id`` of this member's newest saved user turn in the session holding these words; None if not saved yet."""
    from turn_forget import norm

    want = norm(words)
    for rid, content in await _rows(_sql("AND cm.session_id = ? ORDER BY cm.created_at::timestamptz DESC LIMIT 4"),
                                    (user_id, session_id)):
        have = norm(content)
        if want and have and (have == want or have.startswith(want) or want.startswith(have)):
            return rid
    return None


async def _fetch_texts(user_id: str, turn_ids: list) -> dict:
    """``{turn_id: content}`` for the member's OWN on-the-record user turns; a missing id is gone (or forgotten)."""
    return dict(await _rows(_sql(f"AND cm.id IN ({', '.join('?' for _ in turn_ids)})"), (user_id, *turn_ids)))


# ── the two hooks ─────────────────────────────────────────────────────────────────────────────────────────────────────────

async def note_turn(user_message: str, user_id: str, session_id: Optional[str], *, speaker_verified: Optional[bool] = None,
                    rewrote: int = 0) -> bool:
    """Post-turn, from ``memory_extractor.extract_and_ingest`` after ``value_correction.apply`` (inside its identity and
    off-the-record gates). ``rewrote`` > 0: the rewriter already closed this turn. Never raises."""
    try:
        from user_filters import is_unidentified

        if not enabled() or speaker_verified is False or not session_id or rewrote > 0 or is_unidentified(user_id):
            return False
        shaped, keys = correction_shaped(user_message)
        turn_id = await _find_turn_id(user_id, session_id, user_message) if shaped else None
        import turn_forget

        if not turn_id or turn_forget.blocks_write(user_id, user_message):   # not saved, or "forget that" won the race
            return False
        put(user_id, turn_id, keys=keys)
        return True
    except Exception as exc:  # noqa: BLE001 - a marker never costs the turn anything
        logger.debug("PENDING_CORRECTION skipped reason=error user=%s err=%s", user_id, type(exc).__name__)
        return False


async def texts_for_packet(user_id: str, *, now: Optional[float] = None) -> list:
    """The member's <= MAX_SERVED newest pending corrections in their own words, newest first, each <= TEXT_CAP chars. Flag off
    or no marker: [] with no read. Never raises."""
    try:
        live = pending(user_id, now=now)[:MAX_SERVED] if enabled() else []
        found = await _fetch_texts(user_id, [m.turn_id for m in live]) if live else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("PENDING_CORRECTION skipped reason=read_error user=%s err=%s", user_id, type(exc).__name__)
        return []
    out = []
    for m in live:
        text = " ".join((found.get(m.turn_id) or "").split())
        if text:
            out.append(text[:TEXT_CAP])
        else:
            clear(user_id, m.turn_id, reason="turn_gone")
    _log("served", "packet", user_id, len(out))
    return out
