"""
Tier 2 trigger: slow-loop OpenClaw-powered checks.

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

_OPENCLAW_URL = os.environ.get("OPENCLAW_API_URL", "http://localhost:9999")
_TIMEOUT = 30.0


class OpenClawTrigger(ProactiveTrigger):
    """
    Base for triggers that delegate their check logic to an OpenClaw agent call.

    Subclasses must set:
        trigger_type  — string identifier
        _prompt       — the OpenClaw prompt to evaluate
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

    async def _run_openclaw(self, prompt: str) -> dict[str, Any] | None:
        """
        Post a task to OpenClaw and return the parsed JSON response dict,
        or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_OPENCLAW_URL}/v1/task",
                    json={"prompt": prompt, "stream": False},
                )
                r.raise_for_status()
                data = r.json()
                content = data.get("result") or data.get("content") or ""
                # Try to parse embedded JSON.
                try:
                    return json.loads(content)
                except Exception:
                    return {"raw": content}
        except Exception as exc:
            log.warning("OpenClawTrigger._run_openclaw failed: %s", exc)
            return None

    async def check(self, db) -> list[TriggerResult]:
        results: list[TriggerResult] = []
        context: dict[str, Any] = {}
        prompt = self._build_prompt(context)
        if not prompt:
            return results
        data = await self._run_openclaw(prompt)
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
