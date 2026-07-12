"""LLM-based person fact extraction — complements regex person_extractor."""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)
_MODEL = os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")

_TRUTHY = {"1", "true", "yes", "on"}


def prefilter_enabled() -> bool:
    """Dark flag: gate the per-turn LLM pass behind a cheap person-mention
    prefilter. Default OFF — byte-for-byte no-op until enabled."""
    return os.environ.get("ZOE_PERSON_LLM_PREFILTER", "").strip().lower() in _TRUTHY


def confidence_gate_enabled() -> bool:
    """Dark flag: only apply LLM-extracted facts whose self-reported confidence
    clears a threshold. Default OFF — byte-for-byte no-op (every item applied, as
    today). Research-backed precision filter (verbalized-confidence gating; the
    '<threshold discard' coarse first-pass filter) — see
    docs/adr/ADR-contacts-production-hardening.md P2. Replay-gated at enable time
    (this pass runs on the live voice/chat write-path)."""
    return os.environ.get("ZOE_PERSON_LLM_CONFIDENCE_GATE", "").strip().lower() in _TRUTHY


def _confidence_min() -> float:
    """Discard threshold for the confidence gate (default 0.4, matching the
    researched coarse first-pass filter). Env-tunable, clamped to [0, 1]."""
    try:
        return max(0.0, min(1.0, float(os.environ.get("ZOE_PERSON_LLM_CONFIDENCE_MIN", "0.4"))))
    except (TypeError, ValueError):
        return 0.4


