"""Route Zoe evolution outcome memory candidates through admission gates.

This module is intentionally inert. It combines the terminal-proposal outcome
memory builder with the existing memory admission contract, returning the
candidate, request, and decision without writing to MemoryService, Hindsight,
Graphiti, MemPalace, or Multica.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from zoe_evolution_outcome_memory import build_evolution_outcome_memory_event
from zoe_evolution_proposal import EvolutionProposal
from zoe_memory_admission import MemoryAdmissionDecision, MemoryAdmissionRequest, evaluate_memory_admission
from zoe_memory_contract import MemoryEvent
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationTrace


EVOLUTION_OUTCOME_ADMISSION_SOURCE = "evolution_outcome_memory"
DEFAULT_EVOLUTION_OUTCOME_TARGET_BACKENDS = (MemoryBackend.HINDSIGHT.value,)


@dataclass(frozen=True)
class EvolutionOutcomeAdmissionResult:
    candidate: MemoryEvent
    request: MemoryAdmissionRequest
    decision: MemoryAdmissionDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "request": {
                "admission_id": self.request.admission_id,
                "requested_by": self.request.requested_by,
                "target_backends": list(self.request.target_backends),
                "approval_refs": list(self.request.approval_refs),
                "trace_ids": [trace.trace_id for trace in self.request.observation_traces],
                "proposal_id": self.request.proposal.proposal_id if self.request.proposal else None,
                "metadata": dict(self.request.metadata or {}),
            },
            "decision": self.decision.to_dict(),
        }


def build_evolution_outcome_admission_request(
    proposal: EvolutionProposal,
    traces: Sequence[ObservationTrace],
    *,
    admission_id: str | None = None,
    requested_by: str = EVOLUTION_OUTCOME_ADMISSION_SOURCE,
    target_backends: Sequence[str] = DEFAULT_EVOLUTION_OUTCOME_TARGET_BACKENDS,
    approval_refs: Sequence[str] = (),
    user_id: str | None = None,
    scope: str | None = None,
    event_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MemoryAdmissionRequest:
    """Build the admission request for an evolution outcome memory candidate."""

    candidate = build_evolution_outcome_memory_event(
        proposal,
        traces,
        user_id=user_id,
        scope=scope,
        event_id=event_id,
    )
    request = MemoryAdmissionRequest(
        admission_id=admission_id or f"admit_evolution_outcome_{proposal.proposal_id}",
        candidate=candidate,
        requested_by=requested_by,
        target_backends=_clean_sequence(target_backends),
        observation_traces=tuple(traces),
        approval_refs=_clean_sequence(approval_refs),
        proposal=proposal,
        metadata={
            "source": EVOLUTION_OUTCOME_ADMISSION_SOURCE,
            "proposal_id": proposal.proposal_id,
            "candidate_event_id": candidate.event_id,
            "outcome_status": proposal.status,
            "extra": dict(metadata or {}),
        },
    )
    request.validate()
    return request


def evaluate_evolution_outcome_admission(
    proposal: EvolutionProposal,
    traces: Sequence[ObservationTrace],
    *,
    admission_id: str | None = None,
    requested_by: str = EVOLUTION_OUTCOME_ADMISSION_SOURCE,
    target_backends: Sequence[str] = DEFAULT_EVOLUTION_OUTCOME_TARGET_BACKENDS,
    approval_refs: Sequence[str] = (),
    user_id: str | None = None,
    scope: str | None = None,
    event_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EvolutionOutcomeAdmissionResult:
    """Evaluate outcome memory admission without promoting durable memory."""

    request = build_evolution_outcome_admission_request(
        proposal,
        traces,
        admission_id=admission_id,
        requested_by=requested_by,
        target_backends=target_backends,
        approval_refs=approval_refs,
        user_id=user_id,
        scope=scope,
        event_id=event_id,
        metadata=metadata,
    )
    decision = evaluate_memory_admission(request)
    return EvolutionOutcomeAdmissionResult(candidate=request.candidate, request=request, decision=decision)


def _clean_sequence(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(item) for item in values if item is not None and str(item))


__all__ = [
    "DEFAULT_EVOLUTION_OUTCOME_TARGET_BACKENDS",
    "EVOLUTION_OUTCOME_ADMISSION_SOURCE",
    "EvolutionOutcomeAdmissionResult",
    "build_evolution_outcome_admission_request",
    "evaluate_evolution_outcome_admission",
]
