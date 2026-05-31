"""Executor adapter registry — routes Multica issues to a backend by assignee.

Multica is the agnostic source of truth. Which executor runs an issue is
selected by the issue's ``assignee_id`` here, so adding a new backend
(OpenClaw, Codex, Cursor, ...) is a single new adapter + one mapping entry —
no change to Zoe core. Today only the Hermes Kanban adapter is implemented.
"""
from __future__ import annotations

import logging
from typing import Any

from executors.kanban_adapter import KanbanAdapter

logger = logging.getLogger(__name__)

_kanban = KanbanAdapter()


def _adapter_for_assignee(assignee_id: str | None) -> Any | None:
    """Return the executor adapter for a Multica assignee, or None to skip.

    Hermes (the engineering agent) -> Kanban adapter. Any other assignee
    (OpenClaw fallback, unassigned, future agents without an adapter) returns
    None so Zoe does not auto-dispatch it.
    """
    from multica_client import get_engineering_multica_agent_id  # type: ignore[import]

    hermes_id = get_engineering_multica_agent_id()
    if assignee_id and str(assignee_id) == str(hermes_id):
        return _kanban
    return None


async def dispatch_issue(issue: dict) -> dict:
    """Dispatch a Multica issue to its executor. No-op result when unrouted."""
    adapter = _adapter_for_assignee(issue.get("assignee_id"))
    if adapter is None:
        return {"ok": False, "reason": "no executor adapter for assignee", "skipped": True}
    return await adapter.dispatch(issue)


async def poll_ref(external_ref: str, *, backend: str = "kanban") -> dict:
    """Poll an external reference for its aggregate status."""
    if backend == "kanban":
        return await _kanban.poll(external_ref)
    return {"found": False, "status": "not_found", "reason": f"unknown backend {backend}"}
