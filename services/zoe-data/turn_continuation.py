"""One shared "a continuation follows the previous domain" rule for the voice router.

A bare "what else?" / "and?" / "tell me more" names no topic, so the router's own verdict on it is noise: live 2026-10-29, four
"what else?" after a memory answer all routed to ``lists`` (head 0.65 / calendar 0.68) and each got a list reply. A continuation
must follow the turn before it - the same panel, inside a short window - or go to the brain WITH the conversation. It must never
land on a random fast domain.

Rule (``follow``): a continuation inside ``conversation_cues.turn_continuation_ttl_s`` of the panel's previous turn keeps that
turn's domain when it is a conversational one (``memory`` / ``people`` / ``journal`` / ``notes`` / ``chat``); every other previous
domain (a lists / weather / time read) and a continuation with no recent previous turn route to ``chat``, i.e. the brain, which
has the session history. The words are the lexicon's (``turn_continuation``); the window is data (``ZOE_TURN_CONTINUATION_TTL_S``).
In-process state only (a bounded dict per panel); nothing is persisted. NEVER raises.
"""
from __future__ import annotations

import time
from typing import Optional

#: previous domains a continuation may keep: the ones whose answer is an open conversation, not a one-shot fast read
CONVERSATIONAL_DOMAINS = frozenset({"memory", "people", "journal", "notes", "chat"})
_MAX_KEYS = 64
_LAST: dict[str, tuple[str, float]] = {}


def _remember(key: str, domain: str, now: float) -> None:
    if len(_LAST) >= _MAX_KEYS and key not in _LAST:
        for old in sorted(_LAST, key=lambda k: _LAST[k][1])[: _MAX_KEYS // 2]:
            _LAST.pop(old, None)
    _LAST[key] = (domain, now)


def follow(key: str, text: str, decision: Optional[dict], now: Optional[float] = None) -> Optional[dict]:
    """Return the decision to route this turn by. A continuation gets a copy re-pointed at the previous turn's conversational
    domain (or ``chat``) with the two-stage tool dropped; any other turn is returned untouched and becomes the new "previous"."""
    try:
        if not isinstance(decision, dict) or not key:
            return decision
        t = time.monotonic() if now is None else now
        from conversation_cues import turn_continuation, turn_continuation_ttl_s

        if not turn_continuation(text):
            _remember(key, str(decision.get("routed") or "chat"), t)
            return decision
        prev = _LAST.get(key)
        fresh = prev is not None and (t - prev[1]) <= turn_continuation_ttl_s()
        domain = prev[0] if fresh and prev[0] in CONVERSATIONAL_DOMAINS else "chat"
        out = {k: v for k, v in decision.items() if k != "two_stage"}
        out.update(routed=domain, domain=domain, continuation=True, continuation_of=decision.get("routed"))
        if fresh:
            _remember(key, prev[0], t)      # a run of "what else?" keeps the topic alive
        return out
    except Exception:  # noqa: BLE001 - never break a turn over this
        return decision
