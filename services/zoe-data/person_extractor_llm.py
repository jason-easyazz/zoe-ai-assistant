"""LLM-based person fact extraction — complements regex person_extractor."""

from __future__ import annotations

import json
import logging
import os

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)
_MODEL = os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")

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
