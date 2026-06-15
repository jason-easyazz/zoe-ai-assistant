"""Zoe self-evolution proposal contract.

This module keeps the Notice -> Explain -> Search -> Evaluate -> Propose
record boring and reviewable before any code execution or tool install occurs.
It deliberately does not execute proposals; it only validates that a proposal
has enough evidence, candidate scoring, approval gates, tests, and rollback
context to be handed to Multica or a human reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from zoe_candidate_scoring import CandidateEvaluation, adoption_gate


class EvolutionSignalType(str, Enum):
    USER_REQUEST = "user_request"
    REPEATED_FAILURE = "repeated_failure"
    TOOL_GAP = "tool_gap"
    STALE_CAPABILITY = "stale_capability"
    OUTCOME_EVAL_FAILURE = "outcome_eval_failure"
    OPERATOR_NOTE = "operator_note"


class TrustAutonomyClass(str, Enum):
    OBSERVE = "observe"
    RECALL = "recall"
    SUGGEST = "suggest"
    PREPARE = "prepare"
    EXECUTE = "execute"
    PROMOTE = "promote"


class ProposalRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PRIVILEGED = "privileged"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    VERIFIED = "verified"
    FAILED = "failed"
    RETIRED = "retired"


PRIVILEGED_APPROVAL_CLASSES = {
    "device_action_confirmation",
    "install_or_runtime_change",
    "license_review",
    "memory_admission",
    "pr_evidence",
    "secret_access",
    "security_review",
    "sidecar_start",
    "user_or_admin_for_privileged_execution",
}


@dataclass(frozen=True)
class EvolutionSignal:
    signal_id: str
    signal_type: str
    summary: str
    source: str
    evidence_refs: tuple[str, ...]
    user_id: str | None = None
    scope: str = "system"
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if not self.signal_id:
            raise ValueError("signal_id is required")
        if self.signal_type not in {item.value for item in EvolutionSignalType}:
            raise ValueError(f"{self.signal_id}: unknown signal_type {self.signal_type!r}")
        if not self.summary:
            raise ValueError(f"{self.signal_id}: summary is required")
        if not self.source:
            raise ValueError(f"{self.signal_id}: source is required")
        if not self.scope:
            raise ValueError(f"{self.signal_id}: scope is required")
        if not self.evidence_refs:
            raise ValueError(f"{self.signal_id}: evidence_refs are required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "summary": self.summary,
            "source": self.source,
            "evidence_refs": list(self.evidence_refs),
            "user_id": self.user_id,
            "scope": self.scope,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class EvolutionProposal:
    proposal_id: str
    title: str
    problem_statement: str
    signals: tuple[EvolutionSignal, ...]
    candidate: CandidateEvaluation
    affected_capabilities: tuple[str, ...]
    autonomy_class: str
    risk: str
    expected_benefit: str
    verification_plan: tuple[str, ...]
    rollback_plan: str
    evidence_refs: tuple[str, ...]
    approval_required: tuple[str, ...] = ()
    status: str = ProposalStatus.DRAFT.value
    multica_issue_id: str | None = None
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id is required")
        if not self.title:
            raise ValueError(f"{self.proposal_id}: title is required")
        if not self.problem_statement:
            raise ValueError(f"{self.proposal_id}: problem_statement is required")
        if not self.signals:
            raise ValueError(f"{self.proposal_id}: at least one signal is required")
        for signal in self.signals:
            signal.validate()
        self.candidate.validate()
        if not self.affected_capabilities:
            raise ValueError(f"{self.proposal_id}: affected_capabilities are required")
        if self.autonomy_class not in {item.value for item in TrustAutonomyClass}:
            raise ValueError(f"{self.proposal_id}: unknown autonomy_class {self.autonomy_class!r}")
        if self.risk not in {item.value for item in ProposalRisk}:
            raise ValueError(f"{self.proposal_id}: unknown risk {self.risk!r}")
        if self.status not in {item.value for item in ProposalStatus}:
            raise ValueError(f"{self.proposal_id}: unknown status {self.status!r}")
        if not self.expected_benefit:
            raise ValueError(f"{self.proposal_id}: expected_benefit is required")
        if not self.verification_plan:
            raise ValueError(f"{self.proposal_id}: verification_plan is required")
        if not self.rollback_plan:
            raise ValueError(f"{self.proposal_id}: rollback_plan is required")
        if not self.evidence_refs:
            raise ValueError(f"{self.proposal_id}: evidence_refs are required")
        unknown_classes = set(self.approval_required) - PRIVILEGED_APPROVAL_CLASSES
        if unknown_classes:
            raise ValueError(
                f"{self.proposal_id}: unknown approval class(es) in approval_required: {sorted(unknown_classes)}"
            )
        if self.autonomy_class in {TrustAutonomyClass.EXECUTE.value, TrustAutonomyClass.PROMOTE.value}:
            if not self.approval_required:
                raise ValueError(f"{self.proposal_id}: execute/promote proposals require approval_required")
            if "pr_evidence" not in self.approval_required:
                raise ValueError(f"{self.proposal_id}: execute/promote proposals require pr_evidence approval")
        if self.risk in {ProposalRisk.HIGH.value, ProposalRisk.PRIVILEGED.value}:
            if not self.approval_required:
                raise ValueError(f"{self.proposal_id}: high-risk proposals require approval_required")
            if "user_or_admin_for_privileged_execution" not in self.approval_required:
                raise ValueError(
                    f"{self.proposal_id}: high-risk proposals require user_or_admin_for_privileged_execution approval"
                )
        if self.status in {ProposalStatus.APPROVED.value, ProposalStatus.VERIFIED.value}:
            if not self.approval_required:
                raise ValueError(f"{self.proposal_id}: approved/verified proposals require approval evidence gates")

    def approval_gate(self) -> dict[str, Any]:
        self.validate()
        candidate_gate = adoption_gate(self.candidate)
        blockers = list(candidate_gate["blockers"])
        return {
            "proposal_id": self.proposal_id,
            "allowed_to_prepare": candidate_gate["allowed"] and not blockers,
            "allowed_to_execute": False,
            "blockers": blockers,
            "candidate_gate": candidate_gate,
            "approval_required": list(self.approval_required),
        }

    def to_dict(self) -> dict[str, Any]:
        approval_gate = self.approval_gate()
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "problem_statement": self.problem_statement,
            "signals": [signal.to_dict() for signal in self.signals],
            "candidate": self.candidate.to_dict(),
            "affected_capabilities": list(self.affected_capabilities),
            "autonomy_class": self.autonomy_class,
            "risk": self.risk,
            "expected_benefit": self.expected_benefit,
            "verification_plan": list(self.verification_plan),
            "rollback_plan": self.rollback_plan,
            "evidence_refs": list(self.evidence_refs),
            "approval_required": list(self.approval_required),
            "status": self.status,
            "multica_issue_id": self.multica_issue_id,
            "approval_gate": approval_gate,
            "metadata": dict(self.metadata or {}),
        }


def build_evolution_proposal(
    *,
    proposal_id: str,
    title: str,
    problem_statement: str,
    signals: Sequence[EvolutionSignal],
    candidate: CandidateEvaluation,
    affected_capabilities: Sequence[str],
    autonomy_class: str,
    risk: str,
    expected_benefit: str,
    verification_plan: Sequence[str],
    rollback_plan: str,
    approval_required: Sequence[str] = (),
    status: str = ProposalStatus.DRAFT.value,
    multica_issue_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EvolutionProposal:
    evidence_refs: list[str] = []
    for signal in signals:
        evidence_refs.extend(signal.evidence_refs)
    evidence_refs.extend(candidate.evidence_refs)

    proposal = EvolutionProposal(
        proposal_id=proposal_id,
        title=title,
        problem_statement=problem_statement,
        signals=tuple(signals),
        candidate=candidate,
        affected_capabilities=tuple(affected_capabilities),
        autonomy_class=autonomy_class,
        risk=risk,
        expected_benefit=expected_benefit,
        verification_plan=tuple(verification_plan),
        rollback_plan=rollback_plan,
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        approval_required=tuple(approval_required),
        status=status,
        multica_issue_id=multica_issue_id,
        metadata=metadata,
    )
    proposal.validate()
    return proposal


__all__ = [
    "EvolutionProposal",
    "EvolutionSignal",
    "EvolutionSignalType",
    "PRIVILEGED_APPROVAL_CLASSES",
    "ProposalRisk",
    "ProposalStatus",
    "TrustAutonomyClass",
    "build_evolution_proposal",
]
