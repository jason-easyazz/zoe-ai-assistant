import pytest

from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_outcome_memory import EvolutionOutcomeMemoryError
from zoe_evolution_outcome_admission import (
    DEFAULT_EVOLUTION_OUTCOME_TARGET_BACKENDS,
    build_evolution_outcome_admission_request,
    evaluate_evolution_outcome_admission,
)
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)
from zoe_memory_admission import MemoryAdmissionError, MemoryAdmissionStatus
from zoe_memory_contract import MemoryEventType
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType

pytestmark = pytest.mark.ci_safe


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


def _proposal(status=ProposalStatus.VERIFIED.value, approval_required=("pr_evidence", "memory_admission"), **overrides):
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
        "verification_plan": ("pytest:test_zoe_evolution_outcome_admission",),
        "rollback_plan": "Do not retain the outcome candidate.",
        "approval_required": approval_required,
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
        "evidence_refs": ("pytest:test_zoe_evolution_outcome_admission", "pr:310"),
        "user_id": "jason",
        "subject_id": "proposal_memory_gate",
        "confidence": 0.92,
    }
    values.update(overrides)
    return ObservationTrace(**values)


def test_verified_outcome_without_memory_approval_stays_pending():
    result = evaluate_evolution_outcome_admission(_proposal(), (_verification_trace(),))

    assert result.candidate.event_type == MemoryEventType.FIX.value
    assert result.request.target_backends == DEFAULT_EVOLUTION_OUTCOME_TARGET_BACKENDS
    assert result.request.proposal.proposal_id == "proposal_memory_gate"
    assert result.decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert result.decision.allowed_to_keep_pending is True
    assert result.decision.allowed_to_write_durable is False
    assert result.decision.blockers == ("approval_required",)
    assert result.decision.required_approvals == ("memory_admission", "self_evolution_proposal")


def test_verified_outcome_with_memory_approval_can_write_hindsight_candidate():
    result = evaluate_evolution_outcome_admission(
        _proposal(),
        (_verification_trace(),),
        approval_refs=("approval:multica:ZOE-310",),
    )

    assert result.decision.status == MemoryAdmissionStatus.APPROVED.value
    assert result.decision.allowed_to_write_durable is True
    assert result.decision.allowed_backends == (MemoryBackend.HINDSIGHT.value,)
    assert result.decision.blockers == ()
    assert "approval:multica:ZOE-310" in result.decision.evidence_refs


def test_outcome_admission_requires_proposal_memory_admission_gate():
    proposal = _proposal(approval_required=("pr_evidence",))

    with pytest.raises(MemoryAdmissionError, match="must require memory_admission"):
        build_evolution_outcome_admission_request(proposal, (_verification_trace(),))


def test_graphiti_target_is_allowed_for_relational_outcome_candidate():
    result = evaluate_evolution_outcome_admission(
        _proposal(),
        (_verification_trace(),),
        target_backends=(MemoryBackend.GRAPHITI.value,),
        approval_refs=("approval:multica:ZOE-311",),
    )

    assert result.candidate.relationships
    assert result.decision.status == MemoryAdmissionStatus.APPROVED.value
    assert result.decision.allowed_backends == (MemoryBackend.GRAPHITI.value,)
    assert result.decision.required_approvals == (
        "memory_admission",
        "self_evolution_proposal",
        "relational_truth",
    )


def test_failed_outcome_remains_pending_only_and_never_trusted_durable_memory():
    failed_trace = _verification_trace(
        trace_id="trace_failed_memory_gate",
        outcome=ObservationOutcome.FAILED.value,
        summary="Verification failed because approval evidence was missing.",
        evidence_refs=("pytest:failed-memory-gate",),
        confidence=0.8,
    )

    result = evaluate_evolution_outcome_admission(
        _proposal(status=ProposalStatus.FAILED.value),
        (failed_trace,),
        approval_refs=("approval:multica:ZOE-312",),
    )

    assert result.candidate.event_type == MemoryEventType.FAILURE.value
    assert result.decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert result.decision.allowed_to_keep_pending is True
    assert result.decision.allowed_to_write_durable is False
    assert "failed_or_blocked_trace_present" in result.decision.blockers
    assert "proposal_must_be_approved_or_verified" in result.decision.blockers


def test_retired_outcome_remains_blocked_until_retirement_memory_policy_exists():
    retired_trace = _verification_trace(
        trace_id="trace_retired_memory_gate",
        outcome=ObservationOutcome.PARTIAL.value,
        summary="Retirement evidence showed the old path was superseded.",
        evidence_refs=("eval:retirement-review",),
        confidence=0.7,
    )

    result = evaluate_evolution_outcome_admission(
        _proposal(status=ProposalStatus.RETIRED.value),
        (retired_trace,),
        approval_refs=("approval:multica:ZOE-315",),
    )

    assert result.candidate.event_type == MemoryEventType.CAPABILITY.value
    assert result.decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert result.decision.allowed_to_keep_pending is True
    assert result.decision.allowed_to_write_durable is False
    assert "proposal_must_be_approved_or_verified" in result.decision.blockers


def test_retired_outcome_requires_matching_retirement_trace_before_admission_request():
    with pytest.raises(EvolutionOutcomeMemoryError, match="retirement verification"):
        build_evolution_outcome_admission_request(
            _proposal(status=ProposalStatus.RETIRED.value),
            (_verification_trace(outcome=ObservationOutcome.FAILED.value),),
            approval_refs=("approval:multica:ZOE-316",),
        )


def test_result_serializes_candidate_request_and_decision():
    result = evaluate_evolution_outcome_admission(
        _proposal(),
        (_verification_trace(),),
        approval_refs=("approval:multica:ZOE-313",),
        metadata={"review": "operator-approved"},
    )

    payload = result.to_dict()

    assert payload["candidate"]["event_id"] == "mem_evt_evolution_outcome_proposal_memory_gate"
    assert payload["request"]["admission_id"] == "admit_evolution_outcome_proposal_memory_gate"
    assert payload["request"]["metadata"]["extra"] == {"review": "operator-approved"}
    assert payload["decision"]["status"] == MemoryAdmissionStatus.APPROVED.value


def test_none_items_are_ignored_in_backend_and_approval_sequences():
    result = evaluate_evolution_outcome_admission(
        _proposal(),
        (_verification_trace(),),
        target_backends=(None, MemoryBackend.HINDSIGHT.value),
        approval_refs=(None, "approval:multica:ZOE-314"),
    )

    assert result.request.target_backends == (MemoryBackend.HINDSIGHT.value,)
    assert result.request.approval_refs == ("approval:multica:ZOE-314",)
    assert result.decision.status == MemoryAdmissionStatus.APPROVED.value
