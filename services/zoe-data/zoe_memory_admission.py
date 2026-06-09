"""Governed memory admission decisions for Zoe.

This module is intentionally inert. It evaluates whether a memory candidate
has enough scope, evidence, observation traces, and approval context to move
from "pending candidate" toward durable/trusted memory. It does not write to
MemoryService, Hindsight, Graphiti, or Multica.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from zoe_evolution_proposal import EvolutionProposal, ProposalStatus
from zoe_memory_contract import SELF_EVOLUTION_EVENT_TYPES, MemoryEvent, MemorySource
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType


class MemoryAdmissionError(ValueError):
    """Raised when a memory admission request is malformed."""


class MemoryAdmissionStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    BLOCKED = "blocked"
    APPROVED = "approved"


ADMITTED_WRITE_BACKENDS = {
    MemoryBackend.MEMPALACE.value,
    MemoryBackend.HINDSIGHT.value,
    MemoryBackend.GRAPHITI.value,
}

@dataclass(frozen=True)
class MemoryAdmissionRequest:
    admission_id: str
    candidate: MemoryEvent
    requested_by: str
    target_backends: tuple[str, ...]
    observation_traces: tuple[ObservationTrace, ...]
    approval_refs: tuple[str, ...] = ()
    proposal: EvolutionProposal | None = None
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if not self.admission_id:
            raise MemoryAdmissionError("admission_id is required")
        if not self.requested_by:
            raise MemoryAdmissionError(f"{self.admission_id}: requested_by is required")
        try:
            self.candidate.validate()
        except ValueError as exc:
            raise MemoryAdmissionError(f"{self.admission_id}: candidate is invalid: {exc}") from exc
        if not self.target_backends:
            raise MemoryAdmissionError(f"{self.admission_id}: target_backends are required")
        unsupported = set(self.target_backends) - ADMITTED_WRITE_BACKENDS
        if unsupported:
            raise MemoryAdmissionError(
                f"{self.admission_id}: target_backends are not admitted write targets: {sorted(unsupported)}"
            )
        for trace in self.observation_traces:
            try:
                trace.validate()
            except ValueError as exc:
                raise MemoryAdmissionError(f"{self.admission_id}: trace {trace.trace_id} is invalid: {exc}") from exc
            if trace.user_id != self.candidate.user_id:
                raise MemoryAdmissionError(
                    f"{self.admission_id}: trace {trace.trace_id} user_id must match candidate user_id"
                )
        if self.proposal is not None:
            try:
                self.proposal.validate()
            except ValueError as exc:
                raise MemoryAdmissionError(f"{self.admission_id}: proposal is invalid: {exc}") from exc
            if "memory_admission" not in self.proposal.approval_required:
                raise MemoryAdmissionError(
                    f"{self.admission_id}: proposal context must require memory_admission approval"
                )

    def evidence_refs(self) -> tuple[str, ...]:
        refs: list[str] = list(self.candidate.evidence_refs)
        for trace in self.observation_traces:
            refs.extend(trace.evidence_refs)
        if self.proposal is not None:
            refs.extend(self.proposal.evidence_refs)
        refs.extend(self.approval_refs)
        return tuple(dict.fromkeys(refs))


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    admission_id: str
    status: str
    allowed_to_keep_pending: bool
    allowed_to_write_durable: bool
    allowed_backends: tuple[str, ...]
    blockers: tuple[str, ...]
    required_approvals: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "status": self.status,
            "allowed_to_keep_pending": self.allowed_to_keep_pending,
            "allowed_to_write_durable": self.allowed_to_write_durable,
            "allowed_backends": list(self.allowed_backends),
            "blockers": list(self.blockers),
            "required_approvals": list(self.required_approvals),
            "evidence_refs": list(self.evidence_refs),
        }


def evaluate_memory_admission(request: MemoryAdmissionRequest) -> MemoryAdmissionDecision:
    """Return Zoe's memory admission decision without writing memory."""

    request.validate()
    blockers = _admission_blockers(request)
    allowed_to_write = not blockers
    status = MemoryAdmissionStatus.APPROVED.value if allowed_to_write else MemoryAdmissionStatus.BLOCKED.value
    soft_blockers = {"approval_required", "successful_admission_or_verification_trace_required"}
    if blockers and set(blockers).issubset(soft_blockers):
        status = MemoryAdmissionStatus.PENDING_REVIEW.value

    return MemoryAdmissionDecision(
        admission_id=request.admission_id,
        status=status,
        allowed_to_keep_pending=True,
        allowed_to_write_durable=allowed_to_write,
        allowed_backends=request.target_backends if allowed_to_write else (),
        blockers=tuple(blockers),
        required_approvals=tuple(_required_approvals(request)),
        evidence_refs=request.evidence_refs(),
    )


def _admission_blockers(request: MemoryAdmissionRequest) -> list[str]:
    blockers: list[str] = []
    if not request.approval_refs:
        blockers.append("approval_required")

    if not _has_successful_trace(
        request.observation_traces,
        ObservationTraceType.ADMISSION.value,
        ObservationTraceType.VERIFICATION.value,
    ):
        blockers.append("successful_admission_or_verification_trace_required")

    if _has_failed_trace(
        request.observation_traces,
        ObservationTraceType.ADMISSION.value,
        ObservationTraceType.VERIFICATION.value,
    ):
        blockers.append("failed_or_blocked_trace_present")

    if MemoryBackend.GRAPHITI.value in request.target_backends and not (
        request.candidate.relationships or request.candidate.supersedes
    ):
        blockers.append("graphiti_target_requires_relationship_or_supersession")

    requires_proposal = _requires_proposal_context(request.candidate)
    if requires_proposal and request.proposal is None:
        blockers.append("self_evolution_memory_requires_proposal_context")

    if request.proposal is not None and requires_proposal:
        if request.proposal.status not in {ProposalStatus.APPROVED.value, ProposalStatus.VERIFIED.value}:
            blockers.append("proposal_must_be_approved_or_verified")

    return list(dict.fromkeys(blockers))


def _required_approvals(request: MemoryAdmissionRequest) -> list[str]:
    approvals = ["memory_admission"]
    if _requires_proposal_context(request.candidate):
        approvals.append("self_evolution_proposal")
    if MemoryBackend.GRAPHITI.value in request.target_backends:
        approvals.append("relational_truth")
    return approvals


def _has_successful_trace(traces: Sequence[ObservationTrace], *trace_types: str) -> bool:
    return any(
        trace.trace_type in trace_types and trace.outcome == ObservationOutcome.SUCCESS.value
        for trace in traces
    )


def _has_failed_trace(traces: Sequence[ObservationTrace], *trace_types: str) -> bool:
    return any(
        trace.trace_type in trace_types
        and trace.outcome in {ObservationOutcome.FAILED.value, ObservationOutcome.BLOCKED.value}
        for trace in traces
    )


def _requires_proposal_context(candidate: MemoryEvent) -> bool:
    if candidate.source == MemorySource.PROPOSAL.value:
        return True
    return candidate.event_type in SELF_EVOLUTION_EVENT_TYPES


__all__ = [
    "MemoryAdmissionDecision",
    "MemoryAdmissionError",
    "MemoryAdmissionRequest",
    "MemoryAdmissionStatus",
    "evaluate_memory_admission",
]
