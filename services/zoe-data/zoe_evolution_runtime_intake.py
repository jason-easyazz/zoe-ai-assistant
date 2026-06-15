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
from typing import Any, Callable, Mapping, Sequence

from zoe_candidate_scoring import EXAMPLE_CANDIDATES, CandidateEvaluation, adoption_gate, rank_candidates
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
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
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType
from zoe_observation_trace_collector import ObservationTraceCollectorPolicy, collect_observation_traces

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


def build_pi_runtime_install_proposal_intake(
    *,
    proposal_id: str = "prop_pi_runtime_install",
    user_id: str | None = None,
    probe_result: Mapping[str, Any] | None = None,
) -> RuntimeEvolutionProposalIntake:
    """Build an inert proposal for adopting Pi as an external Zoe runtime.

    This does not install Node, npm, Pi, packages, agents, or models. It packages
    the current read-only Pi probe plus Zoe's Pi candidate score into the normal
    proposal contract so Multica/humans can review prerequisites before any
    privileged environment change.
    """

    probe = dict(probe_result or {})
    pi_candidate = _pi_candidate_with_probe(probe)
    gate = adoption_gate(pi_candidate)
    status = str(probe.get("status") or "unknown")
    reason = str(probe.get("reason") or "no probe reason supplied")
    tools = probe.get("tools") if isinstance(probe.get("tools"), Mapping) else {}
    signal = EvolutionSignal(
        signal_id=f"signal_{proposal_id}",
        signal_type=EvolutionSignalType.TOOL_GAP.value,
        summary="Pi external runtime is requested but Zoe must prove local/offline prerequisites before use.",
        source="pi_runtime_probe",
        evidence_refs=("docs/architecture/zoe-pi-runtime-harness.md", "probe:pi_runtime_probe"),
        user_id=user_id,
        scope="system" if not user_id else "project",
        metadata={
            "probe_status": status,
            "probe_reason": reason,
            "node": tools.get("node"),
            "npm": tools.get("npm"),
            "pi": tools.get("pi"),
            "acceptable": probe.get("acceptable"),
            "ok": probe.get("ok"),
        },
    )
    return build_runtime_evolution_proposal_intake(
        proposal_id=proposal_id,
        proposal_type="code_improvement",
        title="Review Pi external runtime adoption",
        problem_statement=(
            "Zoe should use Pi as an external runtime where it provides proven package, CLI, SDK, "
            "or project-agent leverage, but the current runtime must be installed and configured for "
            "local/offline model use before any execution is allowed."
        ),
        signal=signal,
        candidates=(pi_candidate,),
        affected_capabilities=("pi_external_runtime", "multica_governance", "local_model_runtime"),
        expected_benefit=(
            "Reuse Pi's external agent/runtime ecosystem instead of rebuilding comparable harness features inside Zoe."
        ),
        verification_plan=(
            "python3 scripts/maintenance/pi_runtime_probe.py --json",
            "local_model_configuration_evidence_required",
            "candidate_scoring_and_license_review_required",
            "implementation_pr_must_attach_tests_and_rollback_evidence",
        ),
        rollback_plan=(
            "Do not install or enable Pi; remove any proposed runtime packages/config and leave ZOE_PI_ENABLED=false."
        ),
        autonomy_class=TrustAutonomyClass.PREPARE.value,
        risk=ProposalRisk.PRIVILEGED.value,
        approval_required=("install_or_runtime_change", "license_review", "pr_evidence"),
        legacy_target_patterns=("pi external runtime", "local offline model runtime", "multica approval"),
        metadata={"pi_runtime_probe": probe, "pi_candidate_gate": gate},
    )


