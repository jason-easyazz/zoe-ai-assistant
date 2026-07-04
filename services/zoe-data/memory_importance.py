"""Importance scoring for stored memories (Samantha increment 3b).

A small, dependency-free scorer that flags genuinely high-stakes facts so the 2a
hybrid-retrieval importance arm (`MemoryService._semantic_search`, which reads
`metadata["importance"]`) can rank them up. It only *matters* for memory_types
NOT already in `_HYBRID_PREFERENCE_TYPES`
(preference/approval/emotional_moment/person/recurring_task already score 1.0 via
the type arm) — so a plain ``memory_type="fact"`` like a penicillin allergy, a
coeliac diet, or a next-of-kin detail otherwise gets no boost at all.

Pure ``str -> float`` in [0, 1], no deps, so any module can import it cheaply.
High-precision keyword tiers, deliberately CONSERVATIVE: a missed fact just
means "no boost" (semantic recall still finds it), whereas a false-high would
wrongly outrank real hits — so the tiers only fire on unambiguous, safety- or
identity-critical content, never on general sentiment.
"""
from __future__ import annotations

import re

# Tier 1 — SAFETY-CRITICAL (0.9): medical / allergy / medication facts where a
# recall miss is genuinely harmful. These must lead a packet whenever relevant.
_SAFETY_RE = re.compile(
    r"\b("
    r"allerg\w*|anaphyla\w*|epi[\s-]?pen|"
    r"asthma\w*|seizure\w*|epilep\w*|diabet\w*|insulin|"
    # `medication` (not the broad `medicine`, which fires on "studied medicine"
    # / "alternative medicine") covers the drug-taking sense.
    r"medication|prescri\w*|dosage|"
    r"warfarin|blood[\s-]?thinner|pacemaker|"
    r"heart\s+condition|emergency\s+contact"
    r")\b",
    re.IGNORECASE,
)

# Tier 2 — DIETARY restriction (0.7): safety-adjacent; getting a meal suggestion
# wrong is a real failure, so these should outrank ordinary food preferences.
_DIETARY_RE = re.compile(
    r"\b("
    r"coeliac|celiac|gluten[\s-]?free|gluten\s+intoleran\w*|lactose\s+intoleran\w*|"
    r"vegetarian|vegan|kosher|halal|dairy[\s-]?free|nut[\s-]?free"
    r")\b",
    re.IGNORECASE,
)

# Tier 3 — VITAL identity (0.6): rarely-changing, high-consequence personal data.
_VITAL_RE = re.compile(
    r"\b("
    r"date\s+of\s+birth|next\s+of\s+kin|blood\s+type|"
    r"passport\s+number|medicare\s+number|nhs\s+number"
    r")\b",
    re.IGNORECASE,
)

_SAFETY_SCORE = 0.9
_DIETARY_SCORE = 0.7
_VITAL_SCORE = 0.6


def score_importance(text: str) -> float:
    """Importance of a memory's content in [0, 1]; 0.0 = ordinary (no boost).

    Returns the highest tier that matches, so a fact touching several categories
    is scored by its most critical one. Order: safety > dietary > vital.
    """
    if not text:
        return 0.0
    if _SAFETY_RE.search(text):
        return _SAFETY_SCORE
    if _DIETARY_RE.search(text):
        return _DIETARY_SCORE
    if _VITAL_RE.search(text):
        return _VITAL_SCORE
    return 0.0
