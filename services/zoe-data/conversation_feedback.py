"""Feedback said in words: "that was wrong" / "good answer" leave the same thumb the chat buttons leave.

Why. ``routers/chat.py``'s ``POST /feedback/{interaction_id}`` (the thumbs under a typed reply) was the ONLY writer of
``chat_feedback``. A spoken "that was wrong" wrote nothing at all (register G15 / felt gap 7), so the panel - the surface the
household actually talks to - taught Zoe nothing and a kind word got a model turn. This is the one writer both lanes call.

What. ONE deterministic tier, run from ``fast_tiers.resolve`` on every channel that uses the core (chat, voice, LiveKit, Telegram),
so the typed and the spoken sentence behave the same by construction:

* a BARE verdict on the last answer - "that was wrong", "that's not right", "wrong answer", "that's not what I meant" - writes a
  ``thumbs_down`` for the assistant reply it is about and then lets the turn go on (the correction path, the live check on a
  challenged world fact, the brain): the person still wants an answer, the row is a side effect;
* "that's wrong, it's X" (a verdict WITH a value) writes ``correction`` carrying X as ``corrected_response`` and goes on;
* a bare kind verdict - "good answer", "that was helpful", "that's exactly what I needed" - writes ``thumbs_up`` and answers in four
  words, with no model turn.

Walls: a registered member only (a guest or an unknown id writes nothing); a speaker the voice gate REJECTED writes nothing and is
not answered here (the row would carry someone else's say-so under the member's name); a dry replay (``allow_writes=False``) writes
nothing; no previous assistant reply in the session = nothing to rate = nothing written. Whole-utterance anchored regexes only, so a
turn that is none of these shapes costs one failed match and no read.

Flag ``ZOE_CONVERSATION_FEEDBACK`` (default ON; ``0|false|no|off`` = the tier does nothing). VOICE-PATH: one anchored regex per
turn; a matching turn adds one bounded read + one insert.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_CONVERSATION_FEEDBACK"
THUMBS_UP = "thumbs_up"
THUMBS_DOWN = "thumbs_down"
CORRECTION = "correction"
UP_REPLY = "Glad that helped."
_GUEST = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})
_MAX_WORDS = 14
_DB_BUDGET_S = 2.5


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_CONVERSATION_FEEDBACK", True)


_LEAD = r"(?:(?:no|nope|oh|hey|zoe|sorry|um+|uh|thanks|thank you|great|ok(?:ay)?)[,.!\s]+)*"
_TAIL = r"(?:[,.!\s]+(?:zoe|thanks|thank you|please))*[.!\s]*"
_IT = r"(?:that|this|it)(?:\s+(?:is|was)|['’]s)?"
_VERY = r"(?:(?:really|totally|just|completely|so|very)\s+)?"
_DOWN_RE = re.compile(
    rf"^\W*{_LEAD}(?:"
    rf"{_IT}\s+{_VERY}(?:wrong|incorrect|a\s+(?:bad|wrong)\s+answer|not\s+(?:right|correct|true|helpful|good|what\s+i\s+(?:meant|asked|wanted|said)))"
    r"|(?:that\s+was\s+|that['’]s\s+)?(?:a\s+)?(?:wrong|bad|useless|unhelpful)\s+answer"
    r"|you\s+(?:got|have\s+got|had)\s+(?:that|it|this)\s+wrong"
    r"|not\s+what\s+i\s+(?:meant|asked|wanted|said)"
    rf"){_TAIL}$",
    re.IGNORECASE,
)
# a verdict WITH the right value: "that's wrong, it's Thursday" / "no, that's not right - it was Friday"
_CORRECTION_RE = re.compile(
    rf"^\W*{_LEAD}{_IT}\s+{_VERY}(?:wrong|incorrect|not\s+(?:right|correct|true))[,.;:\-\s]+"
    r"(?:it(?:\s+(?:is|was)|['’]s)|the\s+(?:right|correct)\s+answer\s+is|actually(?:\s+it(?:\s+(?:is|was)|['’]s))?)\s+"
    r"(?P<v>[^?]{1,100}?)[.!\s]*$",
    re.IGNORECASE,
)
_UP_RE = re.compile(
    rf"^\W*{_LEAD}(?:"
    r"(?:a\s+)?(?:really\s+|very\s+|so\s+)?(?:good|great|nice|perfect|excellent|brilliant|helpful|lovely)\s+(?:answer|job|one|response|call|reply)"
    rf"|{_IT}\s+{_VERY}(?:helpful|perfect|great\s+(?:answer|help)|spot\s+on|exactly\s+(?:what|right)(?:\s+i\s+(?:needed|wanted|meant))?"
    r"|just\s+what\s+i\s+(?:needed|wanted))"
    rf"){_TAIL}$",
    re.IGNORECASE,
)


def classify(text: str) -> Optional[tuple[str, str]]:
    """``(feedback_type, value)`` for a feedback sentence, else None. Pure. ``value`` is the corrected answer for ``correction``."""
    t = (text or "").strip()
    if not t or len(t.split()) > _MAX_WORDS:
        return None
    m = _CORRECTION_RE.match(t)
    if m:
        return CORRECTION, m.group("v").strip(" .!")
    if _DOWN_RE.match(t):
        return THUMBS_DOWN, ""
    if _UP_RE.match(t):
        return THUMBS_UP, ""
    return None


def _is_guest(user_id: Optional[str]) -> bool:
    u = (user_id or "").strip().lower()
    return u in _GUEST or u.startswith("guest-")


async def record_feedback(user_id: str, interaction_id: str, feedback_type: str, corrected_response: Optional[str] = None) -> str:
    """The ONE writer of ``chat_feedback`` (the thumbs endpoint and this tier both call it). Returns the row id. Raises on a store
    failure - the endpoint reports it; the tier swallows it."""
    from db_pool import get_db_ctx

    row_id = uuid.uuid4().hex[:12]
    async with get_db_ctx() as db:
        await db.execute(
            "INSERT INTO chat_feedback (id, interaction_id, user_id, feedback_type, corrected_response) VALUES (?, ?, ?, ?, ?)",
            (row_id, interaction_id, user_id, feedback_type, corrected_response),
        )
        await db.commit()
    try:
        from memory_metrics import chat_feedback_count

        chat_feedback_count.labels(kind=feedback_type).inc()
    except Exception:  # noqa: BLE001 - a counter never fails a write
        pass
    return row_id


async def last_assistant_message_id(session_id: str) -> Optional[str]:
    """The newest assistant reply of ``session_id`` - the answer a verdict is about. None when there is none."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        cur = await db.execute(
            "SELECT id FROM chat_messages WHERE session_id = ? AND role = 'assistant' ORDER BY created_at DESC LIMIT 1", (sid,))
        row = await cur.fetchone()
    return str(row[0]) if row else None


