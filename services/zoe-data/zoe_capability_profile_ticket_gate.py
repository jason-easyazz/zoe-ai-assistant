"""Gate future Multica ticket creation for capability-profile handoffs.

The profile handoff packet is still inert. This module is the last pure gate a
future ticket writer must pass before submitting that packet to Multica. It
does not create tickets or call Multica.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile_multica_handoff import (
    CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE,
    CapabilityProfileMulticaHandoff,
    render_capability_profile_multica_handoff_payload,
)
from zoe_capability_utils import merge_string_refs


CAPABILITY_PROFILE_TICKET_GATE_SOURCE = "capability_profile_ticket_gate"


@dataclass(frozen=True)
class CapabilityProfileTicketWriterGateDecision:
    allowed_to_create_ticket: bool
    blockers: tuple[str, ...]
    operator_id: str
    approval_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    ticket_payload: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticket_payload", MappingProxyType(dict(self.ticket_payload)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.allowed_to_create_ticket and self.blockers:
            raise ValueError("allowed ticket gate decisions cannot carry blockers")
        if self.allowed_to_create_ticket and not self.ticket_payload:
            raise ValueError("allowed ticket gate decisions require a ticket_payload")
        if not self.allowed_to_create_ticket and not self.blockers:
            raise ValueError("blocked ticket gate decisions require blockers")
        if not self.allowed_to_create_ticket and self.ticket_payload:
            raise ValueError("blocked ticket gate decisions cannot carry ticket_payload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_create_ticket": self.allowed_to_create_ticket,
            "blockers": list(self.blockers),
            "operator_id": self.operator_id,
            "approval_refs": list(self.approval_refs),
            "evidence_refs": list(self.evidence_refs),
            "ticket_payload": dict(self.ticket_payload),
            "metadata": dict(self.metadata),
        }


def evaluate_capability_profile_ticket_writer_gate(
    handoff: CapabilityProfileMulticaHandoff,
    *,
    operator_id: str,
    approval_refs: Sequence[str],
    evidence_refs: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfileTicketWriterGateDecision:
    """Return an allow/deny decision for future profile-handoff ticket writers."""

    merged_approval_refs = merge_string_refs(approval_refs)
    merged_evidence_refs = merge_string_refs(evidence_refs, approval_refs)
    blockers = _gate_blockers(handoff, operator_id=operator_id, approval_refs=merged_approval_refs)
    gate_metadata = _metadata(metadata, handoff)
    if blockers:
        return CapabilityProfileTicketWriterGateDecision(
            allowed_to_create_ticket=False,
            blockers=blockers,
            operator_id=operator_id,
            approval_refs=merged_approval_refs,
            evidence_refs=merged_evidence_refs,
            ticket_payload={},
            metadata=gate_metadata,
        )

    return CapabilityProfileTicketWriterGateDecision(
        allowed_to_create_ticket=True,
        blockers=(),
        operator_id=operator_id,
        approval_refs=merged_approval_refs,
        evidence_refs=merged_evidence_refs,
        ticket_payload=json.loads(render_capability_profile_multica_handoff_payload(handoff)),
        metadata=gate_metadata,
    )


def _gate_blockers(
    handoff: CapabilityProfileMulticaHandoff,
    *,
    operator_id: str,
    approval_refs: Sequence[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not operator_id:
        blockers.append("missing_operator_id")
    if not approval_refs:
        blockers.append("missing_ticket_writer_approval_refs")
    if not handoff.allowed_to_create_ticket:
        blockers.append("handoff_not_createable")
    blockers.extend(handoff.blockers)
    if not handoff.title.strip():
        blockers.append("missing_title")
    if not handoff.description:
        blockers.append("missing_description")
    if not handoff.promotion_manifest:
        blockers.append("missing_promotion_manifest")
    if not handoff.patch_text:
        blockers.append("missing_patch_text")

    profile_promotion = handoff.ticket_metadata.get("profile_promotion")
    if not isinstance(profile_promotion, Mapping):
        blockers.append("missing_profile_promotion_metadata")
        return tuple(dict.fromkeys(blockers))

    if handoff.ticket_metadata.get("zoe_kind") != "capability_profile_promotion":
        blockers.append("invalid_zoe_kind")
    if profile_promotion.get("source") != CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE:
        blockers.append("invalid_profile_promotion_source")
    if not profile_promotion.get("promoted_capability_ids"):
        blockers.append("missing_promoted_capability_ids")
    if profile_promotion.get("promotion_manifest_sha256") != _sha256(handoff.promotion_manifest):
        blockers.append("promotion_manifest_hash_mismatch")
    if profile_promotion.get("patch_sha256") != _sha256(handoff.patch_text):
        blockers.append("patch_hash_mismatch")
    if not profile_promotion.get("source_sha256"):
        blockers.append("missing_source_sha256")
    return tuple(dict.fromkeys(blockers))


def _metadata(metadata: Mapping[str, Any] | None, handoff: CapabilityProfileMulticaHandoff) -> dict[str, Any]:
    profile_promotion = handoff.ticket_metadata.get("profile_promotion", {})
    promoted_ids = profile_promotion.get("promoted_capability_ids", ()) if isinstance(profile_promotion, Mapping) else ()
    return {
        "source": CAPABILITY_PROFILE_TICKET_GATE_SOURCE,
        "handoff_source": handoff.metadata.get("source"),
        "promoted_capability_ids": list(promoted_ids or ()),
        "extra": dict(metadata or {}),
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "CAPABILITY_PROFILE_TICKET_GATE_SOURCE",
    "CapabilityProfileTicketWriterGateDecision",
    "evaluate_capability_profile_ticket_writer_gate",
]
