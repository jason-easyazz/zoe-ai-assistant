"""multica_client.py — Thin async client for the Multica board API.

Gracefully no-ops when MULTICA_BASE_URL is unset or Multica is unavailable.
All public methods return empty dicts/lists on error rather than raising,
so callers don't need to handle Multica outages.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MULTICA_BASE_URL = os.environ.get("MULTICA_BASE_URL", "")
_MULTICA_API_TOKEN = os.environ.get("MULTICA_API_TOKEN", "")
_MULTICA_WORKSPACE_ID = os.environ.get("MULTICA_WORKSPACE_ID", "")
_TIMEOUT = 10.0


class MULClient:
    """Multica board client — wraps the Multica REST API."""

    def __init__(self) -> None:
        self._base = (_MULTICA_BASE_URL or "").rstrip("/")
        self._token = _MULTICA_API_TOKEN or ""
        self._workspace = _MULTICA_WORKSPACE_ID or ""

    def is_configured(self) -> bool:
        """Return True only if all required env vars are set."""
        return bool(self._base and self._token and self._workspace)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def create_issue(
        self,
        title: str,
        description: str = "",
        assignee_id: str | None = None,
        priority: str = "medium",
    ) -> dict:
        """Create a Multica board issue. Returns the created issue dict."""
        if not self.is_configured():
            logger.debug("Multica not configured — skipping create_issue")
            return {}
        url = f"{self._base}/api/v1/workspaces/{self._workspace}/issues"
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
        }
        if assignee_id:
            payload["assigneeId"] = assignee_id
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Multica create_issue failed: %s", exc)
            return {"error": str(exc)}

    async def get_issue(self, issue_id: str) -> dict:
        """Fetch a single issue by ID."""
        if not self.is_configured():
            return {}
        url = f"{self._base}/api/v1/workspaces/{self._workspace}/issues/{issue_id}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Multica get_issue(%s) failed: %s", issue_id, exc)
            return {}

    async def list_issues(self, status: str | None = None) -> list[dict]:
        """List issues in the workspace, optionally filtered by status."""
        if not self.is_configured():
            return []
        url = f"{self._base}/api/v1/workspaces/{self._workspace}/issues"
        params = {}
        if status:
            params["status"] = status
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("issues", [])
        except Exception as exc:
            logger.warning("Multica list_issues failed: %s", exc)
            return []

    async def update_issue(self, issue_id: str, status: str) -> dict:
        """Update the status of an issue."""
        if not self.is_configured():
            return {}
        url = f"{self._base}/api/v1/workspaces/{self._workspace}/issues/{issue_id}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.patch(
                    url, json={"status": status}, headers=self._headers()
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Multica update_issue(%s) failed: %s", issue_id, exc)
            return {}


# Module-level singleton
_client: MULClient | None = None


def get_multica_client() -> MULClient:
    global _client
    if _client is None:
        _client = MULClient()
    return _client
