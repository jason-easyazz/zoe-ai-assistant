"""Single source of truth for the memory-recall keyword gate.

The expensive MemPalace semantic search (ONNX embed + Chroma query) should only
run when a message looks like a recall / personal-fact query, not on every turn.
Both the legacy `zoe_agent` brain and the `/api/memories/for-prompt` endpoint
(which the Pi `memory.ts` extension calls each turn) gate on this — so the words
live HERE, imported by both, to avoid the two copies silently diverging.

Dependency-free on purpose (pure str → bool) so any module can import it cheaply.
"""
from __future__ import annotations

MEMORY_TRIGGER_WORDS = frozenset({
    "remember", "recall", "did i", "have i", "last time", "before",
    "you said", "we talked", "my name", "my preference", "i told you",
    "favourite", "favorite", "prefer", "like", "usually", "always", "never", "often",
    "who is", "what is my", "what do i", "what's my", "family", "remind me",
    "do i have", "my favourite", "my favorite", "my usual", "i usually", "i like",
    "i prefer", "i love", "i hate", "i enjoy", "do you know my",
    # Personal-fact retrieval phrases.
    "born", "age", "years old", "my age", "how old", "my birthday", "birthday",
    "my full name", "called", "known as", "allerg", "condition", "medical",
})


def message_needs_memory(message: str) -> bool:
    """True only when the message likely benefits from MemPalace semantic search."""
    low = (message or "").lower()
    return any(kw in low for kw in MEMORY_TRIGGER_WORDS)
