"""multica_client.py — Thin async client for the Multica board API.

Gracefully no-ops when MULTICA_BASE_URL is unset or Multica is unavailable.
All public methods return empty dicts/lists on error rather than raising,
so callers don't need to handle Multica outages.

API notes (verified against Multica server/cmd/server/router.go):
  - Issues live at /api/issues  (NOT /api/v1/workspaces/{id}/issues)
  - Workspace is passed via X-Workspace-ID header, not the URL path
  - UpdateIssue is PUT, not PATCH
  - Assignee fields are assignee_id (UUID) + assignee_type — never a string name
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
            "X-Workspace-ID": self._workspace,
        }

    async def create_issue(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        assignee_id: str | None = None,
        assignee_type: str | None = None,
    ) -> dict:
        """Create a Multica board issue. Returns the created issue dict."""
        if not self.is_configured():
            logger.debug("Multica not configured — skipping create_issue")
            return {}
        url = f"{self._base}/api/issues"
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
        }
        if assignee_id:
            payload["assignee_id"] = assignee_id
            payload["assignee_type"] = assignee_type or "agent"
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
        url = f"{self._base}/api/issues/{issue_id}"
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
        url = f"{self._base}/api/issues"
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

    async def update_issue(self, issue_id: str, status: str | None = None, **kwargs) -> dict:
        """Update an issue's status and/or other fields (description, title, etc.)."""
        if not self.is_configured():
            return {}
        url = f"{self._base}/api/issues/{issue_id}"
        payload: dict = {}
        if status is not None:
            payload["status"] = status
        payload.update(kwargs)
        if not payload:
            return {}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(url, json=payload, headers=self._headers())
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


# ── Module-level cache for workspace resource lookups ─────────────────────────
_cached_self_imp_agent_id: str | None = None
_cached_self_imp_project_id: str | None = None

_STATUS_MAP = {
    "approved": "in_progress",
    "deployed": "in_review",
    "validated": "done",
    "failed": "cancelled",
}


async def _lookup_evolution_resources(client: MULClient) -> tuple[str | None, str | None]:
    """Return (self_improvement_agent_id, self_improvement_engine_project_id).

    Results are cached in module-level vars after first successful lookup.
    """
    global _cached_self_imp_agent_id, _cached_self_imp_project_id

    if _cached_self_imp_agent_id and _cached_self_imp_project_id:
        return _cached_self_imp_agent_id, _cached_self_imp_project_id

    headers = client._headers()
    params = {"workspace_id": client._workspace}
    base = client._base

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            # Find the Self-Improvement Agent
            agents_resp = await http.get(f"{base}/api/agents", headers=headers, params=params)
            if agents_resp.status_code == 200:
                agents = agents_resp.json()
                if isinstance(agents, list):
                    for a in agents:
                        if a.get("name") == "Self-Improvement Agent":
                            _cached_self_imp_agent_id = a["id"]
                            break

            # Find Self-Improvement Engine project
            projects_resp = await http.get(f"{base}/api/projects", headers=headers, params=params)
            if projects_resp.status_code == 200:
                projects = projects_resp.json()
                items = projects if isinstance(projects, list) else projects.get("projects", [])
                for p in items:
                    if p.get("title") == "Self-Improvement Engine":
                        _cached_self_imp_project_id = p["id"]
                        break
    except Exception as exc:
        logger.warning("Multica: resource lookup failed: %s", exc)

    return _cached_self_imp_agent_id, _cached_self_imp_project_id


async def sync_evolution_proposal_to_multica(
    proposal_id: str,
    title: str,
    description: str,
    evidence: str,
    proposal_type: str,
    label_name: str = "evolution-proposal",
) -> str | None:
    """Create a Multica issue for a new evolution proposal.

    Returns the Multica issue_id, or None if Multica is not configured or the
    call fails.  Called from run_evolution_notice() after writing a new
    proposal row to the DB.
    """
    client = get_multica_client()
    if not client.is_configured():
        logger.debug("Multica not configured — skipping sync_evolution_proposal")
        return None

    agent_id, project_id = await _lookup_evolution_resources(client)

    full_desc = description
    if evidence:
        full_desc = f"{description}\n\n**Evidence:** {evidence}"

    payload: dict[str, Any] = {
        "title": title,
        "description": full_desc,
        "status": "backlog",
        "priority": "medium",
    }
    if agent_id:
        payload["assignee_id"] = agent_id
        payload["assignee_type"] = "agent"
    if project_id:
        payload["project_id"] = project_id

    headers = client._headers()
    params = {"workspace_id": client._workspace}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            # Create the issue
            resp = await http.post(
                f"{client._base}/api/issues",
                json=payload,
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            issue = resp.json()
            issue_id: str = issue.get("id", "")

            # Attach label (defaults to "evolution-proposal"; user-reported issues use "user-feedback")
            labels_resp = await http.get(
                f"{client._base}/api/labels",
                headers=headers,
                params=params,
            )
            if labels_resp.status_code == 200:
                labels = labels_resp.json()
                if isinstance(labels, list):
                    label_id: str | None = None
                    for lbl in labels:
                        if lbl.get("name") == label_name:
                            label_id = lbl["id"]
                            break
                    if label_id is None:
                        # Create label on first use
                        try:
                            create_resp = await http.post(
                                f"{client._base}/api/labels",
                                json={"name": label_name},
                                headers=headers,
                                params=params,
                            )
                            if create_resp.status_code in (200, 201):
                                label_id = create_resp.json().get("id")
                        except Exception:
                            pass
                    if label_id:
                        await http.post(
                            f"{client._base}/api/issues/{issue_id}/labels",
                            json={"label_id": label_id},
                            headers=headers,
                            params=params,
                        )

            logger.info(
                "Multica: synced evolution proposal '%s' → issue %s",
                title[:60], issue_id,
            )
            return issue_id or None

    except Exception as exc:
        logger.warning("Multica sync_evolution_proposal failed: %s", exc)
        return None


async def update_multica_issue_on_proposal_status_change(
    multica_issue_id: str,
    new_status: str,
) -> None:
    """Update a Multica issue when an evolution proposal changes status.

    Mapping:
      'approved'  → Multica 'in_progress'
      'deployed'  → Multica 'in_review'
      'validated' → Multica 'done'
      'failed'    → Multica 'cancelled'
    """
    client = get_multica_client()
    if not client.is_configured():
        return

    multica_status = _STATUS_MAP.get(new_status)
    if not multica_status:
        logger.debug("Multica: no status mapping for '%s' — skipping", new_status)
        return

    await client.update_issue(multica_issue_id, multica_status)
    logger.info(
        "Multica: updated issue %s → %s (proposal status: %s)",
        multica_issue_id, multica_status, new_status,
    )
