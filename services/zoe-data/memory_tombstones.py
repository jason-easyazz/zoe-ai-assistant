"""Forget tombstones — a forget blocks in-flight/late memory writes for that name.

Memory extraction runs seconds behind the conversation (turn digest, person
extractor, LLM passes are all fire-and-forget), so a fact mentioned just before
"forget everything about X" can finish saving just AFTER the forget archived
everything — one straggler row resurrects the forgotten person (seen live
2026-07-13 during the F14 verification; pinned in docs/IDEAS.md).

``memory_forget_entity`` writes a short-lived per-user tombstone for the name;
``MemoryService.ingest`` — the single durable-write chokepoint every extractor
lane funnels through — drops name-anchored candidates while the tombstone is
live. An EXPLICIT re-teach ("remember that Delia …") clears the tombstone: the
user changing their mind beats the guard.

In-process on purpose (not a DB table): zoe-data is a single uvicorn process
and every extractor pass runs inside it, so the in-flight writes a tombstone
must block die with the process on restart — nothing can straggle across a
restart, and cross-process visibility buys nothing. TTL keeps the map bounded.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# How long a forget shadows late writers. Extractor passes complete within
# seconds; minutes of margin covers a congested Gemma digest without turning
# the tombstone into a long-lived "never mention X" rule.
DEFAULT_TTL_S = 300.0

# Ingest sources that are an EXPLICIT user teach (the user dictating a fact
# right now) — these bypass the tombstone drop so a post-forget re-teach can
# store, and the teach handler then clears the tombstone AFTER the store
# succeeds. Async/mined lanes (turn_digest, chat_regex, voice_regex,
# conversation, idle_consolidation, …) are never listed here.
EXPLICIT_TEACH_SOURCES = frozenset({"brain_tool", "voice_fact", "review_ui"})

# {user_id: {name_norm: expires_at_monotonic}}
_tombstones: dict[str, dict[str, float]] = {}


def _norm(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def _purge(user_id: str) -> dict[str, float]:
    now = time.monotonic()
    names = _tombstones.get(user_id)
    if not names:
        return {}
    live = {n: exp for n, exp in names.items() if exp > now}
    if live:
        _tombstones[user_id] = live
    else:
        _tombstones.pop(user_id, None)
    return live


def add(user_id: str, name: str, ttl_s: float = DEFAULT_TTL_S) -> None:
    """Shadow ``name`` for ``user_id``: name-anchored ingests drop until expiry."""
    name_norm = _norm(name)
    if not user_id or not name_norm:
        return
    _purge(user_id)
    _tombstones.setdefault(user_id, {})[name_norm] = time.monotonic() + ttl_s
    logger.info(
        "memory_tombstones: shadowing a forgotten entity for user=%s (ttl=%.0fs)",
        user_id, ttl_s,
    )


def matching_tombstone(user_id: str, text: str) -> Optional[str]:
    """The live tombstoned name that ``text`` mentions (whole word/phrase,
    case-insensitive), or None. Same anchoring rule as the forget sweep itself."""
    if not user_id or not text:
        return None
    for name_norm in _purge(user_id):
        if re.search(r"\b" + re.escape(name_norm) + r"\b", text, re.IGNORECASE):
            return name_norm
    return None


def clear_matching(user_id: str, text: str) -> int:
    """Drop every tombstone whose name appears in ``text`` — called by EXPLICIT
    teach paths so 'forget Delia' → 'remember that Delia …' works immediately.
    Returns the number cleared."""
    if not user_id or not text:
        return 0
    live = _purge(user_id)
    cleared = 0
    for name_norm in list(live):
        if re.search(r"\b" + re.escape(name_norm) + r"\b", text, re.IGNORECASE):
            del live[name_norm]
            cleared += 1
    if cleared:
        if live:
            _tombstones[user_id] = live
        else:
            _tombstones.pop(user_id, None)
        logger.info(
            "memory_tombstones: cleared %d tombstone(s) for user=%s (explicit re-teach)",
            cleared, user_id,
        )
    return cleared


def clear_all(user_id: Optional[str] = None) -> None:
    """Test/maintenance helper."""
    if user_id is None:
        _tombstones.clear()
    else:
        _tombstones.pop(user_id, None)
