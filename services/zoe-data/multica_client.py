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
from typing import Any, Mapping

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_DEFAULT_HERMES_MULTICA_AGENT_ID = "019ae0a7-62f1-47fe-9d46-75fd0ae5d570"
_SELF_IMPROVEMENT_MULTICA_AGENT_ID = "ee8596da-3a08-4e10-98e8-d058e57ea3ff"
_cached_engineering_agent_id: str | None = None
_DEFAULT_LABEL_COLOR = "#64748b"


def get_engineering_multica_agent_id() -> str:
    """Return the Multica agent UUID used for board engineering dispatch (Hermes)."""
    global _cached_engineering_agent_id
    env_id = os.environ.get("HERMES_MULTICA_AGENT_ID", "").strip()
    if env_id:
        return env_id
    if _cached_engineering_agent_id:
        return _cached_engineering_agent_id
    try:
        from zoe_agent_registry import load_agent_registry

        registry = load_agent_registry()
        hermes = (registry.get("agents") or {}).get("hermes") or {}
        reg_id = str(hermes.get("multica_agent_id") or "").strip()
        if reg_id:
            _cached_engineering_agent_id = reg_id
            return reg_id
    except Exception as exc:
        logger.debug("get_engineering_multica_agent_id: registry lookup failed: %s", exc)
    return _DEFAULT_HERMES_MULTICA_AGENT_ID


def get_self_improvement_multica_agent_id() -> str:
    """Legacy Self-Improvement Agent UUID (reassign scripts filter from this)."""
    return _SELF_IMPROVEMENT_MULTICA_AGENT_ID


def _multica_env_key() -> tuple[str, str, str]:
    """Return a comparable snapshot of the current Multica env vars."""
    return (
        os.environ.get("MULTICA_BASE_URL", "").rstrip("/"),
        os.environ.get("MULTICA_API_TOKEN", ""),
        os.environ.get("MULTICA_WORKSPACE_ID", ""),
    )


