"""Gate PR-backed capability-profile edits from created Multica tickets.

This module is intentionally side-effect-free. It validates that a profile
promotion ticket created by ``zoe_capability_profile_ticket_writer`` still
matches the current profile source and carries PR, rollback, verification, and
Greptile evidence before a caller may prepare a profile-edit PR from the
embedded patch. It does not write files, apply patches, create branches, or
merge anything.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from multica_ticket_contract import parse_ticket_block
from zoe_capability_profile_multica_handoff import CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE
from zoe_capability_profile_ticket_writer import CAPABILITY_PROFILE_TICKET_WRITER_SOURCE


CAPABILITY_PROFILE_PR_EDIT_GATE_SOURCE = "capability_profile_pr_edit_gate"


@dataclass(frozen=True)
class CapabilityProfilePREditPlan:
    ticket_id: str
    target_path: str
    patch_text: str
    promoted_capability_ids: tuple[str, ...]
    pr_refs: tuple[str, ...]
    rollback_refs: tuple[str, ...]
    verification_refs: tuple[str, ...]
    greptile_refs: tuple[str, ...]
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "promoted_capability_ids", tuple(self.promoted_capability_ids))
        object.__setattr__(self, "pr_refs", tuple(self.pr_refs))
        object.__setattr__(self, "rollback_refs", tuple(self.rollback_refs))
        object.__setattr__(self, "verification_refs", tuple(self.verification_refs))
        object.__setattr__(self, "greptile_refs", tuple(self.greptile_refs))
        object.__setattr__(self, "blockers", tuple(dict.fromkeys(self.blockers)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.blockers and (self.patch_text or self.promoted_capability_ids):
            raise ValueError("blocked profile PR edit plans cannot carry patch text or promoted capability IDs")

    @property
    def allowed_to_prepare_pr_edit(self) -> bool:
        return bool(self.ticket_id and self.target_path and self.patch_text and self.promoted_capability_ids) and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_prepare_pr_edit": self.allowed_to_prepare_pr_edit,
            "ticket_id": self.ticket_id,
            "target_path": self.target_path,
            "patch_text": self.patch_text,
            "promoted_capability_ids": list(self.promoted_capability_ids),
            "pr_refs": list(self.pr_refs),
            "rollback_refs": list(self.rollback_refs),
            "verification_refs": list(self.verification_refs),
            "greptile_refs": list(self.greptile_refs),
            "blockers": list(self.blockers),
            "metadata": dict(self.metadata),
        }


def build_capability_profile_pr_edit_plan_from_ticket(
    *,
    ticket_id: str,
    ticket_description: str,
    current_source_text: str,
    patch_text: str,
    promotion_manifest: str,
    pr_refs: Sequence[str],
    rollback_refs: Sequence[str],
    verification_refs: Sequence[str],
    greptile_refs: Sequence[str],
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfilePREditPlan:
    """Validate a created profile ticket before preparing a PR edit.

    ``patch_text`` and ``promotion_manifest`` should come from the ticket body or
    the ticket writer result. They are hash-checked against the Zoe ticket block
    so stale or tampered ticket content fails closed.
    """

    ticket = parse_ticket_block(ticket_description)
    blockers = _profile_pr_edit_blockers(
        ticket_id=ticket_id,
        ticket=ticket,
        current_source_text=current_source_text,
        patch_text=patch_text,
        promotion_manifest=promotion_manifest,
        pr_refs=pr_refs,
        rollback_refs=rollback_refs,
        verification_refs=verification_refs,
        greptile_refs=greptile_refs,
    )
    if blockers:
        return CapabilityProfilePREditPlan(
            ticket_id=str(ticket_id or ""),
            target_path="",
            patch_text="",
            promoted_capability_ids=(),
            pr_refs=tuple(str(ref) for ref in pr_refs),
            rollback_refs=tuple(str(ref) for ref in rollback_refs),
            verification_refs=tuple(str(ref) for ref in verification_refs),
            greptile_refs=tuple(str(ref) for ref in greptile_refs),
            blockers=blockers,
            metadata=_metadata(metadata, ticket),
        )

    promotion = ticket["profile_promotion"]
    return CapabilityProfilePREditPlan(
        ticket_id=str(ticket_id),
        target_path=str(promotion["target_path"]),
        patch_text=patch_text,
        promoted_capability_ids=tuple(str(item) for item in promotion["promoted_capability_ids"]),
        pr_refs=tuple(str(ref) for ref in pr_refs),
        rollback_refs=tuple(str(ref) for ref in rollback_refs),
        verification_refs=tuple(str(ref) for ref in verification_refs),
        greptile_refs=tuple(str(ref) for ref in greptile_refs),
        metadata=_metadata(metadata, ticket),
    )


def render_capability_profile_pr_edit_patch(plan: CapabilityProfilePREditPlan) -> str:
    """Return the reviewed patch text only for applyable PR edit plans."""

    if not plan.allowed_to_prepare_pr_edit:
        raise ValueError("cannot render capability profile PR edit patch for blocked plan")
    return plan.patch_text


def _profile_pr_edit_blockers(
    *,
    ticket_id: str,
    ticket: Mapping[str, Any],
    current_source_text: str,
    patch_text: str,
    promotion_manifest: str,
    pr_refs: Sequence[str],
    rollback_refs: Sequence[str],
    verification_refs: Sequence[str],
    greptile_refs: Sequence[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not ticket_id:
        blockers.append("missing_ticket_id")
    if not ticket:
        blockers.append("missing_zoe_ticket_metadata")
        return tuple(blockers)
    if ticket.get("parse_error"):
        blockers.append("invalid_zoe_ticket_metadata")
        return tuple(blockers)
    if ticket.get("zoe_kind") != "capability_profile_promotion":
        blockers.append("invalid_zoe_kind")
    if ticket.get("ticket_writer_source") != CAPABILITY_PROFILE_TICKET_WRITER_SOURCE:
        blockers.append("invalid_ticket_writer_source")

    gate = ticket.get("profile_ticket_gate")
    if not isinstance(gate, Mapping):
        blockers.append("missing_profile_ticket_gate")
    elif gate.get("allowed_to_create_ticket") is not True:
        blockers.append("profile_ticket_gate_not_allowed")

    promotion = ticket.get("profile_promotion")
    if not isinstance(promotion, Mapping):
        blockers.append("missing_profile_promotion_metadata")
        promotion = {}
    elif promotion.get("source") != CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE:
        blockers.append("invalid_profile_promotion_source")

    target_path = str(promotion.get("target_path") or "")
    promoted_ids = promotion.get("promoted_capability_ids")
    if not target_path:
        blockers.append("missing_target_path")
    if not isinstance(promoted_ids, list) or not promoted_ids:
        blockers.append("missing_promoted_capability_ids")
    if not current_source_text:
        blockers.append("missing_current_source_text")
    elif promotion.get("source_sha256") != _sha256(current_source_text):
        blockers.append("stale_or_mismatched_source_sha256")
    if not patch_text:
        blockers.append("missing_patch_text")
    elif promotion.get("patch_sha256") != _sha256(patch_text):
        blockers.append("patch_sha256_mismatch")
    if not promotion_manifest:
        blockers.append("missing_promotion_manifest")
    elif promotion.get("promotion_manifest_sha256") != _sha256(promotion_manifest):
        blockers.append("promotion_manifest_sha256_mismatch")
    if target_path and patch_text and f"--- a/{target_path}" not in patch_text:
        blockers.append("patch_target_missing_a_header")
    if target_path and patch_text and f"+++ b/{target_path}" not in patch_text:
        blockers.append("patch_target_missing_b_header")

    if not tuple(ref for ref in pr_refs if str(ref).strip()):
        blockers.append("missing_pr_refs")
    if not tuple(ref for ref in rollback_refs if str(ref).strip()):
        blockers.append("missing_rollback_refs")
    if not tuple(ref for ref in verification_refs if str(ref).strip()):
        blockers.append("missing_verification_refs")
    if not tuple(ref for ref in greptile_refs if str(ref).strip()):
        blockers.append("missing_greptile_refs")
    return tuple(dict.fromkeys(blockers))


def _metadata(metadata: Mapping[str, Any] | None, ticket: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": CAPABILITY_PROFILE_PR_EDIT_GATE_SOURCE,
        "ticket_schema": ticket.get("schema"),
        "ticket_source": ticket.get("source"),
        "extra": dict(metadata or {}),
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "CAPABILITY_PROFILE_PR_EDIT_GATE_SOURCE",
    "CapabilityProfilePREditPlan",
    "build_capability_profile_pr_edit_plan_from_ticket",
    "render_capability_profile_pr_edit_patch",
]
