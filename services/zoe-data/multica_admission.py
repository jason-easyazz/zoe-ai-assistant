"""Safe single-lane admission from Multica backlog into the engineering driver."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from multica_ticket_contract import parse_ticket_block

_CARD_UPGRADE_RE = re.compile(r"(?i)^(card-upgrade)\s*:\s*phase\s*(\d+)")
_SKYBRIDGE_RE = re.compile(r"(?i)^skybridge\s+p(\d+)")
_GENERIC_PHASE_RE = re.compile(r"(?i)^([\w][\w-]*)\s*:\s*phase\s*(\d+)")
_PRIORITY = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}


def parse_phased_title(title: str) -> tuple[str, int] | None:
    text = (title or "").strip()
    match = _CARD_UPGRADE_RE.match(text)
    if match:
        return match.group(1).lower(), int(match.group(2))
    match = _SKYBRIDGE_RE.match(text)
    if match:
        return "skybridge", int(match.group(1))
    match = _GENERIC_PHASE_RE.match(text)
    if match:
        return match.group(1).lower(), int(match.group(2))
    return None


def ticket_is_dispatch_approved(issue: dict[str, Any], *, hermes_agent_id: str) -> bool:
    if str(issue.get("assignee_id") or "") != str(hermes_agent_id):
        return False
    if str(issue.get("assignee_type") or "agent") not in {"", "agent"}:
        return False
    metadata = parse_ticket_block(issue.get("description") or "")
    if metadata.get("schema") != 1 or metadata.get("parse_error"):
        return False
    if metadata.get("dispatch_approved") is not True:
        return False
    if metadata.get("blocked_reason"):
        return False
    if not metadata.get("acceptance_criteria") or not metadata.get("evidence_expectations"):
        return False
    if str(metadata.get("zoe_kind") or "") == "parent":
        return False
    source = str(metadata.get("source") or "").lower()
    if "smoke" in source or "e2e" in source:
        return False
    return True


def _predecessors_done(
    sequence: str,
    phase: int,
    by_sequence: dict[str, list[dict[str, Any]]],
) -> bool:
    for sibling in by_sequence.get(sequence) or []:
        parsed = parse_phased_title(sibling.get("title") or "")
        if not parsed or parsed[0] != sequence or parsed[1] >= phase:
            continue
        if sibling.get("status") != "done":
            return False
    return True


def select_next_approved_issue(
    backlog: list[dict[str, Any]],
    all_issues: list[dict[str, Any]],
    *,
    hermes_agent_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return one approved backlog ticket, preserving phased predecessor order."""
    by_sequence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in all_issues:
        parsed = parse_phased_title(issue.get("title") or "")
        if parsed:
            by_sequence[parsed[0]].append(issue)

    eligible = [
        issue
        for issue in backlog
        if ticket_is_dispatch_approved(issue, hermes_agent_id=hermes_agent_id)
    ]
    eligible.sort(
        key=lambda issue: (
            int(parse_ticket_block(issue.get("description") or "").get("queue_order") or 1_000_000),
            _PRIORITY.get(str(issue.get("priority") or "none").lower(), 4),
            str(issue.get("identifier") or issue.get("id") or ""),
        )
    )

    held: list[str] = []
    for issue in eligible:
        parsed = parse_phased_title(issue.get("title") or "")
        if parsed and not _predecessors_done(parsed[0], parsed[1], by_sequence):
            held.append(
                f"{issue.get('identifier') or issue.get('id')}: "
                f"{parsed[0]} phase {parsed[1]} waiting for predecessor"
            )
            continue
        return issue, held
    return None, held