def build_graphiti_runtime_trial_proposal_intake(
    *,
    proposal_id: str = "prop_graphiti_runtime_trial",
    user_id: str | None = None,
    runtime_probe_result: Mapping[str, Any] | None = None,
) -> RuntimeEvolutionProposalIntake:
    """Build an inert proposal for Graphiti relational-memory bake-off runtime work.

    This does not install packages, start FalkorDB/Neo4j, ingest episodes, query
    graph data, or wire Graphiti into chat. It packages the current read-only
    runtime probe plus Zoe's Graphiti candidate score into the normal proposal
    contract so Multica/humans can review optional dependencies and sidecar work.
    """

    probe = dict(runtime_probe_result or {})
    graphiti_candidate = _graphiti_candidate_with_probe(probe)
    gate = adoption_gate(graphiti_candidate)
    status = str(probe.get("status") or "unknown")
    reason = str(probe.get("reason") or "no probe reason supplied")
    packages = probe.get("packages") if isinstance(probe.get("packages"), Mapping) else {}
    backend = probe.get("backend") if isinstance(probe.get("backend"), Mapping) else {}
    llm = probe.get("llm") if isinstance(probe.get("llm"), Mapping) else {}
    signal = EvolutionSignal(
        signal_id=f"signal_{proposal_id}",
        signal_type=EvolutionSignalType.TOOL_GAP.value,
        summary="Graphiti relational memory is requested but Zoe must prove local runtime, backend, and structured-output readiness before ingest.",
        source="graphiti_runtime_probe",
        evidence_refs=("docs/architecture/zoe-graphiti-fixtures.md", "probe:graphiti_runtime_probe"),
        user_id=user_id,
        scope="system" if not user_id else "project",
        metadata={
            "probe_status": status,
            "probe_reason": reason,
            "packages": packages,
            "backend_enabled": backend.get("enabled"),
            "llm_enabled": llm.get("enabled"),
            "acceptable": probe.get("acceptable"),
            "ok": probe.get("ok"),
        },
    )
    return build_runtime_evolution_proposal_intake(
        proposal_id=proposal_id,
        proposal_type="code_improvement",
        title="Review Graphiti relational-memory runtime trial",
        problem_statement=(
            "Zoe should evaluate Graphiti for temporal relational truth across people, tools, capabilities, "
            "failures, fixes, approvals, recurring tasks, and superseded facts, but only after optional local "
            "dependencies, graph sidecar readiness, and local structured-output extraction are proven."
        ),
        signal=signal,
        candidates=(graphiti_candidate,),
        affected_capabilities=("graphiti_relational_memory", "memory_router", "multica_governance"),
        expected_benefit=(
            "Measure whether a Graphiti-style temporal graph can provide explicit evidence-backed relationship truth "
            "that MemPalace and Hindsight should not own alone."
        ),
        verification_plan=(
            "python3 scripts/maintenance/graphiti_runtime_probe.py --json",
            "falkordb_fixture_ingest_query_measurement_required",
            "local_structured_output_extraction_evidence_required",
            "p50_p95_latency_and_memory_footprint_required",
            "implementation_pr_must_attach_tests_and_rollback_evidence",
        ),
        rollback_plan=(
            "Do not install Graphiti packages, start graph sidecars, ingest fixtures, or enable graph recall; leave GRAPHITI_ENABLED=false."
        ),
        autonomy_class=TrustAutonomyClass.PREPARE.value,
        risk=ProposalRisk.PRIVILEGED.value,
        approval_required=("install_or_runtime_change", "license_review", "sidecar_start", "pr_evidence"),
        legacy_target_patterns=("graphiti relational memory", "falkordb sidecar", "local structured output"),
        metadata={"graphiti_runtime_probe": probe, "graphiti_candidate_gate": gate},
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
    collect_observation_trace: bool = True,
    trace_collector_policy: ObservationTraceCollectorPolicy | None = None,
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
    if collect_observation_trace:
        evidence_payload["observation_trace_collection"] = _collect_runtime_intake_signal_trace(
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            title=title,
            signal=signal,
            candidates=ranked_candidates,
            policy=trace_collector_policy,
        )
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



def _collect_runtime_intake_signal_trace(
    *,
    proposal_id: str,
    proposal_type: str,
    title: str,
    signal: EvolutionSignal,
    candidates: Sequence[CandidateEvaluation],
    policy: ObservationTraceCollectorPolicy | None,
) -> dict[str, Any]:
    trace = ObservationTrace(
        trace_id=f"trace_runtime_intake_{proposal_id}",
        trace_type=ObservationTraceType.PROPOSAL.value,
        surface="local_service",
        scope=signal.scope,
        outcome=ObservationOutcome.SUCCESS.value,
        summary=f"Runtime intake prepared review-only proposal {proposal_id}: {title}",
        evidence_refs=tuple(signal.evidence_refs),
        user_id=signal.user_id,
        subject_id=proposal_id,
        related_ids=tuple(candidate.candidate_id for candidate in candidates),
        confidence=1.0,
        metadata={
            "proposal_type": proposal_type,
            "source": RUNTIME_INTAKE_SOURCE,
            "source_signal_id": signal.signal_id,
            "selected_candidate_id": candidates[0].candidate_id,
            "candidate_count": len(candidates),
        },
    )
    active_policy = policy or ObservationTraceCollectorPolicy(
        max_batch_size=1,
        allowed_surfaces=("local_service",),
        allowed_trace_types=(ObservationTraceType.PROPOSAL.value,),
    )
    collection = collect_observation_traces((trace,), policy=active_policy)
    payload = collection.to_dict()
    if not collection.ok:
        reasons = "; ".join(item["reason"] for item in collection.rejected)
        raise ValueError(f"{proposal_id}: observation_trace_collection rejected: {reasons}")
    return payload


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


def _graphiti_candidate_with_probe(probe_result: Mapping[str, Any]) -> CandidateEvaluation:
    return _candidate_with_probe_metadata(
        "graphiti_falkordb_trial",
        probe_result,
        probe_evidence_ref="probe:graphiti_runtime_probe",
        probe_metadata_key="graphiti_runtime_probe",
        readiness_key="runtime_ready",
        readiness_fn=lambda probe: bool(probe.get("ok")) and probe.get("status") == "ready_for_ingest_trial",
    )


def _pi_candidate_with_probe(probe_result: Mapping[str, Any]) -> CandidateEvaluation:
    return _candidate_with_probe_metadata(
        "pi_runtime_reuse",
        probe_result,
        probe_evidence_ref="probe:pi_runtime_probe",
        probe_metadata_key="pi_runtime_probe",
        readiness_key="offline_ready",
        readiness_fn=lambda probe: bool(probe.get("ok"))
        and bool((probe.get("config") if isinstance(probe.get("config"), Mapping) else {}).get("local_model_configured")),
    )


def _candidate_with_probe_metadata(
    candidate_id: str,
    probe_result: Mapping[str, Any],
    *,
    probe_evidence_ref: str,
    probe_metadata_key: str,
    readiness_key: str,
    readiness_fn: Callable[[Mapping[str, Any]], bool],
) -> CandidateEvaluation:
    base = next(
        (candidate for candidate in EXAMPLE_CANDIDATES if candidate.candidate_id == candidate_id),
        None,
    )
    if base is None:
        raise ValueError(f"EXAMPLE_CANDIDATES does not contain {candidate_id!r}; update zoe_candidate_scoring.py")
    metadata = dict(base.metadata or {})
    metadata.update(
        {
            probe_metadata_key: dict(probe_result),
            readiness_key: readiness_fn(probe_result),
        }
    )
    return CandidateEvaluation(
        candidate_id=base.candidate_id,
        name=base.name,
        source=base.source,
        task=base.task,
        score=base.score,
        evidence_refs=tuple(dict.fromkeys((*base.evidence_refs, probe_evidence_ref))),
        license_risk=base.license_risk,
        offline_viability=base.offline_viability,
        stars=base.stars,
        last_activity=base.last_activity,
        runtime_notes=base.runtime_notes,
        security_notes=base.security_notes,
        overlaps_existing=base.overlaps_existing,
        recommendation=base.recommendation,
        metadata=metadata,
    )


__all__ = [
    "RUNTIME_INTAKE_SOURCE",
    "RuntimeEvolutionProposalIntake",
    "build_graphiti_runtime_trial_proposal_intake",
    "build_mcp_runtime_evolution_proposal_intake",
    "build_pi_runtime_install_proposal_intake",
    "build_runtime_evolution_proposal_intake",
]
