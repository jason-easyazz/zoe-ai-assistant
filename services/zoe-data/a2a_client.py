"""A2A v1.0 client — Zoe calling peer agents.

Provides discover/submit_task/poll_task/call_skill operations.
All methods degrade gracefully: if the peer is unreachable, they return an
error dict rather than raising so callers can handle offline agents cleanly.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = float(os.environ.get("ZOE_A2A_CLIENT_TIMEOUT_S", "30"))


class A2AClient:
    """Async A2A v1.0 client."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def discover(self, base_url: str) -> dict:
        """Fetch the remote agent's A2A v1.0 card via /.well-known/agent.json."""
        url = base_url.rstrip("/") + "/.well-known/agent.json"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("A2A discover failed for %s: %s", base_url, exc)
            return {"error": str(exc), "base_url": base_url}

    # ── Task submission ────────────────────────────────────────────────────────

    async def submit_task(
        self,
        base_url: str,
        task: str,
        caller: str = "zoe",
        token: str = "",
        session_id: str | None = None,
        stream: bool = False,
    ) -> dict:
        """Submit a task to a peer A2A agent.

        Returns the peer's response dict (contains task_id for non-streaming).
        """
        url = base_url.rstrip("/") + "/api/agent/tasks"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload: dict[str, Any] = {"task": task, "caller": caller, "stream": stream}
        if session_id:
            payload["session_id"] = session_id

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "A2A submit_task HTTP %s for %s: %s",
                exc.response.status_code, base_url, exc.response.text[:200],
            )
            return {"error": f"HTTP {exc.response.status_code}", "detail": exc.response.text[:200]}
        except Exception as exc:
            logger.warning("A2A submit_task failed for %s: %s", base_url, exc)
            return {"error": str(exc)}

    # ── Polling ───────────────────────────────────────────────────────────────

    async def poll_task(self, base_url: str, task_id: str, token: str = "") -> dict:
        """Poll the status of a previously submitted A2A task."""
        url = base_url.rstrip("/") + f"/api/agent/tasks/{task_id}"
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("A2A poll_task failed for %s/%s: %s", base_url, task_id, exc)
            return {"error": str(exc), "task_id": task_id}

    # ── Skill call ────────────────────────────────────────────────────────────

    async def call_skill(
        self,
        agent_url: str,
        skill_id: str,
        input_text: str,
        token: str = "",
    ) -> str:
        """Call a specific skill on a peer agent and return the result text."""
        result = await self.submit_task(
            base_url=agent_url,
            task=input_text,
            caller="zoe",
            token=token,
        )
        if "error" in result:
            return f"[A2A error calling {skill_id} on {agent_url}: {result['error']}]"
        return result.get("result") or f"[task queued: {result.get('task_id', '?')}]"


# Module-level singleton — lazy so import doesn't hit the network at startup
_client: A2AClient | None = None


def get_a2a_client() -> A2AClient:
    global _client
    if _client is None:
        _client = A2AClient()
    return _client