class MULClient:
    """Multica board client — wraps the Multica REST API."""

    def __init__(self) -> None:
        # Read env at instantiation time so callers that import this module
        # before EnvironmentFile/.env loading still get the live Multica config.
        self._base, self._token, self._workspace = _multica_env_key()

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
        status: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Create a Multica board issue. Returns the created issue dict."""
        if not self.is_configured():
            logger.debug("Multica not configured — skipping create_issue")
            return {}
        if metadata:
            from multica_ticket_contract import normalize_ticket_metadata, parse_ticket_block, write_ticket_block

            ticket_metadata = parse_ticket_block(description)
            ticket_metadata.update(metadata)
            ticket_metadata.pop("updated_at", None)
            description = write_ticket_block(description, normalize_ticket_metadata(ticket_metadata))
        url = f"{self._base}/api/issues"
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
        }
        if status:
            payload["status"] = status
        if project_id:
            payload["project_id"] = project_id
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

    async def list_issues(
        self,
        status: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict]:
        """List issues in the workspace, optionally filtered by status."""
        if not self.is_configured():
            return []
        url = f"{self._base}/api/issues"
        params = {}
        if status:
            params["status"] = status
        if limit is not None:
            params["limit"] = max(1, int(limit))
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

    async def list_labels(self) -> list[dict]:
        """List workspace labels."""
        if not self.is_configured():
            return []
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self._base}/api/labels", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("labels", [])
        except Exception as exc:
            logger.warning("Multica list_labels failed: %s", exc)
            return []

    async def ensure_label(self, name: str, *, existing: list[dict] | None = None) -> dict:
        """Return a label by name, creating it when possible.

        Pass ``existing`` from a prior ``list_labels`` call when ensuring many
        labels in a loop.
        """
        wanted = name.strip()
        if not wanted or not self.is_configured():
            return {}
        for label in (existing if existing is not None else await self.list_labels()):
            if str(label.get("name") or "").lower() == wanted.lower():
                return label
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/api/labels",
                    json={"name": wanted, "color": _DEFAULT_LABEL_COLOR},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Multica ensure_label(%s) failed: %s", wanted, exc)
            return {}

    async def attach_label(self, issue_id: str, label_name: str) -> dict:
        """Attach a label to an issue by name."""
        label = await self.ensure_label(label_name)
        label_id = label.get("id") if isinstance(label, dict) else None
        if not label_id or not self.is_configured():
            return {}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/api/issues/{issue_id}/labels",
                    json={"label_id": label_id},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json() if resp.content else {"ok": True}
        except Exception as exc:
            logger.warning("Multica attach_label(%s, %s) failed: %s", issue_id, label_name, exc)
            return {}

    async def list_projects(self) -> list[dict]:
        """List workspace projects."""
        if not self.is_configured():
            return []
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self._base}/api/projects", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("projects", [])
        except Exception as exc:
            logger.warning("Multica list_projects failed: %s", exc)
            return []

    async def ensure_project(self, title: str) -> dict:
        """Return a project by title, creating it when possible."""
        wanted = title.strip()
        if not wanted or not self.is_configured():
            return {}
        for project in await self.list_projects():
            if str(project.get("title") or project.get("name") or "").lower() == wanted.lower():
                return project
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base}/api/projects",
                    json={"title": wanted},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Multica ensure_project(%s) failed: %s", wanted, exc)
            return {}

    async def safe_patch_description(self, issue_id: str, metadata: dict[str, Any]) -> dict:
        """Patch only the fenced Zoe ticket block in an issue description."""
        from multica_ticket_contract import write_ticket_block

        issue = await self.get_issue(issue_id)
        if not issue.get("id"):
            return {}
        original = issue.get("description") or ""
        return await self.update_issue(issue_id, description=write_ticket_block(original, metadata))

    async def append_issue_note(self, issue_id: str, note: str) -> dict:
        """Append a visible progress note while preserving existing description."""
        issue = await self.get_issue(issue_id)
        if not issue.get("id"):
            return {}
        original = (issue.get("description") or "").rstrip()
        updated = f"{original}\n\nZoe note: {note.strip()}" if original else f"Zoe note: {note.strip()}"
        return await self.update_issue(issue_id, description=updated)

    async def create_child_issue(self, parent: dict, template: dict[str, Any]) -> dict:
        """Create a child issue linked to a parent via the Zoe ticket block.

        Parent ``child_issue_ids`` updates are caller-owned so multi-child split
        commands can write one consistent final parent state.
        """
        from multica_ticket_contract import describe_ticket, parse_ticket_block

        parent_id = str(parent.get("id") or "")
        parent_meta = parse_ticket_block(parent.get("description") or "")
        title = str(template.get("title") or f"{parent.get('identifier') or parent_id}: child task")[:140]
        human_description = str(template.get("description") or "")
        child_description = describe_ticket(
            human_description,
            zoe_kind="child",
            evidence_profile=str(template.get("evidence_profile") or parent_meta.get("evidence_profile") or "code"),
            engineering_mode=str(template.get("engineering_mode") or parent_meta.get("engineering_mode") or "interactive"),
            acceptance_criteria=list(template.get("acceptance_criteria") or []),
            evidence_expectations=list(template.get("evidence_expectations") or []),
            source="scope_split",
            parent_issue_id=parent_id,
        )
        child = await self.create_issue(
            title=title,
            description=child_description,
            priority=str(template.get("priority") or parent.get("priority") or "medium"),
            status=str(template.get("status") or "backlog"),
            assignee_id=template.get("assignee_id") or parent.get("assignee_id"),
            assignee_type=template.get("assignee_type") or parent.get("assignee_type") or "agent",
            project_id=template.get("project_id") or parent.get("project_id"),
        )
        child_id = str(child.get("id") or "")
        for label in template.get("labels") or []:
            if child_id:
                await self.attach_label(child_id, str(label))
        return child

    async def record_progress(
        self,
        issue_id: str,
        *,
        phase: str | None = None,
        evidence: str | None = None,
        pr_url: str | None = None,
        blocker: str | None = None,
        clear_blocker: bool = False,
        greptile_status: str | None = None,
        merge_sha: str | None = None,
        status: str | None = None,
        dispatch_approved: bool | None = None,
        completion_reason: str | None = None,
    ) -> dict:
        """Record operator-visible Zoe progress on a Multica issue."""
        from multica_ticket_contract import update_ticket_progress

        issue = await self.get_issue(issue_id)
        if not issue.get("id"):
            return {}
        updated_description = update_ticket_progress(
            issue.get("description") or "",
            phase=phase,
            evidence=evidence,
            pr_url=pr_url,
            blocker=blocker,
            clear_blocker=clear_blocker,
            greptile_status=greptile_status,
            merge_sha=merge_sha,
            dispatch_approved=dispatch_approved,
            completion_reason=completion_reason,
        )
        return await self.update_issue(issue_id, status=status, description=updated_description)


# Module-level singleton
_client: MULClient | None = None


def get_multica_client() -> MULClient:
    global _client, _cached_self_imp_agent_id, _cached_self_imp_project_id
    current = _multica_env_key()
    if _client is None or (_client._base, _client._token, _client._workspace) != current:
        _client = MULClient()
        _cached_self_imp_agent_id = None
        _cached_self_imp_project_id = None
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
    contract_snapshot: str | Mapping[str, Any] | None = None,
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

    _agent_id, project_id = await _lookup_evolution_resources(client)
    hermes_id = get_engineering_multica_agent_id()

    full_desc = description
    if evidence:
        full_desc = f"{description}\n\n**Evidence:** {evidence}"
    try:
        from multica_ticket_contract import describe_ticket
        from zoe_evolution_proposal_adapter import load_proposal_contract_snapshot

        contract = load_proposal_contract_snapshot(contract_snapshot)
        proposal = contract.get("proposal", {}) if contract else {}
        gate = proposal.get("approval_gate", {}) if isinstance(proposal, dict) else {}
        contract_metadata = {
            "evolution_proposal_id": proposal_id,
            "evolution_contract_schema": contract.get("schema") if contract else None,
            "evolution_contract_version": contract.get("version") if contract else None,
            "evolution_contract_proposal_id": proposal.get("proposal_id") if isinstance(proposal, dict) else None,
            "evolution_contract_autonomy_class": proposal.get("autonomy_class") if isinstance(proposal, dict) else None,
            "evolution_contract_risk": proposal.get("risk") if isinstance(proposal, dict) else None,
            "evolution_contract_status": proposal.get("status") if isinstance(proposal, dict) else None,
            "evolution_contract_allowed_to_prepare": gate.get("allowed_to_prepare") if isinstance(gate, dict) else None,
            "evolution_contract_approval_required": proposal.get("approval_required") if isinstance(proposal, dict) else None,
        }

        full_desc = describe_ticket(
            full_desc,
            zoe_kind="harness_fix" if proposal_type in {"bug", "fix", "harness"} else "feature",
            evidence_profile="code",
            engineering_mode="unattended",
            acceptance_criteria=["Proposal is triaged into a narrow, reviewable change before dispatch."],
            evidence_expectations=["Journal evidence", "Tests or validators", "Greptile 5/5 before merge"],
            source=f"evolution_proposal:{proposal_id}",
            metadata=contract_metadata,
        )
    except Exception as exc:
        logger.warning(
            "sync_evolution_proposal_to_multica: failed to build contract metadata for %s: %s",
            proposal_id,
            exc,
        )

    payload: dict[str, Any] = {
        "title": title,
        "description": full_desc,
        "status": "backlog",
        "priority": "medium",
        "assignee_id": hermes_id,
        "assignee_type": "agent",
    }
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
                                json={"name": label_name, "color": _DEFAULT_LABEL_COLOR},
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
