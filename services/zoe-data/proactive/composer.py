"""
Message composer for the proactive engine.

Calls llama-server (same endpoint as pi_agent) to generate a concise,
friendly push-notification message from a trigger context dict.
Falls back to the raw trigger message on any failure.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

_LLM_BASE = os.environ.get("LLAMA_SERVER_URL", "http://localhost:8080")
_TIMEOUT = 12.0
_MAX_TOKENS = 80


_SYSTEM = (
    "You are Zoe, a friendly assistant. "
    "Write a single short push-notification message (≤25 words) for the user. "
    "Be warm, clear, and specific. No markdown, no quotes."
)


async def compose_message(trigger_type: str, context: dict, fallback: str) -> str:
    """
    Given a trigger_type and a context dict, ask the LLM for a short message.
    Returns fallback on failure or if the LLM is unavailable.
    """
    prompt_content = f"Trigger: {trigger_type}\nContext: {context}\nWrite the push notification message."
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt_content},
        ],
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.4,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{_LLM_BASE}/v1/chat/completions", json=payload)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]["content"].strip()
            return msg or fallback
    except Exception as exc:
        log.warning("composer.compose_message failed (%s); using fallback", exc)
        return fallback
