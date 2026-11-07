""""Forget that" means the owner's PREVIOUS TURN - never "the newest memory write" (QA round 3b, 2026-11-07).

The bug. ``memory_forget_last`` used to retract the newest memory WRITE of the last 10 minutes. The post-turn capture (regex
extractor + the LLM turn digest + the person extractors) runs 10-30 minutes behind an active chat, so the newest write was
almost never what the owner had just said: "i secretly hate the colour of our kitchen" -> "forget that" erased an unrelated,
important row about a relative's fall, and the kitchen fact landed later anyway. And "no, don't remember that, forget that"
answered by the brain left the previous turn's capture running, so both rows were stored and survived three forgets.

The contract here:

* Every saved user turn is noted (``note_user_turn``, called from ``routers/chat._save_chat_message`` - every lane saves its
  user turns there) as one turn INSTANCE with its own id (``Turn.tid``): the same words said twice are two turns. The text is
  kept in-process for ``TURN_TTL_S`` only, at most ``_MAX_TURNS`` per user.
* ``previous_turn`` is the newest noted turn that is not itself a forget command: what "forget that" points at.
* ``retract`` installs the WALL for that turn instance for ``RETRACT_TTL_S`` (longer than the worst measured capture lag):
  ``blocks_write`` refuses every write that turn's capture makes. The capture runs inside ``capture_scope`` (a context
  variable ``routers/chat`` sets around the capture passes; asyncio tasks inherit it, so the digest's late write carries it),
  which names the turn INSTANCE it captures - another turn's capture, even one with the same words, is never blocked. A write
  made outside any capture scope is refused only when its evidence excerpt IS a retracted turn's words.
  ``MemoryService.ingest`` and ``MemoryService.review(edit)`` consult it (an explicit re-teach by the owner is exempt, exactly
  like the forget ledger).
* ``rows_from_turn`` finds the rows already written FROM that turn: its ``user_turn_id`` hash, or a source excerpt that is
  exactly the turn's words (never "contains" - a longer turn that merely includes the words is another turn), and never a
  row written before the turn was said.
* ``mark_forgotten`` drops the turn's words from the notes, ONLY once every row was removed: until then the target stays, so
  "forget that" again retries the same turn (a store failure must never leave rows behind a "done").

In-process only: a restart drops the notes (and also kills every in-flight capture), so "forget that" after a restart finds no
turn and asks which thing to forget instead of guessing. The durable half (the nightly digest re-reading ``chat_messages``) is
``exact_words.forget_transcript``, which flags the saved turn ``off_record``.
"""
from __future__ import annotations

import contextvars
import hashlib
import itertools
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: How long a saved user turn stays a "forget that" target.
TURN_TTL_S = 1800.0
#: How long a retracted turn's capture is refused (the measured capture lag reached 30+ minutes, QA round 3b).
RETRACT_TTL_S = 6 * 3600.0
#: A row is "from" a turn only if it was written no earlier than this before the turn was saved (clock slack).
ROW_SLACK_S = 5.0
#: How many recent rows (per status) are read when looking for a turn's rows.
SCAN_LIMIT = 200
_MAX_USERS = 512
_MAX_TURNS = 6
_MAX_RETRACTED = 16

_LOCK = threading.Lock()
_IDS = itertools.count(1)


@dataclass
class Turn:
    text: str
    ts: float
    h: str
    tid: int = 0


@dataclass(frozen=True)
class _Retracted:
    tid: int
    h: str
    until: float


_turns: "OrderedDict[str, deque]" = OrderedDict()
_retracted: "OrderedDict[str, list]" = OrderedDict()
# (user_id, turn instance id or 0 when the turn was never noted, turn hash) of the capture running in this context
_CAPTURE: contextvars.ContextVar = contextvars.ContextVar("zoe_turn_forget_capture", default=None)


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def norm(text: str) -> str:
    t = (text or "").replace("’", "'").replace("‘", "'").lower()
    return re.sub(r"\s+", " ", t).strip(" \t\r\n.,!?;:\"'")


def turn_hash(text: str) -> str:
    return hashlib.sha1(norm(text).encode("utf-8", "ignore")).hexdigest()[:20]


