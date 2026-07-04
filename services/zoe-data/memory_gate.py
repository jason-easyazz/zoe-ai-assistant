"""Single source of truth for the memory-recall keyword gate.

The expensive MemPalace semantic search (ONNX embed + Chroma query) should only
run when a message looks like a recall / personal-fact query, not on every turn.
Both the legacy `zoe_agent` brain and the `/api/memories/for-prompt` endpoint
(which the Pi `memory.ts` extension calls each turn) gate on this — so the words
live HERE, imported by both, to avoid the two copies silently diverging.

Dependency-free on purpose (pure str → bool) so any module can import it cheaply.
"""
from __future__ import annotations

import re

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


# Emotional-state cues — recall of stored `emotional_moment` rows (Samantha
# criterion #2 continuity). Kept SEPARATE from MEMORY_TRIGGER_WORDS: this set is
# consulted only by `message_needs_emotional_recall`, which the for-prompt
# endpoint calls behind ZOE_EMOTIONAL_RECALL_ENABLED — so the default recall gate
# is unchanged until that flag is on. Substrings, lowercased. Covers the user
# *asking* about their state ("how have I been", "how am I doing") and *sharing*
# one ("I've been so stressed", "feeling overwhelmed") — both are cues to pull
# past emotional continuity. Both valences on purpose (the 4B brain under-captures
# joy, so we must not also under-recall it).
EMOTIONAL_TRIGGER_WORDS = frozenset({
    "feeling", "how i feel", "how i've been", "how have i been", "how i been",
    "how am i", "how are things", "how's things", "how are you feeling",
    "stressed", "stressing", "stress about", "anxious", "anxiety",
    "worried", "worrying", "overwhelmed", "depressed", "burnt out", "burned out",
    "lonely", "grieving", "grief", "heartbroken", "upset", "struggling",
    "coping", "mental health", "going through", "my mood", "been down",
    # positive-valence emotional recall (symmetry with joy capture)
    "excited about", "so happy", "happy about", "made me happy", "makes me happy",
    "made me smile", "proud of", "thrilled", "over the moon",
})


# A possessive self-reference ("my dad", "our house") — a strong recall signal.
# NOTE: deliberately excludes the object pronoun "me" (it appears in request
# phrases "tell me / give me / remind me" that are not recall).
_POSSESSIVE_RE = re.compile(r"\b(?:my|mine|our|ours)\b", re.IGNORECASE)
# A first-person SUBJECT ("I", "we") — the user asking about their own state.
# Just \b(?:i|we)\b: the apostrophe in contractions ("I'm", "we're") is a word
# boundary, so this still matches the subject of "I've", "we're", etc., while NOT
# matching "ill" or "were" (a letter follows, so no boundary) — avoiding the
# false positives an optional-apostrophe pattern (i'?ll → "ill") would cause.
_FIRST_PERSON_SUBJ_RE = re.compile(r"\b(?:i|we)\b", re.IGNORECASE)
# A question / recall shape — a leading interrogative or request-to-tell.
_QUESTION_SHAPE_RE = re.compile(
    r"^\s*(?:hey\s+|ok\s+|so\s+|um\s+|uh\s+|zoe[,\s]+)*"
    r"(?:who|what|what'?s|when|when'?s|where|where'?s|why|how|which|whose|"
    r"do|does|did|are|is|was|were|have|has|can|could|would|will|should|am|"
    r"tell\s+me|remind\s+me)\b",
    re.IGNORECASE,
)
# Procedural "how do/can I …" — a how-to, NOT a recall of stored facts.
_PROCEDURAL_HOW_RE = re.compile(r"^\s*how\s+(?:do|can|would|should|could)\s+(?:i|we|you)\b", re.IGNORECASE)


def message_needs_memory(message: str) -> bool:
    """True when the message likely benefits from MemPalace semantic search.

    Fires on either (a) an explicit trigger word/phrase, or (b) a *structural*
    signal: a self-reference in a question/recall shape. The structural rule
    catches natural recall phrasings the keyword list misses ("where do I live",
    "tell me about my mum", "what team do we support") while staying off
    non-personal questions ("what's the weather") and procedural how-tos
    ("how do I make pasta") that would waste the embed.
    """
    text = message or ""
    low = text.lower()
    if any(kw in low for kw in MEMORY_TRIGGER_WORDS):
        return True
    is_question = bool(_QUESTION_SHAPE_RE.match(text)) or low.rstrip().endswith("?")
    if not is_question:
        return False
    # A possessive ("my/our") in a question is recall regardless of phrasing.
    if _POSSESSIVE_RE.search(text):
        return True
    # A first-person subject question ("where do I live") is recall too — unless
    # it's a procedural "how do I <action>".
    if _FIRST_PERSON_SUBJ_RE.search(text) and not _PROCEDURAL_HOW_RE.match(text):
        return True
    return False


def message_needs_emotional_recall(message: str) -> bool:
    """True when the message carries an emotional-state cue worth pulling stored
    `emotional_moment` rows for. Kept SEPARATE from `message_needs_memory` so the
    default recall gate is unchanged: the for-prompt endpoint ORs this in only
    when ZOE_EMOTIONAL_RECALL_ENABLED is set. Pure str → bool, no env read here
    (the flag decision stays at the endpoint), so both callers stay cheap."""
    low = (message or "").lower()
    return any(cue in low for cue in EMOTIONAL_TRIGGER_WORDS)
