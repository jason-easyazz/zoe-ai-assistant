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

Text:
{text}
"""


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

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON arrays."},
            {"role": "user", "content": _EXTRACTION_PROMPT.format(text=text[:1200])},
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
