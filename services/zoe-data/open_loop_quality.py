"""Open-loop quality rules: which user turns may feed the extractor, and which
extracted loops name something real. Deterministic; no model, no I/O.

Owner review of the first real extraction (2026-09-30): two of four loops were Zoe's
own mechanics — a "let's talk" opener read as "wants to talk continuously", a
correction read as "a problem that needs fixing" — vague, with no entity. The rules
reuse the existing classifiers; the only new word list covers nouns none of them has.
Used by ``memory_digest._extract_open_loops`` (and, later, the proactivity selector).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Event/appointment, role and place nouns the reused lists lack
# (intent_router.EVENT_CATEGORY_HINTS already has doctor/dentist/hospital/interview/…).
_ANCHOR_NOUNS = frozenset({
    # events, appointments, errands with a date
    "appointment", "meeting", "exam", "results", "scan", "surgery", "operation",
    "trip", "travel", "travelling", "traveling", "flight", "holiday", "visit", "visiting",
    "wedding", "funeral", "birthday", "party", "deadline", "moving", "course",
    "passport", "visa", "licence", "license", "tax", "bill", "invoice", "rent", "quote",
    # people by role (relations come from memory_gate)
    "plumber", "builder", "electrician", "mechanic", "landlord", "lawyer", "vet",
    "teacher", "coach", "manager", "client",
    # places
    "work", "job", "home", "house", "uni", "university", "airport", "gym", "car",
    "bedroom", "bathroom", "kitchen", "garden", "garage", "shed",
})
_NAME_STOP = frozenset({"User", "Users", "Zoe"})
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")

# Intents that carry what the user SHARED stay extractor input; every other
# deterministic intent is a command Zoe handled on the spot, a greeting/ack, or a
# meta turn about Zoe herself (user_issue_report, memory_forget_*, lets_talk).
_CONTENT_INTENTS = frozenset({
    "memory_remember", "journal_create", "note_create", "people_create", "people_introduce",
})


def loop_anchors(text: str) -> set[str]:
    """Lowercased anchor words in a loop text: a relation (memory_gate._EVT_REL), a
    mid-sentence capitalised name (person_extractor_llm's stoplist), an event, role or
    place noun (intent_router.EVENT_CATEGORY_HINTS + ``_ANCHOR_NOUNS``), and under
    ``ZOE_LOOP_LIFECYCLE`` a health condition (``open_loop_lifecycle.HEALTH_NOUNS``).
    No time words."""
    from intent_router import EVENT_CATEGORY_HINTS
    from memory_gate import _EVT_REL
    from open_loop_lifecycle import HEALTH_NOUNS, lifecycle_enabled
    from person_extractor_llm import _CAP_STOP, _CAP_TOKEN

    raw = text or ""
    nouns = _ANCHOR_NOUNS.union(*EVENT_CATEGORY_HINTS.values())
    if lifecycle_enabled():  # a named condition is a thing to ask about (flag-dark)
        nouns = nouns | HEALTH_NOUNS
    found = {t for t in re.findall(r"[a-z0-9:'-]+", raw.lower())
             if t in nouns or t.rstrip("s") in nouns}
    found |= {m.lower() for m in re.findall(rf"\b(?:{_EVT_REL})\b", raw, re.IGNORECASE)}
    for sentence in _SENTENCE_SPLIT_RE.split(raw):
        # Position rule on top of the stoplist: the LLM writes loops, so a
        # sentence-initial capital ("Expressed…", "Feeling…") is never a name.
        found |= {m.group().lower() for m in _CAP_TOKEN.finditer(sentence)
                  if m.start() > 0 and m.group() not in _CAP_STOP | _NAME_STOP}
    return found


def loop_is_concrete(text: str) -> bool:
    """True when a loop names a person, event, place or time (memory_gate._EVT_TIME)."""
    from memory_gate import _EVT_TIME

    return bool(loop_anchors(text) or re.search(rf"\b{_EVT_TIME}\b", text or "", re.IGNORECASE))


def loop_turn_is_meta(text: str) -> bool:
    """True for a user turn about Zoe's mechanics, not the user's life: a wake-only
    line (voice_presence), a conversation opener/ender (conversation_opener), a
    correction (memory_quality.looks_like_correction), or a deterministic
    command/meta intent (intent_router.detect_intent). Unknown → kept."""
    try:
        from conversation_opener import is_conversation_ender, is_conversation_opener
        from intent_router import detect_intent
        from memory_quality import looks_like_correction
        from voice_presence import is_wake_text

        if (is_wake_text(text) or is_conversation_opener(text) or is_conversation_ender(text)
                or looks_like_correction(text)):
            return True
        intent = detect_intent(text, log_miss=False)
        return intent is not None and intent.name not in _CONTENT_INTENTS
    except Exception as exc:  # noqa: BLE001 — a classifier failure keeps the turn
        logger.debug("open_loop_quality: meta-turn check failed: %s", type(exc).__name__)
        return False
