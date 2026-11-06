"""When may Zoe voice a pending contact offer?  ONE policy shared by every
injector (the /api/memories/for-prompt fold and the Flue fallback block) and the
yes/no binder (intent_router), so they cannot drift:

- at most ONE offer per turn (non-batch);
- none on forget / correction / decline / "not now" turns (deferred, not lost);
- a voiced offer is not repeated for ``COOLDOWN_TURNS`` distinct user turns.

Deterministic, in-process per-user state (zoe-data is one worker). A restart
forgets it, which fails towards asking once more, never towards nagging.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

COOLDOWN_TURNS = 3
# Two builds for the same message within this many seconds are one turn
# (several packet builders per turn); a later identical message is a new turn.
SAME_TURN_S = 10.0
MAX_USERS = 256

_STATE: dict[str, dict[str, Any]] = {}

_NO_OFFER_TURN_RE = re.compile(
    # a bare leading no / nope / nah (not "no problem", "no kidding", "no way")
    r"^\s*(?:(?:no|nope|nah|nay)\b(?=\s*(?:[,.!?;]|$|thanks?\b|thank\s+you\b|not\b|don'?t\b|i\b|it\b|that\b|she\b|he\b|they\b))"
    r"|please\s+no\b|thanks?,?\s+but\s+no\b)"
    r"|\b(?:forget\s+(?:that|it|about)|never\s*mind|not\s+(?:right\s+)?now|not\s+today|"
    r"maybe\s+later|i'?d\s+rather\s+not|skip\s+(?:it|that)|leave\s+(?:it|that)|"
    r"that'?s\s+(?:not\s+(?:right|true|correct)|wrong)|i\s+(?:didn'?t|did\s+not)\s+(?:say|mean)|"
    r"you\s+(?:misheard|misunderstood)|actually\s*,|i\s+meant|just\s+checking|"
    r"don'?t\s+(?:ask|add)|stop\s+(?:asking|that))",
    re.IGNORECASE,
)


def no_offer_turn(message: str) -> bool:
    """Forget / correction / decline / "not now" turns never carry an offer."""
    return bool(_NO_OFFER_TURN_RE.search(message or ""))


def offer_key(p: dict) -> str:
    return str(p.get("id") or p.get("name") or "")


def tick(user_id: str, message: str) -> dict[str, Any]:
    """Advance this user's turn counter (call on EVERY turn, suppressed ones
    included: they count toward the cooldown). Idempotent for a rebuild of the
    same message inside ``SAME_TURN_S``. LRU-evicts users at the cap and prunes
    expired offer keys."""
    key = (message or "").strip().lower()
    now = time.monotonic()
    st = _STATE.pop(user_id, None)  # re-inserted below = most recent last (LRU)
    if st is None:
        if len(_STATE) >= MAX_USERS:
            _STATE.pop(next(iter(_STATE)))
        st = {"key": None, "turn": 0, "ts": 0.0, "offered": {}, "last": None}
    if key != st["key"] or now - st["ts"] > SAME_TURN_S:
        st["turn"] += 1
        st["offered"] = {k: v for k, v in st["offered"].items()
                         if st["turn"] - v <= COOLDOWN_TURNS}
    st["key"], st["ts"] = key, now
    _STATE[user_id] = st
    return st


def eligible(user_id: str, message: str, pend: list[dict]) -> list[dict]:
    """``pend`` minus offers voiced within the cooldown (the offer voiced THIS
    turn stays, so a second builder of the same turn agrees with the first)."""
    st = _STATE.get(user_id) or tick(user_id, message)
    turn = st["turn"]
    return [p for p in pend
            if (at := st["offered"].get(offer_key(p))) is None
            or at == turn or turn - at > COOLDOWN_TURNS]


def note_offered(user_id: str, offers: list[dict]) -> None:
    st = _STATE.get(user_id)
    if st:
        for p in offers:
            st["offered"].setdefault(offer_key(p), st["turn"])
        if offers:
            st["last"] = str(offers[-1].get("id") or "") or None


def last_offered_id(user_id: str) -> Optional[str]:
    """The id of the offer most recently voiced to this user (a bare yes/no binds
    to it, not to the oldest surfaced row)."""
    st = _STATE.get(user_id)
    return st.get("last") if st else None


def reset() -> None:
    _STATE.clear()


async def unmark_unoffered(user_id: str, pend: list[dict], offered: list[dict]) -> None:
    """The surfacing read marks every row it returns as surfaced; rows that did
    not make it into the prompt must not start aging. Best-effort."""
    keep = {str(o.get("id")) for o in offered}
    ids = [p["id"] for p in pend if p.get("id") and str(p["id"]) not in keep]
    if not ids:
        return
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            for i in ids:
                await db.execute(
                    "UPDATE pending_suggestions SET turns_elapsed = 0 "
                    "WHERE id = $1 AND user_id = $2 AND turns_elapsed = 1 AND resolved = 0",
                    i, user_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("contact_offer_policy: unmark failed: %s", exc)
