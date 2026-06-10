"""Build Multica handoff packets for capability-profile promotions.

This module is intentionally inert. It converts already-governed capability
promotion manifests and patch plans into a ticket description/metadata packet
that a future Multica writer can submit. It does not create tickets, edit files,
or apply patches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from multica_ticket_contract import describe_ticket, parse_ticket_block
from zoe_capability_profile_patch_writer import (
    CapabilityProfilePatchPlan,
    render_capability_profile_patch,
)
from zoe_capability_profile_promotion import (
    CapabilityProfilePromotionPlan,
    render_capability_profile_promotion_manifest,
)


CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE = "capability_profile_multica_handoff"


@dataclass(frozen=True)
class CapabilityProfileMulticaHandoff:
    title: str
    description: str
    ticket_metadata: Mapping[str, Any]
    promotion_manifest: str
    patch_text: str
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticket_metadata", MappingProxyType(dict(self.ticket_metadata)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.blockers and (self.description or self.promotion_manifest or self.patch_text):
            raise ValueError("blocked capability profile handoffs cannot carry ticket payloads")

    @property
    def allowed_to_create_ticket(self) -> bool:
        return bool(self.description and self.promotion_manifest and self.patch_text) and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_create_ticket": self.allowed_to_create_ticket,
            "title": self.title,
            "description": self.description,
            "ticket_metadata": dict(self.ticket_metadata),
            "promotion_manifest": self.promotion_manifest,
            "patch_text": self.patch_text,
            "blockers": list(self.blockers),
            "metadata": dict(self.metadata),
        }


def build_capability_profile_multica_handoff(
    promotion_plan: CapabilityProfilePromotionPlan,
    patch_plan: CapabilityProfilePatchPlan,
    *,
    title: str = "Apply governed Zoe capability profile promotion",
    parent_issue_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfileMulticaHandoff:
    """Return a Multica ticket handoff packet for reviewed profile promotions."""

    blockers = _handoff_blockers(promotion_plan, patch_plan, title=title)
    base_metadata = _metadata(metadata)
    if blockers:
        return CapabilityProfileMulticaHandoff(
            title=title,
            description="",
            ticket_metadata={},
            promotion_manifest="",
            patch_text="",
            blockers=blockers,
            metadata=base_metadata,
        )

    promotion_manifest = render_capability_profile_promotion_manifest(promotion_plan)
    patch_text = render_capability_profile_patch(patch_plan)
    promoted_ids = promotion_plan.promoted_capability_ids
    human_description = _human_description(promoted_ids, patch_plan.target_path, patch_plan.source_sha256)
    ticket_metadata = {
        "profile_promotion": {
            "source": CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE,
            "target_path": patch_plan.target_path,
            "source_sha256": patch_plan.source_sha256,
            "promoted_capability_ids": list(promoted_ids),
            "promotion_manifest_sha256": _sha256(promotion_manifest),
            "patch_sha256": _sha256(patch_text),
            "promotion_metadata": promotion_plan.to_dict().get("metadata", {}),
            "patch_metadata": patch_plan.to_dict().get("metadata", {}),
        }
    }
    description = describe_ticket(
        human_description,
        zoe_kind="capability_profile_promotion",
        evidence_profile="code",
        engineering_mode="interactive",
        acceptance_criteria=[
            "Apply only the supplied capability-profile patch plan.",
            "Run focused capability-profile promotion and patch-writer tests.",
            "Attach PR, rollback, verification, and Greptile evidence before merge.",
        ],
        evidence_expectations=[
            "promotion_manifest_sha256",
            "patch_sha256",
            "source_sha256",
            "promoted_capability_ids",
        ],
        source=CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE,
        parent_issue_id=parent_issue_id,
        metadata=ticket_metadata,
    )
    parsed = parse_ticket_block(description)
    return CapabilityProfileMulticaHandoff(
        title=title,
        description=description,
        ticket_metadata=parsed,
        promotion_manifest=promotion_manifest,
        patch_text=patch_text,
        metadata=base_metadata,
    )


def render_capability_profile_multica_handoff_payload(handoff: CapabilityProfileMulticaHandoff) -> str:
    """Render the create-ticket payload only for createable handoffs."""

    if not handoff.allowed_to_create_ticket:
        raise ValueError("cannot render blocked capability profile Multica handoff")
    return json.dumps(
        {
            "title": handoff.title,
            "description": handoff.description,
            "promotion_manifest": handoff.promotion_manifest,
            "patch_text": handoff.patch_text,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def _handoff_blockers(
    promotion_plan: CapabilityProfilePromotionPlan,
    patch_plan: CapabilityProfilePatchPlan,
    *,
    title: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not title.strip():
        blockers.append("missing_title")
    if not promotion_plan.allowed_to_write_patch:
        blockers.append("promotion_plan_not_applyable")
    blockers.extend(promotion_plan.blockers)
    if not patch_plan.allowed_to_apply_patch:
        blockers.append("patch_plan_not_applyable")
    blockers.extend(patch_plan.blockers)
    if promotion_plan.target_path != patch_plan.target_path:
        blockers.append("target_path_mismatch")
    if promotion_plan.promoted_capability_ids != patch_plan.patched_capability_ids:
        blockers.append("capability_id_mismatch")
    if not patch_plan.source_sha256:
        blockers.append("missing_source_sha256")
    return tuple(dict.fromkeys(blockers))


def _human_description(promoted_ids: tuple[str, ...], target_path: str, source_sha256: str) -> str:
    return (
        "Apply a governed Zoe capability-profile promotion.\n\n"
        f"Target: `{target_path}`\n"
        f"Capabilities: {', '.join(promoted_ids)}\n"
        f"Source SHA-256: `{source_sha256}`\n\n"
        "This ticket is a handoff packet only. Apply the attached patch through a normal PR, "
        "run focused tests, keep rollback evidence, and do not mutate profiles directly."
    )


def _metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return {
        "source": CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE,
        "extra": dict(metadata or {}),
    }


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE",
    "CapabilityProfileMulticaHandoff",
    "build_capability_profile_multica_handoff",
    "render_capability_profile_multica_handoff_payload",
]
