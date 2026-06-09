"""Build explicit profile-promotion write plans.

This module is the last pure gate before any future capability-profile writer.
It converts an already-governed trust review result into a deterministic
promotion manifest that can be attached to a PR. It does not edit files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile import CapabilityProfile, capability_profile_index
from zoe_capability_utils import merge_string_refs
from zoe_capability_trust_review import CapabilityTrustReviewResult


CAPABILITY_PROFILE_PROMOTION_SOURCE = "capability_profile_promotion_plan"
DEFAULT_CAPABILITY_PROFILE_TARGET = "services/zoe-data/zoe_capability_profile.py"


@dataclass(frozen=True)
class CapabilityProfilePromotionRecord:
    capability_id: str
    from_trust_level: str
    to_trust_level: str
    target_path: str
    decision_id: str
    proposal_id: str
    approval_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    pr_refs: tuple[str, ...]
    rollback_refs: tuple[str, ...]
    verification_refs: tuple[str, ...]
    profile_snapshot: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_snapshot", MappingProxyType(dict(self.profile_snapshot)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not self.target_path:
            raise ValueError(f"{self.capability_id}: target_path is required")
        if not self.decision_id:
            raise ValueError(f"{self.capability_id}: decision_id is required")
        if not self.proposal_id:
            raise ValueError(f"{self.capability_id}: proposal_id is required")
        if not self.approval_refs:
            raise ValueError(f"{self.capability_id}: approval_refs are required")
        if not self.evidence_refs:
            raise ValueError(f"{self.capability_id}: evidence_refs are required")
        if not self.pr_refs:
            raise ValueError(f"{self.capability_id}: pr_refs are required")
        if not self.rollback_refs:
            raise ValueError(f"{self.capability_id}: rollback_refs are required")
        if not self.verification_refs:
            raise ValueError(f"{self.capability_id}: verification_refs are required")
        if self.from_trust_level == self.to_trust_level:
            raise ValueError(f"{self.capability_id}: promotion must change trust level")
        if self.profile_snapshot.get("capability_id") != self.capability_id:
            raise ValueError(f"{self.capability_id}: profile_snapshot capability_id mismatch")
        if self.profile_snapshot.get("trust_level") != self.to_trust_level:
            raise ValueError(f"{self.capability_id}: profile_snapshot trust_level mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_trust_level": self.from_trust_level,
            "to_trust_level": self.to_trust_level,
            "target_path": self.target_path,
            "decision_id": self.decision_id,
            "proposal_id": self.proposal_id,
            "approval_refs": list(self.approval_refs),
            "evidence_refs": list(self.evidence_refs),
            "pr_refs": list(self.pr_refs),
            "rollback_refs": list(self.rollback_refs),
            "verification_refs": list(self.verification_refs),
            "profile_snapshot": dict(self.profile_snapshot),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CapabilityProfilePromotionPlan:
    target_path: str
    records: tuple[CapabilityProfilePromotionRecord, ...]
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.blockers and self.records:
            raise ValueError("blocked profile promotion plans cannot carry records")

    @property
    def allowed_to_write_patch(self) -> bool:
        return bool(self.records) and not self.blockers

    @property
    def promoted_capability_ids(self) -> tuple[str, ...]:
        if self.blockers:
            return ()
        return tuple(record.capability_id for record in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_write_patch": self.allowed_to_write_patch,
            "target_path": self.target_path,
            "promoted_capability_ids": list(self.promoted_capability_ids),
            "blockers": list(self.blockers),
            "records": [record.to_dict() for record in self.records],
            "metadata": dict(self.metadata),
        }


def build_capability_profile_promotion_plan(
    review_result: CapabilityTrustReviewResult,
    *,
    pr_refs: Sequence[str],
    rollback_refs: Sequence[str],
    verification_refs: Sequence[str],
    target_path: str = DEFAULT_CAPABILITY_PROFILE_TARGET,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfilePromotionPlan:
    """Build a deterministic manifest for a future profile writer."""

    blockers = _promotion_blockers(
        review_result,
        pr_refs=pr_refs,
        rollback_refs=rollback_refs,
        verification_refs=verification_refs,
        target_path=target_path,
    )
    if blockers:
        return CapabilityProfilePromotionPlan(
            target_path=target_path,
            records=(),
            blockers=blockers,
            metadata=_metadata(metadata),
        )

    profile_index = capability_profile_index(review_result.profiles)
    records: list[CapabilityProfilePromotionRecord] = []
    for decision in review_result.decisions:
        if not decision.approved:
            continue
        profile = profile_index[decision.candidate.capability_id]
        records.append(
            CapabilityProfilePromotionRecord(
                capability_id=decision.candidate.capability_id,
                from_trust_level=decision.candidate.current_trust_level,
                to_trust_level=decision.candidate.proposed_trust_level,
                target_path=target_path,
                decision_id=decision.decision_id,
                proposal_id=decision.candidate.proposal_id,
                approval_refs=merge_string_refs(decision.approval_refs),
                evidence_refs=merge_string_refs(decision.evidence_refs, decision.candidate.evidence_refs),
                pr_refs=merge_string_refs(pr_refs),
                rollback_refs=merge_string_refs(rollback_refs),
                verification_refs=merge_string_refs(verification_refs),
                profile_snapshot=profile.to_dict(),
                metadata={
                    "source": CAPABILITY_PROFILE_PROMOTION_SOURCE,
                    "reviewer_id": decision.reviewer_id,
                    "source_event_id": decision.candidate.source_event_id,
                    "source_admission_id": decision.candidate.source_admission_id,
                    "retained_backend": decision.candidate.retained_backend,
                    "extra": dict(metadata or {}),
                },
            )
        )

    return CapabilityProfilePromotionPlan(
        target_path=target_path,
        records=tuple(records),
        metadata=_metadata(metadata),
    )


def render_capability_profile_promotion_manifest(plan: CapabilityProfilePromotionPlan) -> str:
    """Render a deterministic JSON manifest for PR evidence."""

    if not plan.allowed_to_write_patch:
        raise ValueError("cannot render profile promotion manifest for blocked plan")
    return json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n"


def _promotion_blockers(
    review_result: CapabilityTrustReviewResult,
    *,
    pr_refs: Sequence[str],
    rollback_refs: Sequence[str],
    verification_refs: Sequence[str],
    target_path: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not review_result.allowed_to_apply:
        blockers.append("review_not_applyable")
    blockers.extend(review_result.blockers)
    if not review_result.profiles:
        blockers.append("missing_review_profiles")
    if not review_result.applied_capability_ids:
        blockers.append("missing_applied_capability_ids")
    if not pr_refs:
        blockers.append("missing_pr_refs")
    if not rollback_refs:
        blockers.append("missing_rollback_refs")
    if not verification_refs:
        blockers.append("missing_verification_refs")
    if not target_path:
        blockers.append("missing_target_path")

    try:
        profile_index = capability_profile_index(review_result.profiles)
    except ValueError:
        blockers.append("invalid_review_profiles")
        profile_index = {}

    for decision in review_result.decisions:
        if not decision.approved:
            continue
        profile = profile_index.get(decision.candidate.capability_id)
        if profile is None:
            blockers.append(f"missing_promoted_profile:{decision.candidate.capability_id}")
            continue
        if profile.trust_level != decision.candidate.proposed_trust_level:
            blockers.append(f"promoted_profile_trust_mismatch:{decision.candidate.capability_id}")
        if decision.candidate.capability_id not in review_result.applied_capability_ids:
            blockers.append(f"decision_not_applied:{decision.candidate.capability_id}")
    return tuple(dict.fromkeys(blockers))


def _metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return {
        "source": CAPABILITY_PROFILE_PROMOTION_SOURCE,
        "extra": dict(metadata or {}),
    }


__all__ = [
    "CAPABILITY_PROFILE_PROMOTION_SOURCE",
    "DEFAULT_CAPABILITY_PROFILE_TARGET",
    "CapabilityProfilePromotionPlan",
    "CapabilityProfilePromotionRecord",
    "build_capability_profile_promotion_plan",
    "render_capability_profile_promotion_manifest",
]
