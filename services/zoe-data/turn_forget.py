""""Forget that" means the owner's PREVIOUS TURN - never "the newest memory write" (QA round 3b, 2026-11-07).

The bug. ``memory_forget_last`` used to retract the newest memory WRITE of the last 10 minutes. The post-turn capture (regex
extractor + the LLM turn digest + the person extractors) runs 10-30 minutes behind an active chat, so the newest write was
almost never what the owner had just said: "i secretly hate the colour of our kitchen" -> "forget that" erased an unrelated,
important row about a relative's fall, and the kitchen fact landed later anyway. And "no, don't remember that, forget that"
answered by the brain left the previous turn's capture running, so both rows were stored and survived three forgets.

The contract here:

* Every saved user turn is noted (``note_user_turn``, called from ``routers/chat._save_chat_message`` - every lane saves its
  user turns there). The text is kept in-process for ``TURN_TTL_S`` only, at most ``_MAX_TURNS`` per user.
* ``previous_turn`` is the newest noted turn that is not itself a forget command: what "forget that" points at.
* ``retract`` tombstones that turn for ``RETRACT_TTL_S`` (longer than the worst measured capture lag): the turn's text is
  dropped from the note, only its hash and stemmed content words stay (never the words themselves, so nothing retains what it
  was asked to forget). From then on ``blocks_write`` refuses any write the turn's capture would make:

    - the capture task of that turn (``capture_scope`` - a context variable set by ``routers/chat`` around the capture
      passes; asyncio tasks inherit it, so the digest's late write still carries it);
    - any write whose evidence excerpt IS that turn, or whose excerpt's content words are (almost) all that turn's.

  ``MemoryService.ingest`` and ``MemoryService.review(edit)`` consult it (an explicit re-teach by the owner is exempt, exactly
  like the forget ledger).
* ``rows_from_turn`` finds the rows already written FROM that turn (its ``user_turn_id`` hash, or a source excerpt that is the
  turn's own words), never by recency. Nothing from another turn is ever touched.

In-process only: a restart drops the notes (and also kills every in-flight capture), so "forget that" after a restart finds no
turn and asks which thing to forget instead of guessing. The durable half (the nightly digest re-reading ``chat_messages``) is
``exact_words.forget_transcript``, which flags the saved turn ``off_record``.
"""
from __future__ import annotations

import contextvars
import hashlib
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
#: An excerpt whose content words are at least this share of a retracted turn's words is that turn's.
EXCERPT_OVERLAP = 0.9
#: A row is "from" a turn only if it was written no earlier than this before the turn was saved (clock slack).
ROW_SLACK_S = 5.0
#: How many recent rows (per status) are read when looking for a turn's rows.
SCAN_LIMIT = 200
_MAX_USERS = 512
_MAX_TURNS = 6
_MAX_RETRACTED = 16

_LOCK = threading.Lock()


@dataclass
class Turn:
    text: str
    ts: float
    h: str


@dataclass(frozen=True)
class _Retracted:
    h: str
    words: frozenset
    until: float


_turns: "OrderedDict[str, deque]" = OrderedDict()
_retracted: "OrderedDict[str, list]" = OrderedDict()
# (user_id, turn hash, turn content words) of the turn whose post-turn capture is running in this context
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


def _words(text: str) -> frozenset:
    try:
        from exact_words import content_tokens

        return frozenset(content_tokens(text or ""))
    except Exception:  # noqa: BLE001 - a plain fallback keeps the wall up
        return frozenset(w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) >= 3)


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
            if dq and dq[-1].h == h:
                return
            dq.append(Turn(clean, t, h))
    except Exception:  # noqa: BLE001 - bookkeeping must never lose a turn
        logger.debug("turn_forget.note_user_turn failed", exc_info=True)


def previous_turn(user_id: Optional[str], *, now: Optional[float] = None) -> Optional[Turn]:
    """The owner's newest saved turn that is not itself a forget command (within ``TURN_TTL_S``), or None. A turn already
    retracted comes back with ``text == ""`` - a second "forget that" must answer "already forgotten", never walk on to the
    turn before it."""
    t = _now(now)
    with _LOCK:
        turns = list(_turns.get(user_id or "") or ())
    for turn in reversed(turns):
        if t - turn.ts > TURN_TTL_S:
            return None
        if turn.text and is_forget_turn(turn.text):
            continue
        return Turn(turn.text, turn.ts, turn.h)
    return None


