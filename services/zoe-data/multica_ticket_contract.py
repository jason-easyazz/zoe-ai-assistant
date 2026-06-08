"""Structured Zoe ticket metadata embedded in Multica issue descriptions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

FENCE_LANG = "zoe-ticket"
SCHEMA_VERSION = 1

_BLOCK_RE = re.compile(
    r"(?P<prefix>^|\n)```zoe-ticket\s*\n(?P<body>.*?)\n```(?P<suffix>\n|$)",
    re.DOTALL,
)


DEFAULT_TICKET: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "zoe_kind": "operator_task",
    "evidence_profile": "code",
    "engineering_mode": "interactive",
    "acceptance_criteria": [],
    "evidence_expectations": [],
    "parent_issue_id": None,
    "child_issue_ids": [],
    "blocked_reason": None,
    "pr_url": None,
    "merge_sha": None,
    "greptile_status": None,
    "phase": None,
    "last_evidence": None,
}


def parse_ticket_block(description: str | None) -> dict[str, Any]:
    """Return parsed Zoe metadata from a Multica description, if present."""
    text = description or ""
    match = _BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return {"schema": SCHEMA_VERSION, "parse_error": "invalid_json"}
    return parsed if isinstance(parsed, dict) else {}


def normalize_ticket_metadata(metadata: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Merge caller metadata into the stable Zoe ticket schema."""
    ticket = dict(DEFAULT_TICKET)
    for key, value in (metadata or {}).items():
        if value is not None:
            ticket[key] = value
    for key, value in overrides.items():
        if value is not None:
            ticket[key] = value
    ticket["schema"] = SCHEMA_VERSION
    ticket.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    return ticket


def write_ticket_block(description: str | None, metadata: dict[str, Any]) -> str:
    """Replace only the fenced Zoe metadata block, preserving human prose."""
    text = (description or "").rstrip()
    block = "```zoe-ticket\n" + json.dumps(metadata, sort_keys=True, indent=2) + "\n```"
    match = _BLOCK_RE.search(text)
    if match:
        start, end = match.span()
        prefix = text[:start].rstrip()
        suffix = text[end:]
        suffix = ("\n" + suffix) if suffix and not suffix.startswith("\n") else suffix
        separator = "\n\n" if prefix else ""
        return f"{prefix}{separator}{block}{suffix}"
    if not text:
        return block
    return f"{text}\n\n{block}"


def describe_ticket(
    human_description: str,
    *,
    zoe_kind: str = "operator_task",
    evidence_profile: str = "code",
    engineering_mode: str = "interactive",
    acceptance_criteria: list[str] | None = None,
    evidence_expectations: list[str] | None = None,
    source: str | None = None,
    parent_issue_id: str | None = None,
) -> str:
    """Build a Multica description with preserved prose plus Zoe metadata."""
    metadata = normalize_ticket_metadata(
        {
            "zoe_kind": zoe_kind,
            "evidence_profile": evidence_profile,
            "engineering_mode": engineering_mode,
            "acceptance_criteria": acceptance_criteria or [],
            "evidence_expectations": evidence_expectations or [],
            "source": source,
            "parent_issue_id": parent_issue_id,
        }
    )
    return write_ticket_block(human_description, metadata)


def update_ticket_progress(
    description: str | None,
    *,
    phase: str | None = None,
    evidence: str | None = None,
    pr_url: str | None = None,
    blocker: str | None = None,
    clear_blocker: bool = False,
    greptile_status: str | None = None,
    merge_sha: str | None = None,
    child_issue_ids: list[str] | None = None,
    dispatch_approved: bool | None = None,
) -> str:
    """Patch progress fields inside the Zoe block without touching prose."""
    current = parse_ticket_block(description)
    metadata = normalize_ticket_metadata(current)
    if phase is not None:
        metadata["phase"] = phase
    if evidence is not None:
        metadata["last_evidence"] = evidence
    if pr_url is not None:
        metadata["pr_url"] = pr_url
    if clear_blocker:
        metadata["blocked_reason"] = None
    elif blocker is not None:
        metadata["blocked_reason"] = blocker
    if greptile_status is not None:
        metadata["greptile_status"] = greptile_status
    if merge_sha is not None:
        metadata["merge_sha"] = merge_sha
    if child_issue_ids is not None:
        metadata["child_issue_ids"] = child_issue_ids
    if dispatch_approved is not None:
        metadata["dispatch_approved"] = dispatch_approved
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    return write_ticket_block(description, metadata)


def append_child_id(description: str | None, child_id: str) -> str:
    """Add a child issue ID to the Zoe metadata block exactly once."""
    current = parse_ticket_block(description)
    metadata = normalize_ticket_metadata(current)
    children = [str(item) for item in metadata.get("child_issue_ids") or []]
    if child_id not in children:
        children.append(child_id)
    metadata["child_issue_ids"] = children
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    return write_ticket_block(description, metadata)
