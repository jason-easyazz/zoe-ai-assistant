"""Compose retained outcomes into governed capability-profile handoff plans.

This module closes the pure self-evolution contract loop from an admitted and
retained proposal outcome to capability trust review and profile-promotion
handoff. It does not write profile files, create Multica tickets, mutate
runtime state, or retain memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES, CapabilityProfile
from zoe_capability_profile_promotion import DEFAULT_CAPABILITY_PROFILE_TARGET
from zoe_capability_profile_promotion_handoff import (
    CapabilityProfilePromotionHandoffPlan,
    build_capability_profile_promotion_handoff_plan,
)
from zoe_capability_trust_review import CapabilityTrustReviewResult, review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdatePlan, build_capability_trust_update_plan
from zoe_evolution_outcome_retain import EvolutionOutcomeRetainResult


CAPABILITY_OUTCOME_PROFILE_HANDOFF_SOURCE = "capability_outcome_profile_handoff"


@dataclass(frozen=True)
class CapabilityOutcomeProfileHandoffPlan:
    trust_update_plan: CapabilityTrustUpdatePlan
    review_result: CapabilityTrustReviewResult
    promotion_handoff_plan: CapabilityProfilePromotionHandoffPlan
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def allowed_to_handoff(self) -> bool:
        return self.promotion_handoff_plan.allowed_to_handoff and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_handoff": self.allowed_to_handoff,
            "blockers": list(self.blockers),
            "trust_update_plan": self.trust_update_plan.to_dict(),
            "review_result": self.review_result.to_dict(),
            "promotion_handoff_plan": self.promotion_handoff_plan.to_dict(),
            "metadata": dict(self.metadata),
        }


def build_capability_outcome_profile_handoff_plan(
    outcome_retain: EvolutionOutcomeRetainResult,
    *,
    source_text: str,
    reviewer_id: str,
    approval_refs: Sequence[str],
    approved_capability_ids: Sequence[str],
    rejected_capability_ids: Sequence[str] = (),
    pr_refs: Sequence[str],
    rollback_refs: Sequence[str],
    verification_refs: Sequence[str],
    profiles: Sequence[CapabilityProfile] = DEFAULT_CAPABILITY_PROFILES,
    title: str = "Apply governed Zoe capability profile promotion",
    parent_issue_id: str | None = None,
    target_path: str = DEFAULT_CAPABILITY_PROFILE_TARGET,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityOutcomeProfileHandoffPlan:
    """Build the full inert outcome-to-profile-promotion handoff chain."""

    plan_metadata = _metadata(metadata)
    trust_update_plan = build_capability_trust_update_plan(outcome_retain, profiles=profiles)
    review_result = review_capability_trust_update_plan(
        trust_update_plan,
        reviewer_id=reviewer_id,
        approval_refs=approval_refs,
        approved_capability_ids=approved_capability_ids,
        rejected_capability_ids=rejected_capability_ids,
        profiles=profiles,
        metadata=metadata,
    )
    promotion_handoff_plan = build_capability_profile_promotion_handoff_plan(
        review_result,
        source_text=source_text,
        pr_refs=pr_refs,
        rollback_refs=rollback_refs,
        verification_refs=verification_refs,
        title=title,
        parent_issue_id=parent_issue_id,
        target_path=target_path,
        metadata=metadata,
    )
    blockers = _blockers(trust_update_plan, review_result, promotion_handoff_plan)
    return CapabilityOutcomeProfileHandoffPlan(
        trust_update_plan=trust_update_plan,
        review_result=review_result,
        promotion_handoff_plan=promotion_handoff_plan,
        blockers=blockers,
        metadata=plan_metadata,
    )


def _blockers(
    trust_update_plan: CapabilityTrustUpdatePlan,
    review_result: CapabilityTrustReviewResult,
    promotion_handoff_plan: CapabilityProfilePromotionHandoffPlan,
) -> tuple[str, ...]:
    blockers: list[str] = []
    blockers.extend(trust_update_plan.blockers)
    blockers.extend(review_result.blockers)
    blockers.extend(promotion_handoff_plan.blockers)
    if not trust_update_plan.allowed_to_propose:
        blockers.append("trust_update_not_proposable")
    if not review_result.allowed_to_apply:
        blockers.append("trust_review_not_applyable")
    if not promotion_handoff_plan.allowed_to_handoff:
        blockers.append("profile_promotion_handoff_not_createable")
    return tuple(dict.fromkeys(blockers))


def _metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "source": CAPABILITY_OUTCOME_PROFILE_HANDOFF_SOURCE,
        "extra": dict(metadata or {}),
    }


__all__ = [
    "CAPABILITY_OUTCOME_PROFILE_HANDOFF_SOURCE",
    "CapabilityOutcomeProfileHandoffPlan",
    "build_capability_outcome_profile_handoff_plan",
]
