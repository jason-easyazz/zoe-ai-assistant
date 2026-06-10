"""Create Multica tickets only after profile handoff ticket-gate approval.

This is the first narrow writer bridge for governed capability-profile handoffs.
It does not build handoffs and it does not bypass the gate; callers must provide
a handoff packet and explicit operator approval evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from multica_client import get_engineering_multica_agent_id, get_multica_client
from multica_ticket_contract import normalize_ticket_metadata, parse_ticket_block, write_ticket_block
from zoe_capability_profile_multica_handoff import CapabilityProfileMulticaHandoff
from zoe_capability_profile_ticket_gate import (
    CapabilityProfileTicketWriterGateDecision,
    evaluate_capability_profile_ticket_writer_gate,
)


CAPABILITY_PROFILE_TICKET_WRITER_SOURCE = "capability_profile_ticket_writer"
CAPABILITY_PROFILE_TICKET_LABEL = "capability-profile-promotion"


@dataclass(frozen=True)
class CapabilityProfileTicketWriterResult:
    gate_decision: CapabilityProfileTicketWriterGateDecision
    issue: Mapping[str, Any]
    label_results: tuple[Mapping[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue", MappingProxyType(dict(self.issue)))
        object.__setattr__(self, "label_results", tuple(MappingProxyType(dict(item)) for item in self.label_results))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.issue.get("id") and self.blockers:
            raise ValueError("ticket writer results with created issue cannot carry blockers")
        if not self.created and self.issue and not self.blockers:
            raise ValueError("failed ticket writer results with issue payload require blockers")

    @property
    def created(self) -> bool:
        return bool(self.issue.get("id")) and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "blockers": list(self.blockers),
            "gate_decision": self.gate_decision.to_dict(),
            "issue": dict(self.issue),
            "label_results": [dict(item) for item in self.label_results],
            "metadata": dict(self.metadata),
        }


async def create_capability_profile_handoff_ticket(
    handoff: CapabilityProfileMulticaHandoff,
    *,
    operator_id: str,
    approval_refs: Sequence[str],
    evidence_refs: Sequence[str] = (),
    client: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfileTicketWriterResult:
    """Create a Multica ticket from a profile handoff only when the gate allows it."""

    gate_decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id=operator_id,
        approval_refs=approval_refs,
        evidence_refs=evidence_refs,
        metadata={"writer_source": CAPABILITY_PROFILE_TICKET_WRITER_SOURCE, **dict(metadata or {})},
    )
    writer_metadata = _metadata(metadata, gate_decision)
    if not gate_decision.allowed_to_create_ticket:
        return CapabilityProfileTicketWriterResult(
            gate_decision=gate_decision,
            issue={},
            blockers=tuple(gate_decision.blockers),
            metadata=writer_metadata,
        )

    resolved_client = client or get_multica_client()
    ticket_payload = dict(gate_decision.ticket_payload)
    issue = await resolved_client.create_issue(
        title=str(ticket_payload["title"])[:120],
        description=_ticket_description(ticket_payload, gate_decision),
        priority="medium",
        status="backlog",
        assignee_id=get_engineering_multica_agent_id(),
        assignee_type="agent",
    )
    blockers: list[str] = []
    if not issue.get("id"):
        blockers.append("multica_issue_not_created")
        if issue.get("error"):
            blockers.append("multica_create_issue_error")
        return CapabilityProfileTicketWriterResult(
            gate_decision=gate_decision,
            issue=issue,
            blockers=tuple(blockers),
            metadata=writer_metadata,
        )

    label_results: list[Mapping[str, Any]] = []
    attach_label = getattr(resolved_client, "attach_label", None)
    if callable(attach_label):
        try:
            label_results.append(await attach_label(str(issue["id"]), CAPABILITY_PROFILE_TICKET_LABEL))
        except Exception as exc:
            label_results.append({"label_name": CAPABILITY_PROFILE_TICKET_LABEL, "error": str(exc)})
    return CapabilityProfileTicketWriterResult(
        gate_decision=gate_decision,
        issue=issue,
        label_results=tuple(label_results),
        metadata=writer_metadata,
    )


def _ticket_description(
    ticket_payload: Mapping[str, Any],
    gate_decision: CapabilityProfileTicketWriterGateDecision,
) -> str:
    description = str(ticket_payload["description"])
    metadata = parse_ticket_block(description)
    metadata.update(
        {
            "profile_ticket_gate": gate_decision.to_dict(),
            "ticket_writer_source": CAPABILITY_PROFILE_TICKET_WRITER_SOURCE,
            "operator_id": gate_decision.operator_id,
            "ticket_writer_approval_refs": list(gate_decision.approval_refs),
        }
    )
    description = write_ticket_block(description, normalize_ticket_metadata(metadata))
    manifest = _indent_block(_json_text(ticket_payload["promotion_manifest"]))
    patch = _indent_block(str(ticket_payload["patch_text"]))
    return description.rstrip() + "\n\n## Promotion Manifest\n\n" + manifest + "\n## Profile Patch\n\n" + patch


def _indent_block(text: str) -> str:
    normalized = text if text.endswith("\n") else text + "\n"
    lines = []
    for line in normalized.splitlines(True):
        lines.append(f"    {line}" if line.strip() else "\n")
    return "".join(lines)


def _json_text(text: Any) -> str:
    raw = str(text)
    try:
        return json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"
    except Exception:
        return raw if raw.endswith("\n") else raw + "\n"


def _metadata(
    metadata: Mapping[str, Any] | None,
    gate_decision: CapabilityProfileTicketWriterGateDecision,
) -> dict[str, Any]:
    return {
        "source": CAPABILITY_PROFILE_TICKET_WRITER_SOURCE,
        "gate_allowed": gate_decision.allowed_to_create_ticket,
        "operator_id": gate_decision.operator_id,
        "extra": dict(metadata or {}),
    }


__all__ = [
    "CAPABILITY_PROFILE_TICKET_LABEL",
    "CAPABILITY_PROFILE_TICKET_WRITER_SOURCE",
    "CapabilityProfileTicketWriterResult",
    "create_capability_profile_handoff_ticket",
]
