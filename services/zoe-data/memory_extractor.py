"""Unified conversational memory extraction for Zoe.

This module turns user utterances into structured memory candidates and can
persist them through MemoryService. It is intentionally lightweight:
- regex/template extraction (no model call)
- conservative skip rules to avoid noisy writes
- idempotent ingest via MemoryService
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MemoryCandidate:
    text: str
    memory_type: str = "fact"
    title: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    confidence: float = 0.72
    source_excerpt: str = ""


_SKIP_PREFIXES = (
    "what is",
    "what are",
    "how do",
    "how does",
    "explain",
    "tell me about",
    "what time",
    "what day",
    "what date",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank you",
)


_TEMPLATE_PATTERNS: list[tuple[str, str, float]] = [
    (r"(?:please\s+)?remember\s+(?:that\s+|this\s+)?(.{5,280})", "User asked me to remember: {0}", 0.95),
    (r"don'?t\s+forget\s+(?:that\s+)?(.{5,220})", "Important note: {0}", 0.9),
    (r"i\s+(?:prefer|like|love|enjoy)\s+(.{3,120})", "Preference: user likes {0}", 0.78),
    (r"i\s+(?:don'?t\s+like|dislike|hate)\s+(.{3,120})", "Preference: user dislikes {0}", 0.78),
    (r"my\s+favou?rite\s+(?:\w+\s+)?is\s+(.{2,90})", "Favourite: {0}", 0.8),
    (r"i\s+live\s+in\s+(.{2,80})", "User lives in {0}", 0.8),
    (r"i\s+work\s+(?:at|for)\s+(.{2,80})", "User works at/for {0}", 0.8),
    (r"i(?:'m|\s+am)\s+from\s+(.{2,80})", "User is from {0}", 0.78),
    (r"my\s+(?:lucky\s+)?number\s+is\s+(\d+)", "User's lucky number is {0}", 0.85),
]


_PERSON_PATTERN = re.compile(
    r"\bi\s+met\s+([A-Za-z][A-Za-z' -]{1,48}?)(?=\s+(?:who|that|and|he|she|they|is|was)\b|[.,;!?]|$)"
    r"(?:[,\s]+(?:who|that|and)?\s*(?:is|was)\s+(?:an|a)?\s*([A-Za-z][A-Za-z' -]{1,64}))?",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip(" \t\n\r.,;:!?"))


def _person_name_from_fragment(fragment: str) -> str:
    stop = {"who", "that", "and", "is", "was", "she", "he", "they", "today", "tomorrow", "yesterday"}
    out: list[str] = []
    for raw in re.split(r"\s+", _clean(fragment)):
        tok = raw.strip(".,;:!?")
        if not tok:
            continue
        low = tok.lower()
        if low in stop:
            break
        if not re.fullmatch(r"[A-Za-z][A-Za-z'_-]*", tok):
            break
        out.append(tok)
        if len(out) >= 2:
            break
    return " ".join(out)


def _should_skip(user_message: str) -> bool:
    msg = _clean(user_message).lower()
    if not msg:
        return True
    if msg.startswith("remember "):
        return False
    return any(msg.startswith(prefix) for prefix in _SKIP_PREFIXES)


def extract_candidates(user_message: str, assistant_response: str = "") -> list[MemoryCandidate]:
    """Extract memory candidates from a user turn."""
    if _should_skip(user_message):
        return []

    source_excerpt = _clean(user_message)[:220]
    out: list[MemoryCandidate] = []
    seen: set[str] = set()

    for pattern, template, confidence in _TEMPLATE_PATTERNS:
        m = re.search(pattern, user_message, flags=re.IGNORECASE)
        if not m:
            continue
        groups = tuple(_clean(g) for g in m.groups())
        if any(not g for g in groups):
            continue
        text = _clean(template.format(*groups))
        key = text.lower()
        if len(text) < 8 or key in seen:
            continue
        seen.add(key)
        out.append(
            MemoryCandidate(
                text=text,
                memory_type="fact",
                confidence=confidence,
                source_excerpt=source_excerpt,
            )
        )

    pm = _PERSON_PATTERN.search(user_message)
    if pm:
        person = _person_name_from_fragment(pm.group(1))
        detail = _clean(pm.group(2) or "")
        if person:
            text = f"Person the user met: {person}"
            if detail:
                text += f" ({detail})"
            key = text.lower()
            if key not in seen:
                seen.add(key)
                out.append(
                    MemoryCandidate(
                        text=text,
                        memory_type="person",
                        title=person,
                        entity_type="person",
                        entity_id=person.lower().replace(" ", "_"),
                        confidence=0.86,
                        source_excerpt=source_excerpt,
                    )
                )

    return out


async def extract_and_ingest(
    user_message: str,
    assistant_response: str = "",
    *,
    user_id: str,
    session_id: Optional[str] = None,
    source: str = "chat_regex",
    auto_approve: bool = True,
) -> int:
    """Extract candidates and ingest them via MemoryService.

    Returns the number of successfully written/accepted candidates.
    """
    from memory_service import get_memory_service

    candidates = extract_candidates(user_message, assistant_response)
    if not candidates:
        return 0

    svc = get_memory_service()
    saved = 0
    base_turn_id = hashlib.sha1(user_message.encode("utf-8", "ignore")).hexdigest()[:16]
    status = "approved" if auto_approve else "pending"

    for idx, c in enumerate(candidates):
        user_turn_id = f"{base_turn_id}-{idx}"
        ref = await svc.ingest(
            c.text,
            user_id=user_id,
            source=source,
            session_id=session_id,
            user_turn_id=user_turn_id,
            memory_type=c.memory_type,
            confidence=c.confidence,
            status=status,
            tags=["conversation", "auto_extract"],
            entity_type=c.entity_type,
            entity_id=c.entity_id,
        )
        if ref is not None:
            saved += 1
    return saved


__all__ = ["MemoryCandidate", "extract_candidates", "extract_and_ingest"]

