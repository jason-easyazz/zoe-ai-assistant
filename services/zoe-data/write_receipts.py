"""Receipts for writes that really landed - the evidence the write-claim guard checks a reply against.

Live 2026-10-12: the brain said "I've added a work meeting to your calendar" with no calendar row and no tool call in the
turn. A spoken sentence is not evidence; a row is. Every place that WRITES a list item, calendar event, reminder, note or
timer for a user calls ``record`` with the kind and the row id (``calendar_service.create_event_record``,
``list_service.add_item_to_list``, ``reminder_service.create_reminder_record``, the Skybridge inline inserts and its timer
store, the note executor). ``write_claim_guard`` then lets a reply that CLAIMS a write be spoken only when a receipt of that
kind exists for the user since the turn began.

Turn binding (review, #2004): two overlapping turns for the SAME user (a panel turn and a chat turn) must not vouch for each
other. ``begin()`` mints a turn id and binds it to the calling task's ``contextvars`` context; ``record`` stamps each receipt with
the turn of the context it runs in, and a turn's verdict (``since`` / ``has`` with its ``TurnStart``) counts only receipts of ITS
turn or receipts stamped with no turn at all. A write made in the turn's own task (or a task it spawned) is therefore private to
the turn. A write that arrives with no turn context - a brain TOOL call, which reaches zoe-data as a separate HTTP request to
``/api/system/intent-dispatch`` that no ContextVar can follow, and any fast-path write outside a brain turn - is unstamped and
still counts for every concurrent turn of that user (counting it is the safe direction: it never blocks a true claim). Closing
that gap needs the turn id echoed on the dispatch call (the Flue sidecar does this for ``speculative_turn_id``); it is ledgered in
``docs/knowledge/open-problems.md``.

In-process and per user (zoe-data is a single uvicorn worker - the writes happen in this process, including the ones a Flue
brain tool makes through ``/api/system/intent-dispatch``). Bounded: the newest ``_MAX_PER_USER`` receipts, each kept for
``_TTL_S`` (the recap window below is the longest anything reads). Stdlib only; ``record`` never raises.
"""
from __future__ import annotations

import contextvars
import itertools
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, Optional

# The kinds a reply can claim. "calendar" and "reminder" satisfy each other (both are "a time-anchored thing was saved");
# every other kind stands alone. "any" is a claim that names no target ("I've added it").
KINDS = ("list", "calendar", "reminder", "note", "timer")
_COMPATIBLE = {"calendar": {"calendar", "reminder"}, "reminder": {"calendar", "reminder"}}

_TTL_S = 3600.0
_MAX_PER_USER = 64


@dataclass(frozen=True)
class Receipt:
    kind: str
    row_id: str
    source: str
    at: float          # time.monotonic() when the write landed
    turn: str = ""     # the turn whose context made the write ("" = no turn context: counts for any turn of the user)


_STORE: Dict[str, Deque[Receipt]] = {}
_CURRENT_TURN: "contextvars.ContextVar[str]" = contextvars.ContextVar("zoe_write_receipt_turn", default="")
_TURN_IDS = itertools.count(1)


class TurnStart(float):
    """The instant a turn began (``time.monotonic()``) - a plain float that also carries the turn's id, so everything that took
    a ``started`` float keeps working while ``since`` / ``has`` can tell this turn's receipts from a concurrent turn's."""

    turn: str = ""


def _uid(user_id: Optional[str]) -> str:
    return (user_id or "").strip() or "guest"


def now() -> float:
    """The clock a turn's start is taken on (``begin``) - the same one ``record`` stamps with."""
    return time.monotonic()


def begin() -> TurnStart:
    """A turn is starting: writes at or after this instant, made in this context, belong to it."""
    started = TurnStart(now())
    started.turn = f"t{next(_TURN_IDS)}"
    _CURRENT_TURN.set(started.turn)
    return started


def record(user_id: Optional[str], kind: str, row_id: object = "", source: str = "") -> None:
    """A write landed. Never raises (a bookkeeping failure must not fail the write that already happened)."""
    try:
        k = (kind or "").strip().lower()
        if k not in KINDS:
            return
        t = now()
        q = _STORE.setdefault(_uid(user_id), deque(maxlen=_MAX_PER_USER))
        q.append(Receipt(k, str(row_id or "")[:64], (source or "")[:40], t, _CURRENT_TURN.get()))
        while q and t - q[0].at > _TTL_S:
            q.popleft()
    except Exception:  # noqa: BLE001
        return


def since(user_id: Optional[str], started: float, kinds: Optional[Iterable[str]] = None, *,
          turn: Optional[str] = None) -> list[Receipt]:
    """Receipts for ``user_id`` at or after ``started`` (newest last), optionally only of ``kinds`` (compatible kinds count).
    ``turn`` (default: the one ``started`` carries, if it is a ``TurnStart``) hides the receipts of OTHER turns; None / a plain
    float = every receipt of the user."""
    if turn is None:
        turn = getattr(started, "turn", None) or None
    q = _STORE.get(_uid(user_id))
    if not q:
        return []
    wanted: Optional[set] = None
    if kinds is not None:
        wanted = set()
        for k in kinds:
            wanted |= _COMPATIBLE.get(k, {k})
    return [r for r in list(q)
            if r.at >= started and (wanted is None or r.kind in wanted) and (turn is None or r.turn in ("", turn))]


def has(user_id: Optional[str], started: float, kind: Optional[str] = None, *, turn: Optional[str] = None) -> bool:
    """True when a write of ``kind`` (any kind when None / "any") landed for the user since ``started`` (see ``since`` for ``turn``)."""
    if not kind or kind == "any":
        return bool(since(user_id, started, turn=turn))
    return bool(since(user_id, started, (kind,), turn=turn))


def reset(user_id: Optional[str] = None) -> None:
    """Test hook / admin reset."""
    if user_id is None:
        _STORE.clear()
    else:
        _STORE.pop(_uid(user_id), None)
