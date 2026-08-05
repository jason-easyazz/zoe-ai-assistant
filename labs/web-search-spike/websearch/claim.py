"""Claim-backing: "are you sure?" is a DIFFERENT search from "look this up".

This has no oh-my-pi counterpart — its web layer answers open queries. The
distinction matters more for Zoe than for a coding agent, because Zoe's failure
mode is confident-and-wrong in a spoken answer nobody can scroll back through.

Open lookup asks *what is true*: one query, best consensus sources, done.

Claim-backing asks *is this specific statement true*, and the trap is that
searching the claim verbatim is CONFIRMATION BIAS in query form. Search engines
match documents to query terms, so searching "Canberra became capital in 1913"
surfaces pages containing that phrasing — including the ones that got it from
the same wrong source. A claim check must deliberately look for the
contradiction:

1. **Ask the neutral question, not the claim.** Strip the assertion down to its
   entity + attribute ("when did Canberra become the capital") so the engine
   ranks by topic rather than by agreement with our phrasing.
2. **Ask for the contradiction explicitly** as a second query.
3. **Prefer a structured extract** over snippets. A Wikipedia intro states the
   fact; a snippet is a fragment chosen to match the query terms.

The output is deliberately NOT a verdict. This module shapes queries and packs
evidence; the brain decides. A regex that judged truth would be worse than the
hallucination it replaces.
"""

from __future__ import annotations

import re

# Hedges and framing to strip before turning a claim into a neutral query.
# Hedges may be terminated by punctuation ("Are you sure? Canberra is…"), not
# just whitespace — a `[,\s]+` tail silently failed to strip those.
_STRIP_PREFIX = re.compile(
    r"^(?:are you sure(?: that)?|is it true that|i think|you said|didn'?t you say|"
    r"actually|but|so|wait)[?!.,\s]+",
    re.I,
)
_STRIP_TRAILING = re.compile(r"[?!.]+$")

# Assertion verbs whose negation is the contradiction we want to surface.
_NEGATABLE = {
    "is": "is not", "was": "was not", "are": "are not", "were": "were not",
    "has": "has not", "have": "have not", "can": "cannot", "will": "will not",
    "does": "does not", "did": "did not",
}


def neutral_query(claim: str) -> str:
    """Reduce a claim to a topic query, dropping hedges and assertion framing."""
    text = _STRIP_PREFIX.sub("", claim.strip())
    text = _STRIP_TRAILING.sub("", text).strip()
    return text or claim.strip()


def contradiction_query(claim: str) -> str:
    """Build a query aimed at evidence AGAINST the claim.

    Negating the first assertion verb targets pages that state the opposite;
    appending correction vocabulary catches fact-check and errata pages that
    would never rank for the claim as phrased.
    """
    base = neutral_query(claim)
    words = base.split()
    for i, word in enumerate(words):
        key = word.lower().strip(",")
        if key in _NEGATABLE:
            negated = " ".join(words[:i] + [_NEGATABLE[key]] + words[i + 1 :])
            return f"{negated}"
    return f"{base} incorrect OR myth OR debunked"


def build_check_queries(claim: str) -> list[str]:
    """The query set for a claim check: neutral topic first, contradiction second.

    Ordered deliberately — the neutral query is the one that must succeed
    within the voice latency budget; the contradiction query is the enrichment
    that makes the check honest rather than confirmatory.
    """
    neutral = neutral_query(claim)
    contra = contradiction_query(claim)
    return [neutral] if contra == neutral else [neutral, contra]


def is_challenge(utterance: str) -> bool:
    """True when the user is challenging a previous answer rather than asking anew.

    Intentionally narrow: a false positive turns a fresh question into a
    (slower, differently-shaped) claim check, so this only fires on explicit
    challenge phrasing.
    """
    low = utterance.strip().lower()
    # No trailing \b: several of these end in punctuation ("really?"), and \b
    # after `?` never matches at end-of-string — it silently dropped them.
    return bool(
        re.search(
            r"\b(are you sure|you sure|is that right|is that true|that'?s not right|"
            r"that'?s wrong|really\?|prove it|says who|check that|double.?check)",
            low,
        )
    )