# ── retraction ───────────────────────────────────────────────────────────────────────────────────────────────────────

def retract(user_id: str, turn: Turn, *, now: Optional[float] = None) -> None:
    """Tombstone ``turn``: its pending capture never lands, and its words leave the notes. Idempotent. Never raises."""
    try:
        t = _now(now)
        entry = _Retracted(turn.h, _words(turn.text), t + RETRACT_TTL_S)
        with _LOCK:
            live = [e for e in (_retracted.get(user_id) or []) if e.until >= t and e.h != turn.h]
            live.append(entry)
            _retracted[user_id] = live[-_MAX_RETRACTED:]
            _retracted.move_to_end(user_id)
            while len(_retracted) > _MAX_USERS:
                _retracted.popitem(last=False)
            dq = _turns.get(user_id)
            if dq is not None:
                for tr in dq:
                    if tr.h == turn.h:
                        tr.text = ""          # still occupies its slot (it IS the previous turn), its words are gone
        logger.info("TURN_FORGET user=%s retracted=1", user_id)
    except Exception:  # noqa: BLE001
        logger.warning("turn_forget.retract failed", exc_info=True)


def _live(user_id: str, t: float) -> list:
    with _LOCK:
        return [e for e in (_retracted.get(user_id) or []) if e.until >= t]


def is_retracted(user_id: Optional[str], text: str, *, now: Optional[float] = None) -> bool:
    """Is ``text`` a turn the owner retracted? Never raises."""
    try:
        h = turn_hash(text)
        return any(e.h == h for e in _live(user_id or "", _now(now)))
    except Exception:  # noqa: BLE001
        return False


class capture_scope:
    """``with capture_scope(user_id, user_message):`` marks the post-turn capture of that turn in this context (and in every
    task created inside it), so ``blocks_write`` can refuse its writes once the turn is retracted."""

    def __init__(self, user_id: str, text: str) -> None:
        self._val = (user_id or "", turn_hash(text), _words(text))
        self._tok = None

    def __enter__(self) -> "capture_scope":
        self._tok = _CAPTURE.set(self._val)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._tok is not None:
            _CAPTURE.reset(self._tok)


def blocks_write(user_id: Optional[str], excerpt: str = "", *, now: Optional[float] = None) -> bool:
    """Would this write store what a retracted turn said? True for a write made by that turn's own capture, or whose evidence
    excerpt is that turn's words. A row about anything else is never blocked. Never raises."""
    try:
        live = _live(user_id or "", _now(now))
        if not live:
            return False
        cap = _CAPTURE.get()
        if cap is not None and cap[0] == user_id:
            if any(e.h == cap[1] for e in live):
                return True
            if cap[2] and any(e.words and cap[2] == e.words for e in live):
                return True
        if excerpt and excerpt.strip():
            if any(e.h == turn_hash(excerpt) for e in live):
                return True
            ew = _words(excerpt)
            if len(ew) >= 2 and any(e.words and len(ew & e.words) / len(ew) >= EXCERPT_OVERLAP for e in live):
                return True
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
    """Was a row (its metadata) written FROM this turn? Its ``user_turn_id`` is the turn's hash, or its source excerpt is the
    turn's own words (equal, or one inside the other when the shorter has at least three words). Never by time alone."""
    meta = meta or {}
    if turn_ts is not None:
        ts = _added_ts(meta)
        if ts is not None and ts < turn_ts - ROW_SLACK_S:
            return False                       # written before the turn was said: it cannot come from it
    tid = str(meta.get("user_turn_id") or "")
    if tid and any(tid.startswith(p) for p in extractor_turn_ids(turn_text)):
        return True
    ex, key = norm(str(meta.get("source_excerpt") or "")), norm(turn_text)
    if not ex or not key:
        return False
    if ex == key:
        return True
    shorter = ex if len(ex) < len(key) else key
    return len(shorter.split()) >= 3 and (ex in key or key in ex)


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
    "Turn", "note_user_turn", "previous_turn", "retract", "is_retracted", "capture_scope", "blocks_write",
    "rows_from_turn", "row_is_from_turn", "is_forget_turn", "reset",
]