def extractor_turn_ids(text: str) -> frozenset:
    """The ``user_turn_id`` prefixes the per-turn writers mint for ``text`` (``memory_extractor`` / ``memory_digest``:
    ``sha1(message)[:16]``, on the raw and on the stripped message)."""
    out = set()
    for variant in {text or "", (text or "").strip()}:
        if variant:
            out.add(hashlib.sha1(variant.encode("utf-8", "ignore")).hexdigest()[:16])
    return frozenset(out)


def _tracked(user_id: Optional[str]) -> bool:
    try:
        from user_filters import is_unidentified

        return not is_unidentified(user_id)
    except Exception:  # noqa: BLE001
        return bool((user_id or "").strip()) and (user_id or "").strip().lower() != "guest"


def is_forget_turn(text: str) -> bool:
    """Is ``text`` itself a retraction ("forget that", "no, don't remember that", "... forget that please")? Never raises."""
    t = (text or "").strip()
    if not t:
        return False
    try:
        import memory_quality

        if memory_quality.is_forget_command(t) or memory_quality.has_forget_wording(t):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from intent_router import _FORGET_LAST_RE

        return bool(_FORGET_LAST_RE.match(t))
    except Exception:  # noqa: BLE001
        return False


# ── the turn notes ───────────────────────────────────────────────────────────────────────────────────────────────────

def note_user_turn(user_id: Optional[str], text: str, *, now: Optional[float] = None) -> None:
    """Record a saved USER turn. Only identified users; a repeat of the newest turn (the voice lane saves its turn twice) is
    one turn. Never raises."""
    try:
        clean = (text or "").strip()
        if not clean or not _tracked(user_id):
            return
        t = _now(now)
        h = turn_hash(clean)
        with _LOCK:
            dq = _turns.get(user_id)
            if dq is None:
                dq = _turns[user_id] = deque(maxlen=_MAX_TURNS)
                while len(_turns) > _MAX_USERS:
                    _turns.popitem(last=False)
            _turns.move_to_end(user_id)
            if dq and dq[-1].h == h and dq[-1].text:
                return
            dq.append(Turn(clean, t, h, next(_IDS)))
    except Exception:  # noqa: BLE001 - bookkeeping must never lose a turn
        logger.debug("turn_forget.note_user_turn failed", exc_info=True)


def previous_turn(user_id: Optional[str], *, now: Optional[float] = None) -> Optional[Turn]:
    """The owner's newest saved turn that is not itself a forget command (within ``TURN_TTL_S``), or None. A turn already fully
    forgotten comes back with ``text == ""`` - a second "forget that" must answer "already forgotten", never walk on to the
    turn before it."""
    t = _now(now)
    with _LOCK:
        turns = list(_turns.get(user_id or "") or ())
    for turn in reversed(turns):
        if t - turn.ts > TURN_TTL_S:
            return None
        if turn.text and is_forget_turn(turn.text):
            continue
        return Turn(turn.text, turn.ts, turn.h, turn.tid)
    return None


def _newest_tid(user_id: str, h: str) -> int:
    with _LOCK:
        for turn in reversed(list(_turns.get(user_id) or ())):
            if turn.h == h:
                return turn.tid
    return 0


# ── retraction ───────────────────────────────────────────────────────────────────────────────────────────────────────

def retract(user_id: str, turn: Turn, *, now: Optional[float] = None) -> None:
    """Install the wall for this turn INSTANCE: its pending capture never lands. Idempotent; the words stay in the notes until
    ``mark_forgotten`` (so a failed cleanup can be retried). Never raises."""
    try:
        t = _now(now)
        with _LOCK:
            live = [e for e in (_retracted.get(user_id) or []) if e.until >= t and e.tid != turn.tid]
            live.append(_Retracted(turn.tid, turn.h, t + RETRACT_TTL_S))
            _retracted[user_id] = live[-_MAX_RETRACTED:]
            _retracted.move_to_end(user_id)
            while len(_retracted) > _MAX_USERS:
                _retracted.popitem(last=False)
        logger.info("TURN_FORGET user=%s retracted=1", user_id)
    except Exception:  # noqa: BLE001
        logger.warning("turn_forget.retract failed", exc_info=True)


