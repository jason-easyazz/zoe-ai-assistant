"""Build reviewed capability-trust update candidates from retained outcomes.

This module does not mutate capability profiles. It converts a verified,
admitted, and retained self-evolution outcome into reviewable trust-update
candidates that a later governed writer can accept or reject.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile import CapabilityProfile, capability_profile_index
from zoe_evolution_outcome_retain import EvolutionOutcomeRetainResult
from zoe_evolution_proposal import ProposalStatus


TRUST_UPDATE_SOURCE = "evolution_outcome_retain"


@dataclass(frozen=True)
class CapabilityTrustUpdateCandidate:
    capability_id: str
    proposal_id: str
    proposal_candidate_id: str
    current_trust_level: str
    proposed_trust_level: str
    reason: str
    evidence_refs: tuple[str, ...]
    source_event_id: str
    source_admission_id: str
    retained_backend: str
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not self.proposal_id:
            raise ValueError(f"{self.capability_id}: proposal_id is required")
        if not self.evidence_refs:
            raise ValueError(f"{self.capability_id}: evidence_refs are required")
        if not self.source_event_id:
            raise ValueError(f"{self.capability_id}: source_event_id is required")
        if not self.source_admission_id:
            raise ValueError(f"{self.capability_id}: source_admission_id is required")
        if not self.retained_backend:
            raise ValueError(f"{self.capability_id}: retained_backend is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "proposal_id": self.proposal_id,
            "proposal_candidate_id": self.proposal_candidate_id,
            "current_trust_level": self.current_trust_level,
            "proposed_trust_level": self.proposed_trust_level,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "source_event_id": self.source_event_id,
            "source_admission_id": self.source_admission_id,
            "retained_backend": self.retained_backend,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CapabilityTrustUpdatePlan:
    candidates: tuple[CapabilityTrustUpdateCandidate, ...]
    blockers: tuple[str, ...] = ()

    @property
    def allowed_to_propose(self) -> bool:
        return bool(self.candidates) and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_propose": self.allowed_to_propose,
            "blockers": list(self.blockers),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def build_capability_trust_update_plan(
    outcome_retain: EvolutionOutcomeRetainResult,
    *,
    profiles: Sequence[CapabilityProfile] | None = None,
) -> CapabilityTrustUpdatePlan:
    """Build reviewable trust-update candidates from a retained verified outcome."""

    blockers = _trust_update_blockers(outcome_retain)
    if blockers:
        return CapabilityTrustUpdatePlan(candidates=(), blockers=blockers)

    proposal = outcome_retain.admission.request.proposal
    if proposal is None:
        return CapabilityTrustUpdatePlan(candidates=(), blockers=("missing_proposal",))

    profile_index = capability_profile_index(profiles) if profiles is not None else capability_profile_index()
    source_event_id = outcome_retain.admission.candidate.event_id
    retained_backend = str(outcome_retain.execution.sidecar_result.get("bank_id") or "hindsight")
    evidence_refs = _evidence_refs(outcome_retain)
    candidates = tuple(
        CapabilityTrustUpdateCandidate(
            capability_id=capability_id,
            proposal_id=proposal.proposal_id,
            proposal_candidate_id=proposal.candidate.candidate_id,
            current_trust_level=profile_index[capability_id].trust_level if capability_id in profile_index else "unknown",
            proposed_trust_level="trusted",
            reason=(
                f"Verified proposal {proposal.proposal_id} was admitted and retained "
                f"for capability {capability_id}."
            ),
            evidence_refs=evidence_refs,
            source_event_id=source_event_id,
            source_admission_id=outcome_retain.admission.request.admission_id,
            retained_backend=retained_backend,
            metadata={
                "source": TRUST_UPDATE_SOURCE,
                "proposal_status": proposal.status,
                "outcome_reason": outcome_retain.reason,
                "execution_attempted": outcome_retain.execution.attempted,
                "retained": outcome_retain.retained,
            },
        )
        for capability_id in proposal.affected_capabilities
    )
    return CapabilityTrustUpdatePlan(candidates=candidates)


def _trust_update_blockers(outcome_retain: EvolutionOutcomeRetainResult) -> tuple[str, ...]:
    blockers: list[str] = []
    if not outcome_retain.retained:
        blockers.append("outcome_not_retained")
    if outcome_retain.execution is None:
        blockers.append("missing_retain_execution")
    proposal = outcome_retain.admission.request.proposal
    if proposal is None:
        blockers.append("missing_proposal")
    elif proposal.status != ProposalStatus.VERIFIED.value:
        blockers.append("proposal_not_verified")
    if not outcome_retain.admission.decision.allowed_to_write_durable:
        blockers.append("admission_not_durable")
    return tuple(dict.fromkeys(blockers))


def _evidence_refs(outcome_retain: EvolutionOutcomeRetainResult) -> tuple[str, ...]:
    refs: list[str] = []
    refs.extend(outcome_retain.admission.decision.evidence_refs)
    refs.extend(outcome_retain.admission.candidate.evidence_refs)
    if outcome_retain.execution is not None:
        refs.extend(outcome_retain.execution.evidence_refs)
        refs.append(f"hindsight:{outcome_retain.execution.bank_id}:{outcome_retain.execution.event_id}")
    return tuple(dict.fromkeys(ref for ref in refs if ref))


__all__ = [
    "CapabilityTrustUpdateCandidate",
    "CapabilityTrustUpdatePlan",
    "TRUST_UPDATE_SOURCE",
    "build_capability_trust_update_plan",
]
