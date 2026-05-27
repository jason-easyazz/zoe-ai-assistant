"""
Slow-loop Hermes-powered proactive checks.

Subclass this to implement environment-aware triggers (e.g. low stock,
calendar prep, weather alerts).  The engine's slow loop calls check()
on all registered triggers every SLOW_LOOP_INTERVAL seconds.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_HERMES_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642/v1/chat/completions")
_HERMES_API_KEY = os.environ.get("HERMES_API_KEY") or os.environ.get("API_SERVER_KEY") or ""
_TIMEOUT = 30.0


def _hermes_headers(*, session_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if _HERMES_API_KEY:
        headers["Authorization"] = f"Bearer {_HERMES_API_KEY}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    return headers


class OpenClawTrigger(ProactiveTrigger):
    """
    Backwards-compatible base for triggers that delegate check logic to Hermes.

    Subclasses must set:
        trigger_type  — string identifier
        _prompt       — the Hermes prompt to evaluate
        _user_id      — which user to notify (or override get_user_ids())
    """

    trigger_type: str = "openclaw"
    _prompt: str = ""
    _user_id: str = "family-admin"

    def get_user_ids(self) -> list[str]:
        return [self._user_id]

    def _build_prompt(self, context: dict[str, Any]) -> str:
        """Override to customise the prompt per-check."""
        return self._prompt

    async def _run_hermes(self, prompt: str) -> dict[str, Any] | None:
        """
        Post a task to Hermes and return the parsed JSON response dict,
        or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    _HERMES_URL,
                    headers=_hermes_headers(session_id=f"proactive-trigger:{self.trigger_type}"),
                    json={
                        "model": os.environ.get("HERMES_MODEL", "hermes"),
                        "messages": [
                            {
                                "role": "system",
                                "content": "Return compact JSON for Zoe proactive trigger evaluation.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0,
                        "max_tokens": 600,
                        "stream": False,
                    },
                )
                r.raise_for_status()
                data = r.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                # Try to parse embedded JSON.
                try:
                    return json.loads(content)
                except Exception:
                    return {"raw": content}
        except Exception as exc:
            log.warning("OpenClawTrigger._run_hermes failed: %s", exc)
            return None

    async def check(self, db) -> list[TriggerResult]:
        results: list[TriggerResult] = []
        context: dict[str, Any] = {}
        prompt = self._build_prompt(context)
        if not prompt:
            return results
        data = await self._run_hermes(prompt)
        if data is None:
            return results
        # Subclasses inspect `data` and decide whether to fire.
        return await self.evaluate(data, context)

    async def evaluate(
        self, data: dict[str, Any], context: dict[str, Any]
    ) -> list[TriggerResult]:
        """
        Override in subclasses.  Return TriggerResult objects to fire,
        or an empty list to stay silent.
        """
        return []