def mark_forgotten(user_id: str, turn: Turn) -> None:
    """Every row of the turn is gone: drop its words from the notes (it keeps its slot - it IS still the previous turn)."""
    with _LOCK:
        for tr in _turns.get(user_id) or ():
            if tr.tid == turn.tid:
                tr.text = ""


def _live(user_id: str, t: float) -> list:
    with _LOCK:
        return [e for e in (_retracted.get(user_id) or []) if e.until >= t]


class capture_scope:
    """``with capture_scope(user_id, user_message):`` marks the post-turn capture of that turn INSTANCE (the newest noted turn
    with those words) in this context and every task created inside it, so ``blocks_write`` can refuse its writes once that
    turn is retracted - and only that turn's."""

    def __init__(self, user_id: str, text: str) -> None:
        h = turn_hash(text)
        self._val = (user_id or "", _newest_tid(user_id or "", h), h)
        self._tok = None

    def __enter__(self) -> "capture_scope":
        self._tok = _CAPTURE.set(self._val)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._tok is not None:
            _CAPTURE.reset(self._tok)


def blocks_write(user_id: Optional[str], excerpt: str = "", *, now: Optional[float] = None) -> bool:
    """Would this write store what a retracted turn said? Inside a capture scope: only when the scope's turn INSTANCE was
    retracted (another turn with the same words is never blocked). Outside one: only when the write's evidence excerpt IS a
    retracted turn's words. Never raises."""
    try:
        live = _live(user_id or "", _now(now))
        if not live:
            return False
        cap = _CAPTURE.get()
        if cap is not None and cap[0] == user_id:
            if cap[1]:
                return any(e.tid == cap[1] for e in live)
            return any(e.h == cap[2] for e in live)      # a capture of a turn that was never noted: its words are all there is
        if excerpt and excerpt.strip():
            h = turn_hash(excerpt)
            return any(e.h == h for e in live)
        return False
    except Exception:  # noqa: BLE001
        return False


# ── the rows a turn produced ─────────────────────────────────────────────────────────────────────────────────────────

def _added_ts(meta: dict) -> Optional[float]:
    raw = (meta or {}).get("added_ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    added = str((meta or {}).get("added_at") or "")
    if not added:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(added.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return None


def row_is_from_turn(meta: dict, turn_text: str, turn_ts: Optional[float] = None) -> bool:
    """Was a row (its metadata) written FROM this turn? Its ``user_turn_id`` is the turn's hash, or its source excerpt is
    exactly the turn's words. Never "one contains the other" (a longer turn that includes the words is another turn), never
    by time alone, and never a row written before the turn was said."""
    meta = meta or {}
    if turn_ts is not None:
        ts = _added_ts(meta)
        if ts is not None and ts < turn_ts - ROW_SLACK_S:
            return False
    tid = str(meta.get("user_turn_id") or "")
    if tid and any(tid.startswith(p) for p in extractor_turn_ids(turn_text)):
        return True
    ex, key = norm(str(meta.get("source_excerpt") or "")), norm(turn_text)
    return bool(ex and key and ex == key)


async def rows_from_turn(svc: Any, user_id: str, turn_text: str, turn_ts: Optional[float] = None) -> list:
    """The owner's approved / pending rows written from this turn (``row_is_from_turn``). RAISES on a store failure (a forget
    must not be confirmed when the store could not be read)."""
    out, seen = [], set()
    for status in ("approved", "pending"):
        for r in await svc.list_by_status(user_id=user_id, status=status, limit=SCAN_LIMIT):
            if r.id in seen:
                continue
            m = r.metadata or {}
            if (m.get("user_id") or m.get("wing") or user_id) != user_id:
                continue
            if row_is_from_turn(m, turn_text, turn_ts):
                seen.add(r.id)
                out.append(r)
    return out


def reset() -> None:  # tests
    with _LOCK:
        _turns.clear()
        _retracted.clear()


__all__ = [
    "Turn", "note_user_turn", "previous_turn", "retract", "mark_forgotten", "capture_scope", "blocks_write",
    "rows_from_turn", "row_is_from_turn", "is_forget_turn", "reset",
]
