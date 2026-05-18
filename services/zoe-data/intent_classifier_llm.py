"""
Tier 0.5 intent classifier — confidence-scored JSON classification.

Runs between Tier 0 (regex) and Tier 1 (Zoe Agent) for short utterances that
the regex cascade missed. Uses the same llama-server endpoint as nlu_extractor
but with a plain JSON completion prompt (no tool forcing) for maximum speed.

Returns an Intent with a confidence score:
  >= 0.75  → caller should execute directly (like a Tier 0 regex match)
  0.5-0.75 → caller should pass as a hint to Tier 1 (reduces OpenClaw escalations)
  < 0.5    → caller should fall through to Tier 1 unchanged
  None     → timeout or parse failure (always falls through)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    from conversation_context import ConversationContext

logger = logging.getLogger(__name__)

CONFIDENCE_EXECUTE_THRESHOLD = 0.75
CONFIDENCE_HINT_THRESHOLD    = 0.50
SHORT_UTTERANCE_MAX_WORDS    = 20
CLASSIFIER_TIMEOUT_S         = 2.0

_LLAMA_BASE = os.environ.get("LLAMA_SERVER_URL", "http://localhost:8080")

_KNOWN_INTENTS = (
    "set_volume music_play music_control music_volume music_stop "
    "smart_home_control timer_set reminder_create calendar_add "
    "greeting calculate weather_query note_create list_add general_question"
)

_CLASSIFY_PROMPT = """\
You are an intent classifier for a home AI assistant.
Respond ONLY with a single line of valid JSON — no explanation, no markdown.

Schema: {{"intent": "<name or null>", "slots": {{}}, "confidence": 0.0-1.0, "unresolved_refs": false}}

Rules:
- intent must be one of the known intents below, or null if genuinely unsure
- confidence: >0.75 means execute directly; 0.5-0.75 means needs agent context; <0.5 means uncertain
- unresolved_refs: true when message uses "it"/"that"/"same" with no resolvable context
- slots: e.g. {{"level": 80}} for volume, {{"query": "weather today"}} for search

Known intents:
{known_intents}

Recent context:
{context_turns}

Message: {text}

JSON:"""


async def classify_intent_with_context(
    text: str,
    *,
    context: "Optional[ConversationContext]" = None,
    timeout: float = CLASSIFIER_TIMEOUT_S,
) -> "Optional[Intent]":
    """
    Classify a short utterance using the local LLM.
    Returns an Intent with confidence, or None on failure/timeout.
    """
    from intent_router import Intent  # lazy to avoid circular at load time

    context_turns = ""
    if context and context.is_fresh() and context.last_text:
        context_turns = (
            f"User previously said: {context.last_text!r}\n"
            f"That was classified as: {context.last_intent}"
        )
    else:
        context_turns = "(no recent context)"

    prompt = _CLASSIFY_PROMPT.format(
        known_intents=_KNOWN_INTENTS,
        context_turns=context_turns,
        text=text,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await asyncio.wait_for(
                client.post(
                    f"{_LLAMA_BASE}/completion",
                    json={
                        "prompt": prompt,
                        "max_tokens": 80,
                        "temperature": 0.0,
                        "stop": ["\n", "```"],
                    },
                ),
                timeout=timeout,
            )
        resp.raise_for_status()
        raw = resp.json().get("content", "").strip()
        # Strip markdown fences if model ignores stop tokens
        raw = raw.lstrip("` \n").rstrip("` \n")
        data = json.loads(raw)
        intent_name = data.get("intent")
        if not intent_name:
            return None
        confidence  = float(data.get("confidence", 0.5))
        slots       = data.get("slots") or {}
        return Intent(intent_name, slots, confidence=confidence)
    except asyncio.TimeoutError:
        logger.debug("intent_classifier_llm: timeout after %.1fs for %r", timeout, text)
        return None
    except Exception as exc:
        logger.debug("intent_classifier_llm: failed for %r: %s", text, exc)
        return None
