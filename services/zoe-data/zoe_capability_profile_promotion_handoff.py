"""Orchestrate inert capability-profile promotion handoffs.

Runtime proposal callers should not know every profile-promotion sub-step and
should not create Multica tickets directly. This helper closes the pure contract
loop from reviewed trust result to promotion manifest, source patch plan, and
Multica handoff packet without writing files, mutating profiles, or calling
Multica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile_multica_handoff import (
    CapabilityProfileMulticaHandoff,
    build_capability_profile_multica_handoff,
)
from zoe_capability_profile_patch_writer import (
    CapabilityProfilePatchPlan,
    build_capability_profile_patch_plan,
)
from zoe_capability_profile_promotion import (
    DEFAULT_CAPABILITY_PROFILE_TARGET,
    CapabilityProfilePromotionPlan,
    build_capability_profile_promotion_plan,
)
from zoe_capability_trust_review import CapabilityTrustReviewResult


CAPABILITY_PROFILE_PROMOTION_HANDOFF_SOURCE = "capability_profile_promotion_handoff"


@dataclass(frozen=True)
class CapabilityProfilePromotionHandoffPlan:
    promotion_plan: CapabilityProfilePromotionPlan
    patch_plan: CapabilityProfilePatchPlan
    handoff: CapabilityProfileMulticaHandoff
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def allowed_to_handoff(self) -> bool:
        return self.handoff.allowed_to_create_ticket and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_handoff": self.allowed_to_handoff,
            "blockers": list(self.blockers),
            "promotion_plan": self.promotion_plan.to_dict(),
            "patch_plan": self.patch_plan.to_dict(),
            "handoff": self.handoff.to_dict(),
            "metadata": dict(self.metadata),
        }


def build_capability_profile_promotion_handoff_plan(
    review_result: CapabilityTrustReviewResult,
    *,
    source_text: str,
    pr_refs: Sequence[str],
    rollback_refs: Sequence[str],
    verification_refs: Sequence[str],
    title: str = "Apply governed Zoe capability profile promotion",
    parent_issue_id: str | None = None,
    target_path: str = DEFAULT_CAPABILITY_PROFILE_TARGET,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfilePromotionHandoffPlan:
    """Build a pure reviewed-promotion handoff plan for runtime callers."""

    plan_metadata = _metadata(metadata)
    promotion_plan = build_capability_profile_promotion_plan(
        review_result,
        pr_refs=pr_refs,
        rollback_refs=rollback_refs,
        verification_refs=verification_refs,
        target_path=target_path,
        metadata=metadata,
    )
    patch_plan = build_capability_profile_patch_plan(
        promotion_plan,
        source_text=source_text,
        metadata=metadata,
    )
    handoff = build_capability_profile_multica_handoff(
        promotion_plan,
        patch_plan,
        title=title,
        parent_issue_id=parent_issue_id,
        metadata=metadata,
    )
    blockers = _blockers(handoff)
    return CapabilityProfilePromotionHandoffPlan(
        promotion_plan=promotion_plan,
        patch_plan=patch_plan,
        handoff=handoff,
        blockers=blockers,
        metadata=plan_metadata,
    )


def _blockers(handoff: CapabilityProfileMulticaHandoff) -> tuple[str, ...]:
    blockers: list[str] = list(handoff.blockers)
    if not handoff.allowed_to_create_ticket:
        blockers.append("handoff_not_createable")
    return tuple(dict.fromkeys(blockers))


def _metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "source": CAPABILITY_PROFILE_PROMOTION_HANDOFF_SOURCE,
        "extra": dict(metadata or {}),
    }


__all__ = [
    "CAPABILITY_PROFILE_PROMOTION_HANDOFF_SOURCE",
    "CapabilityProfilePromotionHandoffPlan",
    "build_capability_profile_promotion_handoff_plan",
]
