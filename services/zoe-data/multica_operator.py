"""Operator-safe Multica ticket actions used by chat and maintenance tools."""

from __future__ import annotations

from typing import Any

from multica_client import get_engineering_multica_agent_id, get_multica_client
from multica_ticket_contract import (
    describe_ticket,
    normalize_ticket_metadata,
    parse_ticket_block,
    write_ticket_block,
)


async def find_issue(reference: str) -> dict[str, Any]:
    client = get_multica_client()
    if hasattr(client, "resolve_issue"):
        return await client.resolve_issue(reference)
    # Best-effort compatibility for older test/fake clients; real clients should expose resolve_issue.
    issues = await client.list_issues()
    if not issues:
        return {}
    wanted = str(reference).strip().lower()
    for issue in issues:
        if str(issue.get("identifier") or "").lower() == wanted or str(issue.get("id") or "").lower() == wanted:
            return issue
    return {}


async def create_ticket(title: str, *, source: str = "operator") -> dict[str, Any]:
    client = get_multica_client()
    description = describe_ticket(
        title,
        zoe_kind="operator_task",
        evidence_profile="code",
        engineering_mode="interactive",
        acceptance_criteria=["Deliver the requested behavior in a small, reviewable change."],
        evidence_expectations=["Focused tests or validators", "PR URL for code changes"],
        source=source,
    )
    issue = await client.create_issue(
        title=title[:120],
        description=description,
        priority="medium",
        status="backlog",
        assignee_id=get_engineering_multica_agent_id(),
        assignee_type="agent",
    )
    if issue.get("id"):
        await client.attach_label(str(issue["id"]), "operator-task")
    return issue


async def move_to_todo(reference: str, *, approve: bool = True) -> dict[str, Any]:
    client = get_multica_client()
    issue = await find_issue(reference)
    if not issue.get("id"):
        return {}
    metadata = parse_ticket_block(issue.get("description") or "")
    metadata.update(
        {
            "dispatch_approved": bool(approve),
            "blocked_reason": None,
            "acceptance_criteria": metadata.get("acceptance_criteria")
            or ["Deliver the ticket in a small, reviewable change."],
            "evidence_expectations": metadata.get("evidence_expectations")
            or ["Focused tests or validators"],
        }
    )
    description = write_ticket_block(
        issue.get("description") or "",
        normalize_ticket_metadata(metadata),
    )
    return await client.update_issue(
        str(issue["id"]),
        status="todo",
        description=description,
        assignee_id=get_engineering_multica_agent_id(),
        assignee_type="agent",
    )


async def split_ticket(
    reference: str,
    *,
    child_title: str,
    reason: str = "operator requested split",
) -> dict[str, Any]:
    client = get_multica_client()
    parent = await find_issue(reference)
    if not parent.get("id"):
        return {}
    child = await client.create_child_issue(
        parent,
        {
            "title": child_title[:120],
            "description": f"Split from {parent.get('identifier') or parent.get('id')}: {reason}",
            "acceptance_criteria": ["Complete this bounded child scope."],
            "evidence_expectations": ["Focused tests or validators"],
        },
    )
    if not child.get("id"):
        return {}
    parent_metadata = parse_ticket_block(parent.get("description") or "")
    child_ids = list(parent_metadata.get("child_issue_ids") or [])
    child_ids.append(str(child["id"]))
    parent_metadata.update(
        {
            "zoe_kind": "parent",
            "blocked_reason": reason,
            "child_issue_ids": list(dict.fromkeys(child_ids)),
            "dispatch_approved": False,
        }
    )
    await client.update_issue(
        str(parent["id"]),
        status="blocked",
        description=write_ticket_block(
            parent.get("description") or "",
            normalize_ticket_metadata(parent_metadata),
        ),
    )
    return child