async def handle(text: str, user_id: str, session_id: str = "", *, speaker_verified: Optional[bool] = None,
                 allow_writes: bool = True) -> Optional[str]:
    """The reply for a kind verdict (``thumbs_up``), or None: the row for a wrong verdict is written here and the turn goes on to
    whatever answers it. NEVER raises (``CancelledError`` still propagates, so a cancelled speculative turn writes nothing)."""
    try:
        if not enabled():
            return None
        hit = classify(text)
        if hit is None:
            return None
        kind, value = hit
        if _is_guest(user_id) or speaker_verified is False:
            return None
        import asyncio

        interaction = await asyncio.wait_for(last_assistant_message_id(session_id), _DB_BUDGET_S)
        if not interaction:
            return None
        if not allow_writes:
            return UP_REPLY if kind == THUMBS_UP else None
        from provenance_answers import _speculation_barrier

        await _speculation_barrier()
        await asyncio.wait_for(record_feedback(user_id, interaction, kind, value or None), _DB_BUDGET_S)
        logger.info("CONVERSATION_FEEDBACK user=%s kind=%s", user_id, kind)
        return UP_REPLY if kind == THUMBS_UP else None
    except Exception as exc:  # noqa: BLE001 - a turn is never broken by this tier
        logger.warning("conversation_feedback failed (non-fatal): %s", type(exc).__name__)
        return None
