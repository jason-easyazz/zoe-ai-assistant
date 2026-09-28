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
    # "how have I been" style state-of-being recall. NOTE: bare "feeling" and bare
    # "how am i" are deliberately NOT here — they over-fire ("feeling like pizza",
    # "how am I supposed to configure this"); anchor to the emotional phrasings.
    "how i feel", "how i'm feeling", "how i've been feeling", "how i've been",
    "how have i been", "how i been", "been feeling", "how are you feeling",
    "feeling down", "feeling low", "feeling anxious", "feeling overwhelmed",
    "feeling stressed", "feeling sad", "feeling lonely", "feeling numb",
    "how am i doing", "how am i feeling", "how am i holding up",
    "how are things", "how's things",
    "stressed", "stressing", "stress about", "anxious", "anxiety",
    "worried", "worrying", "overwhelmed", "depressed", "burnt out", "burned out",
    "lonely", "grieving", "grief", "heartbroken", "upset", "struggling",
    "coping", "mental health", "my mood", "been down",
    # "going through" anchored so it doesn't fire on "going through my email".
    "going through a lot", "going through a hard", "going through a rough",
    "going through a tough",
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


# ── Affect at capture time (Samantha bar S4) ─────────────────────────────────
#
# The per-turn digest rewrites "Honestly I'm pretty anxious about my job
# interview at the aquarium on Friday" as the neutral fact "The user has a job
# interview at the aquarium on Friday" — the feeling is exactly what emotional
# continuity needs, and it is gone before anything is stored. The RAW turn still
# has it at capture time, so `extract_affect` reads it deterministically from the
# user's own words and the digest stores it beside the fact (`affect` metadata).
#
# First-person only: "my sister is anxious" is not the user's feeling. Each
# label maps the surface forms that express it; the label is what is rendered
# ("felt anxious").
_AFFECT_LABELS: tuple[tuple[str, str], ...] = (
    ("anxious", r"anxious|anxiety|nervous|on\s+edge|uneasy"),
    ("worried", r"worried|worrying|dreading"),
    ("stressed", r"stressed(?:\s+out)?|stressing|under\s+(?:a\s+lot\s+of\s+)?pressure"),
    ("scared", r"scared|afraid|terrified|frightened"),
    ("overwhelmed", r"overwhelmed|swamped"),
    ("sad", r"sad|down(?!\s+(?:for|to|with|here|there|at|in|on|by)\b)|low(?!\s+on\b)|"
            r"miserable|heartbroken|gutted"),
    ("upset", r"upset"),
    ("lonely", r"lonely"),
    ("frustrated", r"frustrated|annoyed|fed\s+up"),
    ("exhausted", r"exhausted|drained|burnt\s+out|burned\s+out|worn\s+out"),
    ("excited", r"excited|thrilled|pumped"),
    ("happy", r"happy|delighted|over\s+the\s+moon"),
    ("proud", r"proud"),
    ("relieved", r"relieved"),
)
_AFFECT_SOFTENERS = (
    r"(?:(?:so|really|pretty|quite|super|very|just|still|totally|kind\s+of|kinda|"
    r"sort\s+of|a\s+bit|a\s+little|a\s+little\s+bit|bit|incredibly|extremely|"
    r"honestly|feeling|more|getting)\s+){0,3}"
)
_AFFECT_ANCHOR = (
    r"(?:\bi(?:['’]?m|\s+am|['’]?ve\s+been|\s+have\s+been|\s+was|\s+feel|\s+felt|"
    r"\s+keep\s+feeling|\s+still\s+feel|\s+get|\s+got)|(?:^|[.!?,]\s*)\s*feeling)\s+"
)
_AFFECT_RES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(_AFFECT_ANCHOR + _AFFECT_SOFTENERS + r"(?:" + forms + r")\b",
                       re.IGNORECASE))
    for label, forms in _AFFECT_LABELS
) + (("excited", re.compile(r"\bi\s+can['’]?t\s+wait\b", re.IGNORECASE)),)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Someone ELSE's words are never the user's feeling (Greptile #1762):
#   * quoted spans — straight/curly double quotes, curly single quotes, and a
#     straight single-quoted span that opens after whitespace and closes before a
#     boundary (so the apostrophe in "I'm" is not taken for a quote);
#   * reported speech — "<someone other than I> said/says/told me/… (that) …" to
#     the end of the sentence ("my sister said she is so stressed").
_QUOTED_SPAN_RE = re.compile(
    r"\"[^\"]*\"|“[^”]*”|‘[^’]*’|(?:(?<=\s)|^)'.*?'(?=\s|[,.!?;:]|$)"
)
_REPORTED_SPEECH_RE = re.compile(
    r"\b(?!i\b)[a-z']+\s+(?:said|says|say|told\s+(?:me|us)|tells\s+(?:me|us)|"
    r"asked|texted|wrote|messaged|mentioned|reckons|thinks)\b[^.!?]*",
    re.IGNORECASE,
)


def _own_words(sentence: str) -> str:
    """``sentence`` with quoted spans and reported speech removed."""
    return _REPORTED_SPEECH_RE.sub(" ", _QUOTED_SPAN_RE.sub(" ", sentence))


def extract_affect(message: str) -> tuple[str, str]:
    """(label, sentence) for the first first-person feeling in ``message``, or
    ("", "") — e.g. "Honestly I'm pretty anxious about my job interview…" →
    ("anxious", "Honestly I'm pretty anxious about my job interview…"). The
    sentence is returned so a caller can attach the feeling only to facts drawn
    from it. Quoted and reported words are someone else's (``_own_words``).
    Pure str → tuple, no env read."""
    for sentence in _SENTENCE_SPLIT_RE.split(message or ""):
        own = _own_words(sentence)
        best: tuple[int, str] | None = None
        for label, rx in _AFFECT_RES:
            m = rx.search(own)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), label)
        if best is not None:
            return best[1], sentence.strip()
    return "", ""