def _keep_item(item: dict, *, gated: bool, min_conf: float) -> bool:
    """When the gate is off, keep every item (today's behaviour). When on, drop
    items whose self-reported ``confidence`` is below ``min_conf`` (missing /
    unparseable confidence counts as 0.0 → dropped)."""
    if not gated:
        return True
    try:
        conf = float(item.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    # Fail closed on malformed / wrong-scale confidence: the prompt contract is
    # 0.0-1.0, so a value like 5 or 80 is not a real high-confidence signal.
    if not (0.0 <= conf <= 1.0):
        return False
    return conf >= min_conf


# ── Person-mention prefilter ────────────────────────────────────────────────
# This LLM pass fires on EVERY non-guest turn (chat + voice, background) and
# costs ~0.6–1.3 s of Gemma time per call on the SAME llama-server the live
# brain uses — pure GPU contention for turns like "set a timer". Measured
# 2026-07-06 (scripts/perf/measure_person_extractor.py, 30-turn dry set):
# median 593 ms/call; this prefilter skipped 50% of turns with ZERO false
# negatives (every turn where the LLM actually extracted a fact passed).
# Names at sentence start ("Tom got promoted...") must pass — hence a stoplist
# of sentence-function/command capitals rather than a position rule.
_REL_WORDS = re.compile(
    r"\b(wife|husband|partner|mum|mom|dad|father|mother|son|daughter|brother|sister|"
    r"friend|mate|boss|colleague|neighbou?r|cousin|aunt|uncle|grandma|grandpa|kids?|children)\b",
    re.I,
)
_CAP_STOP = frozenset(
    "The A An I Is Are Was Were What When Where Who Whom How Why Do Does Did Can Could Would "
    "Should Will Please Book Set Turn Add Play Remind Make My No Yes Actually Thanks Thank "
    "Remember Cancel Show Open Stop Start Put Get Tell Give Find Check Call It This That "
    "There Here If Then And Or But So Not Just Ok Okay Hey Hi Also Now Today Tomorrow "
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April "
    "May June July August September October November December Zoe".split()
)
_CAP_TOKEN = re.compile(r"\b[A-Z][a-z]{2,}\b")


def mentions_person(text: str) -> bool:
    """Cheap regex verdict: does this turn plausibly mention a person?"""
    if _REL_WORDS.search(text):
        return True
    return any(tok not in _CAP_STOP for tok in _CAP_TOKEN.findall(text))

_EXTRACTION_PROMPT = """\
Extract person-related facts from the text. Return ONLY a JSON array.
Each item: {{"name": "First Last", "fact_type": "preference|birthday|work|meeting|gift_idea|gift_given|bucket_list", "value": "concise fact"}}
Only include facts explicitly stated about named people. If none, return [].
For RELATIONSHIP facts (wife, husband, partner, boyfriend, girlfriend, son,
daughter, kids, girls, boys, friend, brother, sister, mum, dad, etc.) the value
MUST say whose relative they are — e.g. "wife of Lindsay Cannon" or "user's
friend" — NEVER a bare role like "wife" or "girl" (a bare role is ambiguous and
will be discarded).

Text:
{text}
"""

# Confidence-gated variant (P2): each item carries a self-reported confidence the
# gate thresholds on. Kept separate so the un-gated path is byte-for-byte today's.
_EXTRACTION_PROMPT_CONF = """\
Extract person-related facts from the text. Return ONLY a JSON array.
Each item: {{"name": "First Last", "fact_type": "preference|birthday|work|meeting|gift_idea|gift_given|bucket_list", "value": "concise fact", "confidence": 0.0-1.0}}
"confidence" = how sure you are this fact is EXPLICITLY stated about a named person
(1.0 = stated verbatim, 0.5 = implied, below 0.4 = a guess). Only include facts
explicitly stated about named people. If none, return [].
For RELATIONSHIP facts (wife, husband, partner, boyfriend, girlfriend, son,
daughter, kids, girls, boys, friend, brother, sister, mum, dad, etc.) the value
MUST say whose relative they are — e.g. "wife of Lindsay Cannon" or "user's
friend" — NEVER a bare role like "wife" or "girl" (a bare role is ambiguous and
will be discarded).

Text:
{text}
"""


# ── Bare-role backstop ────────────────────────────────────────────────────────
# The prompt asks the LLM to qualify relationship values ("wife of Lindsay
# Cannon", "user's friend"), but a 4B model won't always comply. An UNANCHORED
# role is poison: "Emily: wife" stored from "Emily is the wife" (describing a
# FRIEND's family) ranked #1 for "Who is my wife?" (live 2026-07-12). Better to
# drop the fact than store a wrong-attachment relationship — user-relative
# shapes ("my friend X") are still captured by the deterministic regex paths.
_ROLE_WORDS = (
    "wife|husband|partner|girlfriend|boyfriend|fianc[eé]e?|spouse"
    "|son|daughter|kid|kids|child|children|girl|girls|boy|boys|baby"
    "|friend|mate|buddy|bestie"
    "|brother|sister|mum|mom|mother|dad|father|grandma|grandmother|grandpa"
    "|grandfather|aunt|uncle|niece|nephew|cousin|colleague|coworker|boss|neighbou?r"
)
# Bare = optional article/adjectives + a role word as the HEAD NOUN, with no
# anchor — a trailing qualifier doesn't rescue it ("male friend from work" is
# still ambiguous about WHOSE friend). Anchored forms contain "of <name>", a
# possessive ("Lindsay's", "user's"), or a pronoun possessive ("his", "her",
# "their") — those pass through. Head-noun (not role-anywhere) so trait facts
# like "great with kids" — where the role sits inside a prepositional phrase —
# are not flagged as relationship claims.
_BARE_ROLE_RE = re.compile(
    rf"^(?:(?:a|an|the)\s+)?(?:[a-z]+\s+){{0,2}}(?:{_ROLE_WORDS})s?\b", re.IGNORECASE
)
_ANCHOR_RE = re.compile(r"\bof\s+\S|['’]s\b|\b(?:his|her|their|my|user)\b", re.IGNORECASE)
# Words that mark the role as part of a descriptive phrase, not the head noun.
_NON_HEAD_LEADIN_RE = re.compile(
    r"^(?:\w+\s+)*?(?:with|for|to|about|around|at|on|from|like|loves?|has|have|had"
    r"|is|was|works?|great|good)\s+(?:\w+\s+)*?(?:%s)s?\b" % _ROLE_WORDS,
    re.IGNORECASE,
)


def _is_unanchored_role(value: str) -> bool:
    """True when the fact value is a relationship-role HEAD with no 'whose' anchor."""
    v = (value or "").strip().rstrip(".")
    if _ANCHOR_RE.search(v):
        return False
    if not _BARE_ROLE_RE.match(v):
        return False
    # role reached through a preposition/verb ("great with kids") → trait, not
    # a relationship claim; only a role in head-noun position is flagged.
    if _NON_HEAD_LEADIN_RE.match(v):
        return False
    return True


async def process_text_llm(
    text: str,
    *,
    user_id: str,
    source: str = "conversation",
    session_id: str | None = None,
    db=None,
) -> int:
    """Extract person facts via local LLM. Silently no-ops on error."""
    text = (text or "").strip()
    if not text or user_id in ("guest", "") or len(text.split()) < 4:
        return 0
    if prefilter_enabled() and not mentions_person(text):
        # No plausible person mention → skip the ~0.6–1.3 s Gemma call
        # entirely (flag-gated; see prefilter rationale above).
        return 0

    gated = confidence_gate_enabled()
    min_conf = _confidence_min() if gated else 0.0
    prompt = _EXTRACTION_PROMPT_CONF if gated else _EXTRACTION_PROMPT
    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON arrays."},
            {"role": "user", "content": prompt.format(text=text[:1200])},
        ],
        "max_tokens": 300,
        "temperature": 0.1,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("person_extractor_llm: LLM call failed: %s", exc)
        return 0

    try:
        start, end = raw.find("["), raw.rfind("]") + 1
        if start == -1 or end <= start:
            return 0
        items = json.loads(raw[start:end])
        if not isinstance(items, list):
            return 0
    except (json.JSONDecodeError, ValueError):
        return 0

    from person_extractor import apply_person_fact

    written = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        fact_type = (item.get("fact_type") or "preference").strip()
        value = (item.get("value") or "").strip()
        if not name or not value:
            continue
        if not _keep_item(item, gated=gated, min_conf=min_conf):
            logger.debug("person_extractor_llm: dropped low-confidence %r/%r", name, fact_type)
            continue
        if _is_unanchored_role(value):
            # Rescue before dropping: if the TURN itself supports a user anchor
            # ("my male friend" → value "male friend"), qualify deterministically
            # instead of losing a valid fact. The validator's synonym-aware check
            # is the arbiter: supported → prefix "user's"; unsupported → drop.
            try:
                from memory_quality import user_relationship_claim_unsupported
                if not user_relationship_claim_unsupported(f"user's {value}", text):
                    value = f"user's {value}"
                else:
                    logger.info(
                        "person_extractor_llm: dropped unanchored relationship %r for %r — "
                        "value must say whose relative (e.g. \"wife of X\", \"user's friend\")",
                        value, name,
                    )
                    continue
            except Exception:
                logger.info(
                    "person_extractor_llm: dropped unanchored relationship %r for %r",
                    value, name,
                )
                continue
        # Told to qualify, the 4B model GUESSES "user" when the text doesn't say
        # ("Emily is the wife" → "wife of user"). Only accept a user anchor the
        # source turn supports ("my <role>"); otherwise drop — a wrong-attachment
        # relationship is worse than no fact.
        try:
            from memory_quality import user_relationship_claim_unsupported
            if user_relationship_claim_unsupported(f"{name}: {value}", text):
                logger.info(
                    "person_extractor_llm: dropped unsupported user-anchored "
                    "relationship %r for %r (source never says \"my …\")", value, name,
                )
                continue
        except Exception:
            pass  # validator unavailable → keep legacy behavior
        ok = await apply_person_fact(
            name,
            fact_type,
            value,
            user_id=user_id,
            source=source,
            session_id=session_id,
            db=db,
        )
        if ok:
            written += 1
    return written
