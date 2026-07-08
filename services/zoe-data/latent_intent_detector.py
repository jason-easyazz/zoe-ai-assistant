"""Detect casual save opportunities and store proactive offers."""

from __future__ import annotations

import json
import logging
import os

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)
_MODEL = os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")

# Base action set always offered. `person_create` is appended only when the
# contacts-from-known-people bridge is enabled (see _build_prompt), so the whole
# feature is byte-for-byte dark when the flag is off.
_ACTION_TYPES_BASE = "list_add|reminder_create|calendar_create|note_create"

_DETECT_PROMPT = """\
Does this user message casually mention something Zoe could save WITHOUT the user explicitly asking?
Return ONLY a JSON array (max 2 items). Each item:
{{
  "action_type": "{action_types}",
  "description": "short label",
  "list_type": "shopping|tasks|personal (only for list_add)",
  "when_hint": "optional time hint or null",
  "offer_phrase": "natural question Zoe can ask",
  "pre_filled_slots": {{"item": "...", "list_type": "shopping"}}
}}
{person_hint}If the user already explicitly asked (add to list, remind me, schedule), return [].
If nothing to offer, return [].

User message:
{text}
"""

# Injected verbatim into the prompt (single braces — not re-parsed by .format()).
_PERSON_HINT = (
    'For "person_create": the user mentions a specific named person (a friend, '
    "family member, or colleague) Zoe could add as a contact. Use "
    'pre_filled_slots {"name": "<their name>", "relationship": "<relationship or empty>"}. '
    "Only a real proper name — never a pronoun (he/she/they) or generic word.\n"
)


def _build_prompt(text: str, person_enabled: bool) -> str:
    action_types = _ACTION_TYPES_BASE
    person_hint = ""
    if person_enabled:
        action_types = f"{_ACTION_TYPES_BASE}|person_create"
        person_hint = _PERSON_HINT
    return _DETECT_PROMPT.format(
        action_types=action_types, person_hint=person_hint, text=text
    )


def _person_enabled() -> bool:
    """Reuse the Phase-1 flag so the emitter and the executor share one switch."""
    try:
        from pending_suggestions import person_suggestions_enabled
        return person_suggestions_enabled()
    except Exception:
        return False


async def _complete(prompt: str) -> str | None:
    """Run the detector LLM. Returns the raw string content, or None on failure.

    Isolated so tests can monkeypatch the LLM without touching the deterministic
    parse/filter layer below.
    """
    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON arrays."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 350,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("latent_intent_detector: LLM failed: %s", exc)
        return None


async def _already_a_contact(name: str, user_id: str) -> bool:
    """Best-effort: don't propose someone who is already a contact.

    Fails open (returns False) if the DB is unavailable — the executor dedups
    again on accept, so a missed check only means a redundant offer, never a
    duplicate row.
    """
    if not name or user_id in ("guest", ""):
        return False
    try:
        from db_pool import get_db_ctx
        async with get_db_ctx() as db:
            row = await db.fetchrow(
                "SELECT 1 FROM people WHERE user_id=$1 AND lower(name)=lower($2)"
                " AND deleted=0 LIMIT 1",
                user_id,
                name,
            )
            return row is not None
    except Exception:
        return False


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

    person_enabled = _person_enabled()
    raw = await _complete(_build_prompt(text[:800], person_enabled))
    if raw is None:
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

        if action == "person_create":
            # Flag-gated dark: never emit unless the contacts bridge is on.
            if not person_enabled:
                continue
            slots = item.get("pre_filled_slots")
            if not isinstance(slots, dict):
                slots = {}
            name = (slots.get("name") or item.get("name") or "").strip()
            # Don't trust the LLM: drop bad/empty names via the shared precision
            # guard (rejects pronouns / sentence-openers). Import lazily.
            from person_extractor import _looks_like_person_name
            if not name or not _looks_like_person_name(name):
                continue
            if await _already_a_contact(name, user_id):
                continue
            relationship = (
                slots.get("relationship") or item.get("relationship") or ""
            ).strip()
            new_slots = {"name": name}
            if relationship:
                new_slots["relationship"] = relationship
            offer = (item.get("offer_phrase") or "").strip() or f"Add {name} to your contacts?"
            cleaned.append({
                "action_type": "person_create",
                "description": f"Add {name} to contacts",
                "list_type": None,
                "when_hint": None,
                "amount_hint": None,
                "offer_phrase": offer[:300],
                "pre_filled_slots": new_slots,
            })
            continue

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
