"""Runtime intake helpers for Zoe self-evolution proposals.

This module closes the Notice -> Search -> Evaluate -> Propose contract for
runtime callers without performing any durable side effects. Callers provide an
evidence-bearing signal plus searched/scored candidates; the helper returns the
legacy `evolution_proposals` row shape and a validated proposal snapshot that
existing Multica/review gates can inspect later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from zoe_candidate_scoring import CandidateEvaluation, rank_candidates
from zoe_evolution_proposal import (
    EvolutionSignal,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)
from zoe_evolution_proposal_adapter import (
    CONTRACT_ENVELOPE_VERSION,
    build_existing_zoe_proposal_candidate,
    legacy_signal_type_for_proposal_type,
    normalize_mcp_evolution_proposal_type,
)

RUNTIME_INTAKE_SOURCE = "runtime_evolution_intake"
_SCHEMA = "zoe_evolution_proposal"

_RISK_APPROVALS = {
    ProposalRisk.LOW.value: (),
    ProposalRisk.MEDIUM.value: (),
    ProposalRisk.HIGH.value: ("user_or_admin_for_privileged_execution",),
    ProposalRisk.PRIVILEGED.value: ("user_or_admin_for_privileged_execution", "security_review"),
}
_AUTONOMY_APPROVALS = {
    TrustAutonomyClass.OBSERVE.value: (),
    TrustAutonomyClass.RECALL.value: (),
    TrustAutonomyClass.SUGGEST.value: (),
    TrustAutonomyClass.PREPARE.value: (),
    TrustAutonomyClass.EXECUTE.value: ("pr_evidence",),
    TrustAutonomyClass.PROMOTE.value: ("pr_evidence",),
}


@dataclass(frozen=True)
class RuntimeEvolutionProposalIntake:
    proposal_id: str
    proposal_type: str
    title: str
    description: str
    evidence: str
    target_patterns: str
    status: str = "pending"
    multica_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id is required")
        if not self.proposal_type:
            raise ValueError(f"{self.proposal_id}: proposal_type is required")
        if not self.title:
            raise ValueError(f"{self.proposal_id}: title is required")
        if not self.description:
            raise ValueError(f"{self.proposal_id}: description is required")
        if not self.evidence:
            raise ValueError(f"{self.proposal_id}: evidence is required")
        if not self.target_patterns:
            raise ValueError(f"{self.proposal_id}: target_patterns contract snapshot is required")

    def to_legacy_row(self) -> dict[str, Any]:
        return {
            "id": self.proposal_id,
            "type": self.proposal_type,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "target_patterns": self.target_patterns,
            "status": self.status,
        }



def build_mcp_runtime_evolution_proposal_intake(
    *,
    proposal_id: str,
    title: str,
    description: str,
    evidence: str = "",
    proposal_type: str = "intent_pattern",
    user_id: str | None = None,
) -> RuntimeEvolutionProposalIntake:
    """Build an inert runtime-intake row for the MCP proposal writer."""

    normalized_type = normalize_mcp_evolution_proposal_type(proposal_type)
    normalized_user_id = str(user_id).strip() if user_id else None
    source_ref = f"mcp:create_evolution_proposal:{proposal_id}"
    evidence_refs = _evidence_refs(source_ref, evidence)
    signal = EvolutionSignal(
        signal_id=f"signal_{proposal_id}",
        signal_type=legacy_signal_type_for_proposal_type(normalized_type),
        summary=description,
        source="mcp:create_evolution_proposal",
        evidence_refs=evidence_refs,
        user_id=normalized_user_id,
        scope="personal" if normalized_user_id else "system",
        metadata={"evidence_excerpt": evidence[:500], "mcp_tool": "create_evolution_proposal"},
    )
    candidate = build_existing_zoe_proposal_candidate(
        proposal_type=normalized_type,
        title=title,
        evidence_refs=evidence_refs,
        legacy_writer="mcp:create_evolution_proposal",
        runtime_notes="MCP creates review-only evolution proposals; no execution is granted by this contract.",
    )
    return build_runtime_evolution_proposal_intake(
        proposal_id=proposal_id,
        proposal_type=normalized_type,
        title=title,
        problem_statement=description,
        signal=signal,
        candidates=(candidate,),
        affected_capabilities=("zoe_codebase", "multica_governance", "verification"),
        expected_benefit="Create a reviewable Zoe improvement proposal with MCP-supplied evidence before implementation work.",
        verification_plan=(
            "human_or_multica_review_required_before_approval",
            "implementation_pr_must_attach_tests_and_evidence_before_completion",
        ),
        rollback_plan="Reject or defer the proposal; no runtime change has been made by proposal creation.",
        metadata={"legacy_writer": "mcp:create_evolution_proposal"},
    )

def build_runtime_evolution_proposal_intake(
    *,
    proposal_id: str,
    proposal_type: str,
    title: str,
    problem_statement: str,
    signal: EvolutionSignal,
    candidates: Sequence[CandidateEvaluation],
    affected_capabilities: Sequence[str],
    expected_benefit: str,
    verification_plan: Sequence[str],
    rollback_plan: str,
    autonomy_class: str = TrustAutonomyClass.SUGGEST.value,
    risk: str = ProposalRisk.MEDIUM.value,
    approval_required: Sequence[str] = (),
    legacy_target_patterns: Sequence[str] = (),
    multica_issue_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RuntimeEvolutionProposalIntake:
    """Build a review-only proposal row from runtime Notice/Search/Evaluate data.

    The return value is inert. It contains enough validated data for a caller to
    insert into `evolution_proposals` and sync to Multica, but this function does
    neither. Candidate selection is deterministic and keeps alternatives in the
    contract metadata for reviewer evidence.
    """

    ranked_candidates = rank_candidates(tuple(candidates))
    if not ranked_candidates:
        raise ValueError(f"{proposal_id}: at least one candidate is required")
    normalized_approval_required = _approval_requirements(
        autonomy_class=autonomy_class,
        risk=risk,
        explicit=approval_required,
    )
    evidence_payload = _evidence_payload(signal=signal, candidates=ranked_candidates, metadata=metadata)
    proposal_metadata = dict(metadata or {})
    proposal_metadata.update(
        {
            "created_by": RUNTIME_INTAKE_SOURCE,
            "legacy_table": "evolution_proposals",
            "legacy_status": "pending",
            "legacy_proposal_type": proposal_type,
            "legacy_target_patterns": [str(item) for item in legacy_target_patterns if item is not None],
            "candidate_search": [candidate.to_dict() for candidate in ranked_candidates],
            "selected_candidate_id": ranked_candidates[0].candidate_id,
        }
    )
    proposal = build_evolution_proposal(
        proposal_id=proposal_id,
        title=title,
        problem_statement=problem_statement,
        signals=(signal,),
        candidate=ranked_candidates[0],
        affected_capabilities=tuple(affected_capabilities),
        autonomy_class=autonomy_class,
        risk=risk,
        expected_benefit=expected_benefit,
        verification_plan=tuple(verification_plan),
        rollback_plan=rollback_plan,
        approval_required=normalized_approval_required,
        status=ProposalStatus.PENDING_APPROVAL.value,
        multica_issue_id=multica_issue_id,
        metadata=proposal_metadata,
    )
    contract_snapshot = {
        "version": CONTRACT_ENVELOPE_VERSION,
        "schema": _SCHEMA,
        "legacy_writer": RUNTIME_INTAKE_SOURCE,
        "proposal": proposal.to_dict(),
    }
    target_patterns = json.dumps(contract_snapshot, sort_keys=True, separators=(",", ":"))
    evidence = json.dumps(evidence_payload, sort_keys=True, separators=(",", ":"))
    return RuntimeEvolutionProposalIntake(
        proposal_id=proposal_id,
        proposal_type=proposal_type,
        title=title,
        description=problem_statement,
        evidence=evidence,
        target_patterns=target_patterns,
        multica_payload={
            "proposal_id": proposal_id,
            "title": title,
            "description": problem_statement,
            "evidence": evidence,
            "proposal_type": proposal_type,
            "contract_snapshot": target_patterns,
        },
    )


def _evidence_refs(source_ref: str, evidence: str) -> tuple[str, ...]:
    refs = [source_ref]
    if evidence.strip():
        refs.append(f"evidence:{source_ref}")
    return tuple(refs)


def _approval_requirements(*, autonomy_class: str, risk: str, explicit: Sequence[str]) -> tuple[str, ...]:
    approval_required: list[str] = []
    approval_required.extend(_AUTONOMY_APPROVALS.get(autonomy_class, ()))
    approval_required.extend(_RISK_APPROVALS.get(risk, ()))
    approval_required.extend(str(item) for item in explicit if item)
    return tuple(dict.fromkeys(approval_required))


def _evidence_payload(
    *,
    signal: EvolutionSignal,
    candidates: Sequence[CandidateEvaluation],
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    signal.validate()
    return {
        "source": RUNTIME_INTAKE_SOURCE,
        "signal": signal.to_dict(),
        "candidate_ids": [candidate.candidate_id for candidate in candidates],
        "candidate_evidence_refs": list(
            dict.fromkeys(ref for candidate in candidates for ref in candidate.evidence_refs)
        ),
        "metadata": dict(metadata or {}),
    }


__all__ = [
    "RUNTIME_INTAKE_SOURCE",
    "RuntimeEvolutionProposalIntake",
    "build_mcp_runtime_evolution_proposal_intake",
    "build_runtime_evolution_proposal_intake",
]
