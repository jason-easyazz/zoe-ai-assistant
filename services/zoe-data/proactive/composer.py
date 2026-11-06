"""
Message composer for the proactive engine.

Calls llama-server (same endpoint as zoe_agent) to generate a concise,
friendly push-notification message from a trigger context dict.
Falls back to the raw trigger message on any failure.
"""
from __future__ import annotations

import logging

import httpx

from gemma_endpoint import gemma_base

log = logging.getLogger(__name__)

_TIMEOUT = 12.0
_MAX_TOKENS = 80


_SYSTEM = (
    "You are Zoe, a friendly assistant. "
    "Write a single short push-notification message (≤25 words) for the user. "
    "Be warm, clear, and specific. No markdown, no quotes. "
    "Only state when something was said if the context gives an exact 'mentioned' wording; "
    "otherwise do not say when (never 'this morning', 'yesterday', 'earlier today' on your own)."
)


async def compose_message(trigger_type: str, context: dict, fallback: str, *, in_turn: bool = False) -> str:
    """
    Given a trigger_type and a context dict, ask the LLM for a short message.
    Returns fallback on failure or if the LLM is unavailable (including a daytime refusal by ``brain_scheduler`` in enforce
    mode). ``in_turn`` marks a call made while answering a person's own request; the proactive engine leaves it False.
    """
    try:
        from time_utils import clock_stamp

        now = f"Now (household local time): {clock_stamp()}\n"
    except Exception:  # noqa: BLE001 - the clock is garnish; the message is not
        now = ""
    prompt_content = f"{now}Trigger: {trigger_type}\nContext: {context}\nWrite the push notification message."
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
        import brain_scheduler

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            with brain_scheduler.declare("turn:compose_message" if in_turn else "proactive.composer.compose_message"):
                r = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]["content"].strip()
            return msg or fallback
    except Exception as exc:
        log.warning("composer.compose_message failed (%s); using fallback", exc)
        return fallback
