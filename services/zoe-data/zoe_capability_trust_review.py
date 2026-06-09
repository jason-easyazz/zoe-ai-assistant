"""Govern reviewed capability-trust promotions.

This module is an in-memory reviewer/apply layer for trust-update candidates.
It does not write profile files, databases, MemoryService, or production chat
state. A later runtime writer can use these pure results as its input gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile import CapabilityProfile, capability_profile_index
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan


CAPABILITY_TRUST_REVIEW_SOURCE = "capability_trust_review"


@dataclass(frozen=True)
class CapabilityTrustReviewDecision:
    decision_id: str
    candidate: CapabilityTrustUpdateCandidate
    reviewer_id: str
    approved: bool
    reason: str
    approval_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        if not self.decision_id:
            raise ValueError("decision_id is required")
        if not self.reviewer_id:
            raise ValueError(f"{self.decision_id}: reviewer_id is required")
        if not self.reason:
            raise ValueError(f"{self.decision_id}: reason is required")
        if self.approved and not self.approval_refs:
            raise ValueError(f"{self.decision_id}: approved trust reviews require approval_refs")
        if self.approved and not self.evidence_refs:
            raise ValueError(f"{self.decision_id}: approved trust reviews require evidence_refs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate": self.candidate.to_dict(),
            "reviewer_id": self.reviewer_id,
            "approved": self.approved,
            "reason": self.reason,
            "approval_refs": list(self.approval_refs),
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CapabilityTrustReviewResult:
    decisions: tuple[CapabilityTrustReviewDecision, ...]
    profiles: tuple[CapabilityProfile, ...]
    blockers: tuple[str, ...] = ()

    @property
    def allowed_to_apply(self) -> bool:
        return any(decision.approved for decision in self.decisions) and not self.blockers

    @property
    def applied_capability_ids(self) -> tuple[str, ...]:
        return tuple(decision.candidate.capability_id for decision in self.decisions if decision.approved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_apply": self.allowed_to_apply,
            "applied_capability_ids": list(self.applied_capability_ids),
            "blockers": list(self.blockers),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "profiles": [profile.to_dict() for profile in self.profiles],
        }


def review_capability_trust_update_plan(
    plan: CapabilityTrustUpdatePlan,
    *,
    reviewer_id: str,
    approval_refs: Sequence[str],
    approved_capability_ids: Sequence[str],
    rejected_capability_ids: Sequence[str] = (),
    profiles: Sequence[CapabilityProfile],
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityTrustReviewResult:
    """Review and apply approved trust candidates to an in-memory profile set."""

    base_profiles = tuple(profiles)
    global_blockers = list(plan.blockers)
    if not reviewer_id:
        global_blockers.append("missing_reviewer_id")
    if not approval_refs:
        global_blockers.append("missing_approval_refs")
    if not plan.candidates:
        global_blockers.append("no_candidates")
    if not reviewer_id:
        return CapabilityTrustReviewResult(
            decisions=(),
            profiles=base_profiles,
            blockers=tuple(dict.fromkeys(global_blockers)),
        )

    profile_index = capability_profile_index(base_profiles)
    approved_ids = {str(capability_id) for capability_id in approved_capability_ids if str(capability_id)}
    rejected_ids = {str(capability_id) for capability_id in rejected_capability_ids if str(capability_id)}
    profile_updates: dict[str, CapabilityProfile] = {}
    decisions: list[CapabilityTrustReviewDecision] = []
    result_blockers: list[str] = list(global_blockers)
    candidate_global_blockers = tuple(blocker for blocker in global_blockers if blocker != "no_candidates")

    for candidate in plan.candidates:
        decision_blockers = candidate_global_blockers
        decision_blockers += _candidate_review_blockers(
            candidate,
            profile_index,
            approved_ids,
            rejected_ids,
        )
        if decision_blockers:
            result_blockers.extend(decision_blockers)
            decisions.append(
                _decision(
                    candidate,
                    reviewer_id=reviewer_id,
                    approved=False,
                    reason="; ".join(decision_blockers),
                    approval_refs=(),
                    metadata=metadata,
                )
            )
            continue

        try:
            updated_profile = _promoted_profile(profile_index[candidate.capability_id], candidate, approval_refs, metadata)
        except ValueError as exc:
            blocker = f"invalid_promoted_profile:{candidate.capability_id}"
            result_blockers.append(blocker)
            decisions.append(
                _decision(
                    candidate,
                    reviewer_id=reviewer_id,
                    approved=False,
                    reason=f"{blocker}: {exc}",
                    approval_refs=(),
                    metadata=metadata,
                )
            )
            continue
        profile_updates[candidate.capability_id] = updated_profile
        decisions.append(
            _decision(
                candidate,
                reviewer_id=reviewer_id,
                approved=True,
                reason=f"Approved trust promotion to {candidate.proposed_trust_level}.",
                approval_refs=approval_refs,
                metadata=metadata,
            )
        )

    updated_profiles = tuple(profile_updates.get(profile.capability_id, profile) for profile in base_profiles)
    return CapabilityTrustReviewResult(
        decisions=tuple(decisions),
        profiles=updated_profiles,
        blockers=tuple(dict.fromkeys(result_blockers)),
    )


def _candidate_review_blockers(
    candidate: CapabilityTrustUpdateCandidate,
    profile_index: Mapping[str, CapabilityProfile],
    approved_ids: set[str],
    rejected_ids: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    profile = profile_index.get(candidate.capability_id)
    if profile is None:
        blockers.append(f"unknown_capability_profile:{candidate.capability_id}")
    elif profile.trust_level != candidate.current_trust_level:
        blockers.append(f"stale_current_trust_level:{candidate.capability_id}")
    if candidate.capability_id in rejected_ids:
        blockers.append(f"review_rejected:{candidate.capability_id}")
    if candidate.capability_id not in approved_ids:
        blockers.append(f"not_approved:{candidate.capability_id}")
    if candidate.proposed_trust_level == candidate.current_trust_level:
        blockers.append(f"no_trust_change:{candidate.capability_id}")
    return tuple(blockers)


def _promoted_profile(
    profile: CapabilityProfile,
    candidate: CapabilityTrustUpdateCandidate,
    approval_refs: Sequence[str],
    metadata: Mapping[str, Any] | None,
) -> CapabilityProfile:
    promoted = replace(
        profile,
        trust_level=candidate.proposed_trust_level,
        evidence_refs=_merge(profile.evidence_refs, candidate.evidence_refs, approval_refs),
        metadata={
            **dict(profile.metadata),
            "trust_review": {
                "source": CAPABILITY_TRUST_REVIEW_SOURCE,
                "proposal_id": candidate.proposal_id,
                "source_event_id": candidate.source_event_id,
                "source_admission_id": candidate.source_admission_id,
                "retained_backend": candidate.retained_backend,
                "extra": dict(metadata or {}),
            },
        },
    )
    promoted.validate()
    return promoted


def _decision(
    candidate: CapabilityTrustUpdateCandidate,
    *,
    reviewer_id: str,
    approved: bool,
    reason: str,
    approval_refs: Sequence[str],
    metadata: Mapping[str, Any] | None,
) -> CapabilityTrustReviewDecision:
    return CapabilityTrustReviewDecision(
        decision_id=f"trust_review_{candidate.proposal_id}_{candidate.capability_id}",
        candidate=candidate,
        reviewer_id=reviewer_id,
        approved=approved,
        reason=reason,
        approval_refs=_merge(approval_refs),
        evidence_refs=_merge(candidate.evidence_refs, approval_refs),
        metadata={
            "source": CAPABILITY_TRUST_REVIEW_SOURCE,
            "extra": dict(metadata or {}),
        },
    )


def _merge(*groups: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        values.extend(str(value) for value in group if value is not None and str(value))
    return tuple(dict.fromkeys(values))


__all__ = [
    "CAPABILITY_TRUST_REVIEW_SOURCE",
    "CapabilityTrustReviewDecision",
    "CapabilityTrustReviewResult",
    "review_capability_trust_update_plan",
]
