"""Write-quality gate for Zoe's conversational memory (mem0-style).

The store was polluted because the conversational write paths accepted
anything: interrogatives stored as facts ("Do you remember what my mum's name
is?"), LLM meta-rambling ("The provided statements illustrate a pattern…"),
and near-duplicate / contradictory rows ("My dad's name is Neil" written 5×).

This module is the single source of truth for *whether a conversational
candidate is shaped like a storable personal fact* and for the *near-dedup /
supersession* decision. It is:

  * Pure + offline — `is_storable_fact` is a deterministic string check (regex
    only, no model call, no I/O), so it is safe to unit-test exhaustively and
    cheap to run on every write.
  * CONSERVATIVE — a false-reject (losing a real fact) is worse than a stored
    fact, so anything that isn't a clear non-fact is ACCEPTED. The reject rules
    only fire on shapes we have actually observed as junk.

It is wired into the CONVERSATIONAL writers only (expert_dispatch.store_fact,
memory_extractor.extract_and_ingest, zoe_agent._mempalace_add). The structured
writers (notes / journal / people / user_profile routers) are already
structured and are NOT gated here.

The gate runs on WRITE, which is always a background path — it never blocks the
spoken voice reply.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional


# ---------------------------------------------------------------------------
# is_storable_fact — fact-shape check
# ---------------------------------------------------------------------------

# Minimum useful length. Below this it's almost never a real fact ("ok", "yes").
_MIN_LEN = 6

# Interrogative openers — "do/did/can you remember…", "what/when/where/who is…".
# These are RECALL questions, not facts. expert_dispatch.store_fact already has
# a richer store-vs-recall split; this is the defensive backstop for the other
# writers (and a second layer for store_fact).
_QUESTION_OPENER_RE = re.compile(
    r"^\s*(?:hey\s+|ok\s+|so\s+|um\s+|uh\s+)*"
    r"(?:"
    r"do|did|does|don'?t|can|could|would|will|should|are|is|was|were|have|has|had|am|"
    r"what'?s?|what|when'?s?|when|where'?s?|where|who'?s?|who|whose|which|why|how|"
    r"remind\s+me\s+(?:what|who|when|where|how|why|which)"
    r")\b",
    re.IGNORECASE,
)

# Low-information meta / LLM-rambling. Observed: rows starting with "The
# provided…", "The context…", "The concept…", "The following…", "Based on…",
# "It seems…", "These statements…". These are model commentary, never a fact.
_META_OPENER_RE = re.compile(
    r"^\s*(?:"
    r"the\s+(?:provided|context|concept|following|statement|statements|above|user'?s|"
    r"information|text|passage|conversation|response|key|main|overall)|"
    r"these\s+(?:statements?|facts?|points?|examples?)|"
    r"this\s+(?:statement|passage|text|response|conversation)|"
    r"based\s+on\b|"
    r"in\s+summary\b|"
    r"to\s+summari[sz]e\b|"
    r"it\s+(?:seems|appears|looks\s+like)\b|"
    r"here\s+(?:is|are)\s+(?:a\s+)?(?:summary|some|the)\b|"
    r"as\s+an\s+ai\b|"
    r"i\s+(?:cannot|can'?t|don'?t\s+have)\b"
    r")",
    re.IGNORECASE,
)

# A first/second-person subject — "I …", "my …", "you …", "your …", "we / our".
# A genuine personal fact almost always has one. Memory-command forms
# ("remember that …", "note that …") are personal facts too.
_PERSONAL_SUBJECT_RE = re.compile(
    r"\b(?:i|i'?m|i'?ve|i'?ll|i'?d|my|mine|me|myself|"
    r"you|you'?re|you'?ve|your|yours|"
    r"we|we'?re|our|ours)\b",
    re.IGNORECASE,
)
# A "do/can/will YOU remember…?" RECALL question contains 'remember' but is NOT
# a teach command — exclude it so the question check still fires.
_RECALL_QUESTION_RE = re.compile(
    r"\b(?:do|did|does|can|could|would|will)\s+(?:you|ya)\s+"
    r"(?:remember|recall|recollect|know|have)\b",
    re.IGNORECASE,
)
# An imperative memory command ("remember that …", "note that …"). The
# lookbehinds stop "you/ya/i remember…" (a recall) from reading as a command.
_MEMORY_COMMAND_RE = re.compile(
    r"\b(?:don'?t\s+forget|note\s+that|make\s+a\s+note|keep\s+in\s+mind)\b"
    r"|(?<!you )(?<!ya )(?<!i )\bremember\b",
    re.IGNORECASE,
)
# A concrete noun-ish token: a capitalised word (a name/place) or a digit
# (date, number, age). Used only as a fallback when there is NO personal
# subject — to avoid rejecting an odd-but-real fragment.
_CONCRETE_TOKEN_RE = re.compile(r"\b[A-Z][a-z]+\b|\d")

# Some extraction templates emit a third-person summary ("User's name is …",
# "Preference: user likes …", "Person the user met: …"). Those are legitimate
# stored shapes even though they lack a first/second-person pronoun.
_EXTRACTED_PREFIX_RE = re.compile(
    r"^\s*(?:user(?:'?s)?\b|preference\b|favou?rite\b|person\s+the\s+user\b|"
    r"important\s+note\b)",
    re.IGNORECASE,
)


def is_storable_fact(text: str) -> tuple[bool, str]:
    """Return ``(storable, reason)`` for a conversational memory candidate.

    REJECT (storable=False) clear non-facts:
      * empty / too short
      * interrogatives / questions ("do you remember…?", trailing "?")
      * low-information meta / LLM-rambling ("The provided…", "Based on…")
      * no personal subject AND no concrete noun (generic filler)

    ACCEPT (storable=True) declarative personal facts/preferences
    ("My dad's name is Neil", "I prefer morning coffee", "remember that …").

    Lean toward ACCEPT when unsure — a false-reject loses a real fact, which is
    worse than letting one junk row through. ``reason`` is "" when accepted, or a
    short stable label (for metrics/logging) when rejected.
    """
    raw = (text or "").strip()
    if not raw:
        return False, "empty"
    if len(raw) < _MIN_LEN:
        return False, "too_short"

    # A memory command ("remember that my mum likes NCIS") is always a fact,
    # even though it can superficially look like other shapes — accept early.
    # But "do you remember…?" is a recall question, not a command.
    has_memory_command = bool(_MEMORY_COMMAND_RE.search(raw)) and not _RECALL_QUESTION_RE.search(raw)

    # Trailing "?" or an interrogative opener → a question, not a fact. But a
    # memory command that merely *contains* a question word ("remember that I
    # asked who's coming") is still a teach, so the command guard wins.
    if not has_memory_command:
        if raw.rstrip().endswith("?"):
            return False, "question_mark"
        if _QUESTION_OPENER_RE.match(raw):
            return False, "interrogative"

    # LLM meta-rambling — never a personal fact.
    if _META_OPENER_RE.match(raw):
        return False, "meta_rambling"

    if has_memory_command:
        return True, ""

    # Accept the structured-extraction summary shapes ("User's name is …").
    if _EXTRACTED_PREFIX_RE.match(raw):
        return True, ""

    # A genuine personal fact has a first/second-person subject.
    if _PERSONAL_SUBJECT_RE.search(raw):
        return True, ""

    # No personal subject. Only keep it if it carries a concrete noun/number
    # (could be a real but oddly-phrased fact); otherwise it's generic filler.
    if _CONCRETE_TOKEN_RE.search(raw):
        return True, ""
    return False, "no_subject_no_concrete"


# ---------------------------------------------------------------------------
# Near-dedup / supersession (mem0 ADD vs UPDATE idea)
# ---------------------------------------------------------------------------

# Pull the attribute being asserted from "my <attr> is/are/was <value>" or
# "<subject>'s <attr> is <value>" so we can tell "my dad's name is Neil" from
# "my dad's name is spelt N-E-I-L" (same attribute → supersede, not add).
_ATTR_RE = re.compile(
    r"\bmy\s+([a-z][a-z '`-]*?)\s+(?:is|are|was|were|=|:)\b",
    re.IGNORECASE,
)

# How similar two candidate texts must be to count as "the same fact" for the
# skip-near-exact-duplicate path. High, to stay conservative.
_NEAR_DUP_RATIO = 0.92
# Lower bar for "same attribute, different value" supersession — we additionally
# require the extracted attribute keys to match, so this can be looser.
_SUPERSEDE_RATIO = 0.55


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .!?,")


def _attribute_key(text: str) -> Optional[str]:
    """Best-effort: the attribute a 'my X is Y' statement is about ('dad name').

    Returns None when the text isn't an attribute assertion (then we fall back
    to pure similarity for the dedup decision)."""
    m = _ATTR_RE.search(text or "")
    if not m:
        return None
    attr = re.sub(r"[^a-z ]+", " ", m.group(1).lower())
    # collapse "dad 's name" → "dad name", drop possessive noise words
    tokens = [t for t in attr.split() if t not in {"s", "the", "a", "an"}]
    return " ".join(tokens).strip() or None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def classify_against_existing(
    text: str,
    existing: list[tuple[str, str]],
) -> tuple[str, Optional[str]]:
    """Decide ADD / UPDATE / SKIP for a candidate against existing memories.

    ``existing`` is a list of ``(mem_id, mem_text)`` from a semantic search
    (e.g. ``MemoryService.search(text, user_id, limit≈3)``). Returns:

      * ("add",    None)    — no close match; store as new.
      * ("update", mem_id)  — a near-exact duplicate OR a same-attribute,
                              different-value fact → supersede ``mem_id``
                              instead of adding a duplicate/contradiction.
      * ("skip",   mem_id)  — defensively unused today; reserved.

    Conservative: only returns "update" when we are confident the existing row
    is the same fact (high text similarity) or unambiguously the same attribute
    (matching attribute key + moderate similarity). Anything else → "add".
    """
    if not existing:
        return "add", None

    cand_attr = _attribute_key(text)
    best_dup: tuple[float, Optional[str]] = (0.0, None)
    best_attr: tuple[float, Optional[str]] = (0.0, None)

    for mem_id, mem_text in existing:
        if not mem_text:
            continue
        sim = _similarity(text, mem_text)
        if sim > best_dup[0]:
            best_dup = (sim, mem_id)
        if cand_attr and _attribute_key(mem_text) == cand_attr and sim > best_attr[0]:
            best_attr = (sim, mem_id)

    # 1) Near-exact duplicate → supersede the old row (collapses repeats beyond
    #    the exact-idempotency check MemoryService already does).
    if best_dup[0] >= _NEAR_DUP_RATIO and best_dup[1]:
        return "update", best_dup[1]

    # 2) Same attribute, different value ("dad's name is Neil" vs "…spelt
    #    N-E-I-L") → supersede rather than accumulate contradictions.
    if best_attr[1] and best_attr[0] >= _SUPERSEDE_RATIO:
        return "update", best_attr[1]

    return "add", None


__all__ = ["is_storable_fact", "classify_against_existing"]
