"""Detect casual save opportunities and store proactive offers."""

from __future__ import annotations

import json
import logging
import os
import uuid

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)
_MODEL = os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")

_DETECT_PROMPT = """\
Does this user message casually mention something Zoe could save WITHOUT the user explicitly asking?
Return ONLY a JSON array (max 2 items). Each item:
{{
  "action_type": "list_add|reminder_create|calendar_create|note_create",
  "description": "short label",
  "list_type": "shopping|tasks|personal (only for list_add)",
  "when_hint": "optional time hint or null",
  "offer_phrase": "natural question Zoe can ask",
  "pre_filled_slots": {{"item": "...", "list_type": "shopping"}}
}}
If the user already explicitly asked (add to list, remind me, schedule), return [].
If nothing to offer, return [].

User message:
{text}
"""


async def detect(
    user_message: str,
    *,
    user_id: str,
    session_id: str,
) -> list[dict]:
    """Background detection — returns suggestions stored by caller."""
    text = (user_message or "").strip()
    if not text or user_id in ("guest", "") or len(text.split()) < 3:
        return []

    try:
        from intent_router import detect_intent
        if detect_intent(text):
            return []
    except Exception:
        pass

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON arrays."},
            {"role": "user", "content": _DETECT_PROMPT.format(text=text[:800])},
        ],
        "max_tokens": 350,
        "temperature": 0.1,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("latent_intent_detector: LLM failed: %s", exc)
        return []

    try:
        start, end = raw.find("["), raw.rfind("]") + 1
        if start == -1 or end <= start:
            return []
        items = json.loads(raw[start:end])
        if not isinstance(items, list):
            return []
    except (json.JSONDecodeError, ValueError):
        return []

    cleaned: list[dict] = []
    for item in items[:2]:
        if not isinstance(item, dict):
            continue
        action = (item.get("action_type") or "").strip()
        offer = (item.get("offer_phrase") or "").strip()
        if not action or not offer:
            continue
        slots = item.get("pre_filled_slots")
        if not isinstance(slots, dict):
            slots = {}
        desc = (item.get("description") or offer)[:500]
        if action == "list_add":
            slots.setdefault("list_type", item.get("list_type") or "shopping")
            slots.setdefault("item", desc)
        elif action in ("reminder_create", "calendar_create"):
            slots.setdefault("title", desc)
            if item.get("when_hint"):
                slots.setdefault("when_hint", item.get("when_hint"))
        elif action == "note_create":
            slots.setdefault("content", desc)
            slots.setdefault("title", "Note")
        cleaned.append({
            "action_type": action,
            "description": desc,
            "list_type": item.get("list_type"),
            "when_hint": item.get("when_hint"),
            "amount_hint": item.get("amount_hint"),
            "offer_phrase": offer[:300],
            "pre_filled_slots": slots,
        })
    return cleaned


async def detect_and_store(user_message: str, *, user_id: str, session_id: str) -> int:
    from pending_suggestions import store_suggestions

    suggestions = await detect(user_message, user_id=user_id, session_id=session_id)
    if not suggestions:
        return 0
    return await store_suggestions(user_id, session_id, suggestions)
