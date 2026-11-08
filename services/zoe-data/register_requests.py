"""A member's own words about HOW Zoe speaks, stored as their register - riding ``ZOE_MEMBER_REGISTER`` (no flag of its own).

THE GAP (QA round 5, 2026-11-08, three adult demo users, 297 turns): ``ZOE_MEMBER_REGISTER=on`` changed nothing anyone could hear.
Not a bug in the renderer - there was NOTHING TO RENDER. The register is a table of ``stated`` / ``confirmed`` rows, written only
by the owner-facing routes; the producer that was to turn a member's own sentence ("keep it short") into a row was left as a
follow-up (docs/knowledge/member-register.md) and an inferred row is never rendered. So a slangy, a formal and a terse member all met
the same neutral voice, and "stop ending every reply with a question" was agreed to in words and kept nowhere.

This is that producer, for EXPLICIT requests only: an imperative aimed at Zoe, in the member's own turn, that names a setting the
register already has (length, formality, humour, questions). It is not inference - nothing here guesses a style from how someone
types; that stays an inferred-row proposal for the night loop to make and the member to confirm. A match is written as ``stated``
(the member's own words: the highest rank, it lifts a tombstone and nothing overrides it), BEFORE the register line is rendered
for this same turn, so the reply to "stop ending with questions" already obeys it and every turn after does too.

Who: an adult only (``member_register.audience``); a guest, a child and every failed lookup store nothing and the turn is unchanged.
Only a short turn is read (``MAX_CHARS``) and the request must OPEN the turn after a courtesy lead ("ok", "zoe,", "can you",
"from now on"): "my boss told me to keep it short" is a story, not an instruction. English only, as the rest of the pattern-shaped
reply code is; another language stores nothing (never a wrong guess).

Stdlib only apart from a lazy ``member_register`` import. Never raises into a turn.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MAX_CHARS = 140

_LEAD = (r"^\W*(?:(?:ok(?:ay)?|hey|hi|zoe|please|pls|and|also|just|so|right|look|actually|can\s+you|could\s+you|would\s+you|will\s+you|"
         r"from\s+now\s+on|going\s+forward|in\s+future|next\s+time|i\s+(?:said|asked(?:\s+you)?|told\s+you|want\s+you|need\s+you|"
         r"would\s+like\s+you|d\s+like\s+you)(?:\s+to)?)[\s,.:;-]+)*")
_APOS = "['’]?"
_NEG = r"(?:stop|quit|don" + _APOS + r"t|do\s+not|never|no\s+more|please\s+don" + _APOS + r"t)"
_ENDING = r"(?:end(?:ing)?|finish(?:ing)?|clos(?:e|ing)|wrap(?:ping)?(?:\s+up)?)"

#: (key, value, pattern) - the first rule that matches wins, so a more specific rule sits ahead of a general one
RULES: tuple[tuple[str, str, "re.Pattern[str]"], ...] = tuple(
    (key, value, re.compile(_LEAD + body, re.IGNORECASE))
    for key, value, body in (
        # questions: "stop ending with questions", "dont end every reply with a question pls", "no more questions", "stop asking me questions"
        ("questions", "none", _NEG + r"\s+(?:\w+\s+){0,3}?" + _ENDING + r"\s+(?:\w+\s+){0,4}?(?:with|on|in)\s+(?:a\s+|an\s+)?questions?\b"),
        ("questions", "none", r"stop\s+asking\s+(?:me\s+)?(?:so\s+many\s+|all\s+these\s+|all\s+those\s+|those\s+)?questions\b"),
        ("questions", "none", r"no\s+more\s+questions\b"),
        ("questions", "normal", r"you\s+can\s+(?:ask|end\s+with)\s+(?:me\s+)?questions\s+again\b"),
        # length
        ("length", "terse", r"(?:keep|make)\s+(?:it|them|things|replies|your\s+replies|your\s+answers|answers|responses|your\s+responses)\s+"
                            r"(?:short|brief|shorter|quick|snappy)\b"),
        ("length", "terse", r"(?:be|stay)\s+(?:a\s+bit\s+|more\s+|much\s+|way\s+)*(?:brief|concise|short|terse)\b"),
        ("length", "terse", r"(?:shorter|briefer|short)\s+(?:replies|answers|responses)\b"),
        ("length", "terse", r"(?:less|stop\s+the)\s+waffl(?:e|ing)\b"),
        ("length", "fuller", r"(?:longer|fuller|more\s+detailed)\s+(?:replies|answers|responses)\b"),
        ("length", "fuller", r"(?:give\s+me|go\s+into|i\s+want|i\s+d\s+like)\s+more\s+detail\b"),
        # formality
        ("formality", "casual", r"(?:be|sound|talk|speak|chat|write)\s+(?:to\s+me\s+)?(?:a\s+bit\s+|a\s+little\s+|way\s+|much\s+)*(?:more\s+)?"
                                r"(?:casual(?:ly)?|relaxed|chill|laid\s*back|informal(?:ly)?)\b"),
        ("formality", "casual", r"(?:loosen\s+up|lighten\s+up|chill\s+out)\b"),
        ("formality", "casual", r"talk\s+(?:to\s+me\s+)?like\s+a\s+(?:mate|friend)\b"),
        ("formality", "formal", r"(?:be|sound|talk|speak|write)\s+(?:to\s+me\s+)?(?:a\s+bit\s+|a\s+little\s+|much\s+)*(?:more\s+)?"
                                r"(?:formal(?:ly)?|professional(?:ly)?)\b"),
        # humour
        ("humour", "off", r"(?:no\s+(?:more\s+)?jokes|stop\s+(?:making\s+|with\s+the\s+|cracking\s+)?jokes|" + _NEG + r"\s+(?:joke|try\s+to\s+be\s+funny))\b"),
        ("humour", "light", r"(?:be\s+(?:a\s+bit\s+)?(?:funnier|funny)|crack\s+(?:some\s+)?jokes|make\s+(?:me\s+)?(?:some\s+)?jokes)\b"),
    )
)


def parse(message: str) -> list[tuple[str, str]]:
    """The register settings an explicit request in ``message`` asks for, as ``[(key, value), ...]`` (one per key, in rule order).
    ``[]`` for anything that is not a short imperative aimed at Zoe. Pure."""
    msg = " ".join((message or "").split())
    if not msg or len(msg) > MAX_CHARS:
        return []
    seen: dict[str, str] = {}
    for key, value, rx in RULES:
        if key not in seen and rx.match(msg):
            seen[key] = value
    return list(seen.items())


async def apply(user_id: str, message: str) -> list[tuple[str, str, str]]:
    """Store what ``message`` asks for as the member's ``stated`` register. ``[(key, value, written|kept), ...]``; ``[]`` when the
    register is off, nothing matched, or the speaker is not a positively-adult member. Never raises."""
    try:
        import member_register as mr

        if not mr.enabled():
            return []
        wanted = parse(message)
        if not wanted:
            return []
        uid = (user_id or "").strip()
        if await mr.audience(uid) != mr.AUDIENCE_ADULT:
            return []
        out = []
        for key, value in wanted:
            out.append((key, value, await mr.set_entry(uid, key, value, "stated", updated_by=uid)))
        logger.info("REGISTER_REQUEST user=%s keys=%s", uid, ",".join(f"{k}={v}" for k, v, _ in out))
        return out
    except Exception:  # noqa: BLE001 - a style request is optional; the turn is not
        logger.warning("REGISTER_REQUEST failed; nothing stored", exc_info=True)
        return []

