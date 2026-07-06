"""Conversation-session opener detection for Zoe voice ("hey zoe, let's talk").

When Jason opens a session with a phrase like "let's talk" / "let's chat"
(post wake-strip), Zoe should warmly acknowledge and hand the session over to
continuous conversation mode (the LiveKit always-listening lane) instead of
treating it as a one-shot command.

Like voice_presence wake acks, this is deliberately a fast-path: the opener is
matched with a cheap allowlist and answered instantly — it must never enter the
brain/LLM lane.

Flag-gated, DEFAULT OFF: ZOE_CONVERSATION_OPENER_ENABLED (read per call).
The phrase matcher itself is pure and dependency-free.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Mapping

# Tight allowlist. Matching is fullmatch over the normalized utterance
# (apostrophes removed, punctuation stripped), optionally followed by "zoe" —
# so "let's talk about mum's birthday" style longer requests NEVER fire.
_OPENER_PHRASES = frozenset(
    {
        "lets talk",           # "let's talk" / "lets talk"
        "lets chat",           # "let's chat" / "lets chat"
        "lets have a chat",
        "lets have a talk",
        "can we talk",
        "can we chat",
        "talk to me",
        "i want to talk",
        "i want to chat",
    }
)

_DEFAULT_OPENER_ACK_PHRASES = (
    "I'm here — what's on your mind?|Of course. What's going on?|I'm listening."
)

_OPENER_ACK_CURSOR = 0
_OPENER_ACK_LOCK = threading.Lock()

# Fire-and-forget LiveKit warmup tasks are held here so they are not
# garbage-collected mid-flight (standard asyncio create_task pattern).
_BACKGROUND_TASKS: set = set()

_NON_LETTER_RE = re.compile(r"[^a-z\s]+")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str | None) -> str:
    """Lowercase, drop apostrophes (curly included), map punctuation to spaces,
    and collapse whitespace — so "Let's chat!" and "lets chat" normalize alike."""
    lowered = str(text or "").lower()
    lowered = lowered.replace("’", "").replace("ʼ", "").replace("'", "")
    lowered = _NON_LETTER_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", lowered).strip()


def is_conversation_opener(text: str | None) -> bool:
    """Return True only when the WHOLE (post-wake-strip) utterance is a
    conversation-opener phrase, optionally followed by "zoe".

    Pure str -> bool; case/punctuation-insensitive; no dependencies.
    """
    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized.endswith(" zoe"):
        normalized = normalized[: -len(" zoe")].rstrip()
    return normalized in _OPENER_PHRASES


def conversation_opener_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Flag gate — DEFAULT OFF, read per call so live toggles take effect."""
    values = env if env is not None else os.environ
    raw = str(values.get("ZOE_CONVERSATION_OPENER_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def opener_ack_phrases(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve the warm opener lines (pipe-separated env override supported,
    mirroring ZOE_WAKE_ACK_PHRASES conventions in voice_presence)."""
    values = env if env is not None else os.environ
    configured = str(values.get("ZOE_CONVERSATION_OPENER_PHRASES") or "").strip()
    source = configured if configured else _DEFAULT_OPENER_ACK_PHRASES
    return [item.strip() for item in source.split("|") if item.strip()]


def opener_ack_phrase(env: Mapping[str, str] | None = None, *, index: int | None = None) -> str:
    """Pick one warm opener line, rotating across calls (like wake ack variants)."""
    global _OPENER_ACK_CURSOR
    phrases = opener_ack_phrases(env)
    if not phrases:
        return ""
    if index is None:
        with _OPENER_ACK_LOCK:
            selected = _OPENER_ACK_CURSOR % len(phrases)
            _OPENER_ACK_CURSOR += 1
    else:
        selected = index % len(phrases)
    return phrases[selected]


def _warm_livekit() -> None:
    """Fire-and-forget ensure_livekit_started() so the continuous LiveKit room
    is warming while the opener ack plays. Best-effort: never raises."""
    try:
        import asyncio

        from routers.voice_livekit import ensure_livekit_started

        loop = asyncio.get_running_loop()
        task = loop.create_task(ensure_livekit_started())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except Exception:
        # No running loop / LiveKit router unavailable — the opener ack still
        # works; the client will start LiveKit itself when it switches mode.
        pass


def maybe_conversation_opener(
    text: str | None, env: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """The single wiring seam for the voice path.

    Returns {"phrase": <warm line>, "conversation_mode": True} when the flag is
    ON and the (wake-stripped) utterance is an opener phrase; None otherwise.
    Flag OFF is a true no-op — nothing is matched, warmed, or rotated.

    On a hit, also fire-and-forgets the LiveKit on-demand warmup.
    """
    if not conversation_opener_enabled(env):
        return None
    if not is_conversation_opener(text):
        return None
    _warm_livekit()
    return {"phrase": opener_ack_phrase(env), "conversation_mode": True}
