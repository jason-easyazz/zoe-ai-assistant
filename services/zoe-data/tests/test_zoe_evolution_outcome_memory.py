import pytest

from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_outcome_memory import EvolutionOutcomeMemoryError, build_evolution_outcome_memory_event
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)
from zoe_memory_contract import MemoryEventType, RelationshipType
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType


def _candidate():
    return CandidateEvaluation(
        candidate_id="existing_memory_gate",
        name="Existing Zoe memory gate",
        source="existing_zoe",
        task="memory governance",
        score=CandidateScore(
            fit=5,
            activity=4,
            license=5,
            offline=5,
            security=5,
            footprint=5,
            tests=5,
            maintainability=4,
            overlap=5,
        ),
        evidence_refs=("docs:memory-gate",),
        license_risk="compatible",
        offline_viability="required",
        recommendation="keep",
    )


def _signal(user_id="jason", scope="project"):
    return EvolutionSignal(
        signal_id="signal_memory_gate",
        signal_type=EvolutionSignalType.USER_REQUEST.value,
        summary="User asked Zoe to retain verified self-evolution outcomes.",
        source="chat",
        evidence_refs=("chat:self-evolution-outcomes",),
        user_id=user_id,
        scope=scope,
    )


def _proposal(status=ProposalStatus.VERIFIED.value, **overrides):
    values = {
        "proposal_id": "proposal_memory_gate",
        "title": "Retain verified evolution outcomes",
        "problem_statement": "Zoe needs retained evidence for changes that worked or failed.",
        "signals": (_signal(),),
        "candidate": _candidate(),
        "affected_capabilities": ("memory_admission", "self_evolution_loop"),
        "autonomy_class": TrustAutonomyClass.PROMOTE.value,
        "risk": ProposalRisk.MEDIUM.value,
        "expected_benefit": "Zoe learns from verified outcomes without blind durable writes.",
        "verification_plan": ("pytest:test_zoe_evolution_outcome_memory",),
        "rollback_plan": "Do not retain the outcome candidate.",
        "approval_required": ("pr_evidence",),
        "status": status,
    }
    values.update(overrides)
    return build_evolution_proposal(**values)


def _verification_trace(**overrides):
    values = {
        "trace_id": "trace_verify_memory_gate",
        "trace_type": ObservationTraceType.VERIFICATION.value,
        "surface": "multica",
        "scope": "project",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Focused tests and validators passed.",
        "evidence_refs": ("pytest:test_zoe_evolution_outcome_memory", "pr:307"),
        "user_id": "jason",
        "subject_id": "proposal_memory_gate",
        "confidence": 0.92,
    }
    values.update(overrides)
    return ObservationTrace(**values)


def test_verified_proposal_outcome_builds_pending_fix_memory_event():
    event = build_evolution_outcome_memory_event(_proposal(), (_verification_trace(),))

    payload = event.to_dict()

    assert payload["event_id"] == "mem_evt_evolution_outcome_proposal_memory_gate"
    assert payload["user_id"] == "jason"
    assert payload["scope"] == "project"
    assert payload["event_type"] == MemoryEventType.FIX.value
    assert payload["source"] == "trace"
    assert "chat:self-evolution-outcomes" in payload["evidence_refs"]
    assert "pytest:test_zoe_evolution_outcome_memory" in payload["evidence_refs"]
    assert payload["metadata"]["proposal_status"] == ProposalStatus.VERIFIED.value
    assert payload["confidence"] == 0.92
    relationships = {(item["relationship_type"], item["source"], item["target"]) for item in payload["relationships"]}
    assert (
        RelationshipType.TRUSTED_FOR.value,
        "existing_memory_gate",
        "memory_admission",
    ) in relationships
    assert (
        RelationshipType.TRUSTED_FOR.value,
        "existing_memory_gate",
        "self_evolution_loop",
    ) in relationships


def test_failed_proposal_outcome_builds_failure_memory_event():
    trace = _verification_trace(
        trace_id="trace_failed_memory_gate",
        outcome=ObservationOutcome.FAILED.value,
        summary="Verification failed because approval evidence was missing.",
        evidence_refs=("pytest:failed-memory-gate",),
        confidence=0.8,
    )

    event = build_evolution_outcome_memory_event(_proposal(status=ProposalStatus.FAILED.value), (trace,))

    payload = event.to_dict()

    assert payload["event_type"] == MemoryEventType.FAILURE.value
    assert "failed" in payload["content"]
    relationships = {(item["relationship_type"], item["source"], item["target"]) for item in payload["relationships"]}
    assert (
        RelationshipType.FAILED_ON.value,
        "existing_memory_gate",
        "memory_admission",
    ) in relationships


def test_retired_proposal_outcome_builds_capability_memory_event():
    trace = _verification_trace(
        trace_id="trace_retired_memory_gate",
        outcome=ObservationOutcome.PARTIAL.value,
        summary="Retirement evidence showed the old path was superseded.",
        evidence_refs=("eval:retirement-review",),
        confidence=0.7,
    )

    event = build_evolution_outcome_memory_event(_proposal(status=ProposalStatus.RETIRED.value), (trace,))

    payload = event.to_dict()

    assert payload["event_type"] == MemoryEventType.CAPABILITY.value
    relationships = {(item["relationship_type"], item["source"], item["target"]) for item in payload["relationships"]}
    assert (
        RelationshipType.SUPERSEDES.value,
        "existing_memory_gate",
        "memory_admission",
    ) in relationships


def test_non_terminal_proposal_cannot_create_outcome_memory():
    with pytest.raises(EvolutionOutcomeMemoryError, match="status must be terminal"):
        build_evolution_outcome_memory_event(
            _proposal(status=ProposalStatus.APPROVED.value),
            (_verification_trace(),),
        )


def test_verified_proposal_requires_successful_verification_trace():
    with pytest.raises(EvolutionOutcomeMemoryError, match="successful verification trace"):
        build_evolution_outcome_memory_event(
            _proposal(),
            (
                _verification_trace(
                    trace_type=ObservationTraceType.OUTCOME_EVAL.value,
                    outcome=ObservationOutcome.SUCCESS.value,
                ),
            ),
        )


def test_failed_proposal_requires_failed_or_blocked_trace():
    with pytest.raises(EvolutionOutcomeMemoryError, match="failed or blocked"):
        build_evolution_outcome_memory_event(
            _proposal(status=ProposalStatus.FAILED.value),
            (_verification_trace(outcome=ObservationOutcome.SUCCESS.value),),
        )


def test_outcome_memory_requires_user_scope():
    proposal = _proposal(signals=(_signal(user_id=None),))

    with pytest.raises(EvolutionOutcomeMemoryError, match="user_id is required"):
        build_evolution_outcome_memory_event(proposal, (_verification_trace(),))

    event = build_evolution_outcome_memory_event(proposal, (_verification_trace(),), user_id="zoe_system")

    assert event.user_id == "zoe_system"
