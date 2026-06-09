"""Build pending memory events from terminal Zoe evolution outcomes.

This module is intentionally inert. It converts verified, failed, or retired
self-evolution proposals plus observation evidence into Zoe `MemoryEvent`
candidates. It does not write to MemoryService, Hindsight, Graphiti, or
capability profiles.
"""

from __future__ import annotations

from typing import Sequence

from zoe_evolution_proposal import EvolutionProposal, ProposalStatus
from zoe_memory_contract import (
    MemoryEvent,
    MemoryEventType,
    MemoryRelationship,
    MemoryScope,
    MemorySource,
    RelationshipType,
)
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType


TERMINAL_PROPOSAL_STATUSES = {
    ProposalStatus.VERIFIED.value,
    ProposalStatus.FAILED.value,
    ProposalStatus.RETIRED.value,
}


class EvolutionOutcomeMemoryError(ValueError):
    """Raised when an evolution outcome cannot become a safe memory candidate."""


def build_evolution_outcome_memory_event(
    proposal: EvolutionProposal,
    traces: Sequence[ObservationTrace],
    *,
    user_id: str | None = None,
    scope: str | None = None,
    event_id: str | None = None,
) -> MemoryEvent:
    """Return a pending self-evolution memory candidate for a terminal proposal."""

    proposal.validate()
    if proposal.status not in TERMINAL_PROPOSAL_STATUSES:
        raise EvolutionOutcomeMemoryError(
            f"{proposal.proposal_id}: proposal status must be terminal before outcome memory"
        )
    if not traces:
        raise EvolutionOutcomeMemoryError(f"{proposal.proposal_id}: at least one outcome trace is required")

    for trace in traces:
        trace.validate()

    resolved_user_id = user_id or _proposal_user_id(proposal)
    if not resolved_user_id:
        raise EvolutionOutcomeMemoryError(f"{proposal.proposal_id}: user_id is required for outcome memory")

    resolved_scope = scope or _proposal_scope(proposal)
    if resolved_scope not in {item.value for item in MemoryScope}:
        raise EvolutionOutcomeMemoryError(f"{proposal.proposal_id}: unsupported memory scope {resolved_scope!r}")

    _validate_trace_outcome(proposal, traces)

    evidence_refs = _evidence_refs(proposal, traces)
    event = MemoryEvent(
        event_id=event_id or f"mem_evt_evolution_outcome_{proposal.proposal_id}",
        user_id=resolved_user_id,
        scope=resolved_scope,
        source=MemorySource.TRACE.value,
        event_type=_event_type_for_status(proposal.status),
        content=_content_for_outcome(proposal, traces),
        entities=tuple(dict.fromkeys((proposal.proposal_id, proposal.candidate.candidate_id, *proposal.affected_capabilities))),
        relationships=_relationships_for_outcome(proposal),
        evidence_refs=evidence_refs,
        confidence=_confidence_for_traces(traces),
        metadata={
            "proposal_id": proposal.proposal_id,
            "proposal_status": proposal.status,
            "candidate_id": proposal.candidate.candidate_id,
            "affected_capabilities": list(proposal.affected_capabilities),
            "trace_ids": [trace.trace_id for trace in traces],
        },
    )
    return event.validated()


def _proposal_user_id(proposal: EvolutionProposal) -> str | None:
    for signal in proposal.signals:
        if signal.user_id:
            return signal.user_id
    return None


def _proposal_scope(proposal: EvolutionProposal) -> str:
    for signal in proposal.signals:
        if signal.scope:
            return signal.scope
    return MemoryScope.PROJECT.value


def _validate_trace_outcome(proposal: EvolutionProposal, traces: Sequence[ObservationTrace]) -> None:
    if proposal.status == ProposalStatus.VERIFIED.value:
        if not any(
            trace.trace_type == ObservationTraceType.VERIFICATION.value
            and trace.outcome == ObservationOutcome.SUCCESS.value
            for trace in traces
        ):
            raise EvolutionOutcomeMemoryError(
                f"{proposal.proposal_id}: verified proposals require a successful verification trace"
            )
        return

    if proposal.status == ProposalStatus.FAILED.value:
        if not any(
            trace.outcome in {ObservationOutcome.FAILED.value, ObservationOutcome.BLOCKED.value}
            and trace.trace_type in {ObservationTraceType.VERIFICATION.value, ObservationTraceType.OUTCOME_EVAL.value}
            for trace in traces
        ):
            raise EvolutionOutcomeMemoryError(
                f"{proposal.proposal_id}: failed proposals require failed or blocked verification/outcome evidence"
            )
        return

    if proposal.status == ProposalStatus.RETIRED.value:
        if not any(
            trace.trace_type in {ObservationTraceType.VERIFICATION.value, ObservationTraceType.OUTCOME_EVAL.value}
            and trace.outcome in {ObservationOutcome.SUCCESS.value, ObservationOutcome.PARTIAL.value}
            for trace in traces
        ):
            raise EvolutionOutcomeMemoryError(
                f"{proposal.proposal_id}: retired proposals require retirement verification/outcome evidence"
            )


def _event_type_for_status(status: str) -> str:
    if status == ProposalStatus.VERIFIED.value:
        return MemoryEventType.FIX.value
    if status == ProposalStatus.FAILED.value:
        return MemoryEventType.FAILURE.value
    return MemoryEventType.CAPABILITY.value


def _content_for_outcome(proposal: EvolutionProposal, traces: Sequence[ObservationTrace]) -> str:
    latest = max(traces, key=lambda trace: trace.created_at)
    return (
        f"Zoe evolution proposal {proposal.proposal_id} ended as {proposal.status}: "
        f"{proposal.title}. Latest evidence: {latest.summary}"
    )


def _relationships_for_outcome(proposal: EvolutionProposal) -> tuple[MemoryRelationship, ...]:
    if proposal.status == ProposalStatus.VERIFIED.value:
        relationship = RelationshipType.TRUSTED_FOR.value
    elif proposal.status == ProposalStatus.FAILED.value:
        relationship = RelationshipType.FAILED_ON.value
    else:
        relationship = RelationshipType.SUPERSEDES.value

    relationships: list[MemoryRelationship] = [
        MemoryRelationship(
            relationship_type=RelationshipType.EVIDENCED_BY.value,
            source=proposal.proposal_id,
            target=ref,
        )
        for ref in proposal.evidence_refs
    ]
    relationships.extend(
        MemoryRelationship(
            relationship_type=relationship,
            source=proposal.candidate.candidate_id,
            target=capability,
        )
        for capability in proposal.affected_capabilities
    )
    return tuple(relationships)


def _evidence_refs(proposal: EvolutionProposal, traces: Sequence[ObservationTrace]) -> tuple[str, ...]:
    refs: list[str] = list(proposal.evidence_refs)
    for trace in traces:
        refs.extend(trace.evidence_refs)
    return tuple(dict.fromkeys(refs))


def _confidence_for_traces(traces: Sequence[ObservationTrace]) -> float:
    confidences = [trace.confidence for trace in traces if trace.confidence is not None]
    if not confidences:
        return 0.5
    return min(confidences)


__all__ = [
    "EvolutionOutcomeMemoryError",
    "TERMINAL_PROPOSAL_STATUSES",
    "build_evolution_outcome_memory_event",
]
