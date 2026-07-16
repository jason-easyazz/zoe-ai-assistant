import pytest

from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)
from zoe_memory_admission import (
    MemoryAdmissionError,
    MemoryAdmissionRequest,
    MemoryAdmissionStatus,
    evaluate_memory_admission,
)
from zoe_memory_contract import (
    MemoryEvent,
    MemoryEventType,
    MemoryRelationship,
    MemoryScope,
    MemorySource,
    RelationshipType,
)
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType

pytestmark = pytest.mark.ci_safe


def _fact_event(**overrides):
    values = {
        "event_id": "mem_evt_pref",
        "user_id": "jason",
        "scope": MemoryScope.PERSONAL.value,
        "source": MemorySource.CHAT.value,
        "event_type": MemoryEventType.PREFERENCE.value,
        "content": "Jason prefers Zoe memory to stay offline-only.",
        "evidence_refs": ("chat:offline-memory",),
        "confidence": 0.9,
    }
    values.update(overrides)
    return MemoryEvent(**values)


def _success_trace(**overrides):
    values = {
        "trace_id": "trace_admit_ok",
        "trace_type": ObservationTraceType.ADMISSION.value,
        "surface": "multica",
        "scope": "personal",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Memory candidate reviewed with evidence.",
        "evidence_refs": ("multica:ZOE-123",),
        "user_id": "jason",
    }
    values.update(overrides)
    return ObservationTrace(**values)


def _proposal(status=ProposalStatus.APPROVED.value):
    signal = EvolutionSignal(
        signal_id="signal_memory_gate",
        signal_type=EvolutionSignalType.USER_REQUEST.value,
        summary="User asked Zoe to retain a verified memory lesson.",
        source="chat",
        evidence_refs=("chat:memory-gate",),
        user_id="jason",
        scope="project",
    )
    candidate = CandidateEvaluation(
        candidate_id="candidate_existing_zoe_memory",
        name="Existing Zoe memory contract",
        source="existing_zoe",
        task="memory admission",
        score=CandidateScore(
            fit=5,
            activity=4,
            license=5,
            offline=5,
            security=4,
            footprint=5,
            tests=4,
            maintainability=4,
            overlap=5,
        ),
        evidence_refs=("docs:zoe-memory-contract",),
        license_risk="compatible",
        offline_viability="required",
        recommendation="keep",
    )
    return build_evolution_proposal(
        proposal_id="proposal_memory_gate",
        title="Admit verified memory lesson",
        problem_statement="Zoe needs to retain a verified self-evolution lesson.",
        signals=(signal,),
        candidate=candidate,
        affected_capabilities=("memory",),
        autonomy_class=TrustAutonomyClass.SUGGEST.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Retains evidence-backed improvement without blind auto-retain.",
        verification_plan=("Focused memory admission tests pass.",),
        rollback_plan="Leave the candidate pending and do not promote durable memory.",
        approval_required=("memory_admission",),
        status=status,
    )


def test_pending_candidate_cannot_write_durable_memory_without_approval():
    request = MemoryAdmissionRequest(
        admission_id="admit_pending",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert decision.allowed_to_keep_pending is True
    assert decision.allowed_to_write_durable is False
    assert decision.allowed_backends == ()
    assert decision.blockers == ("approval_required", "successful_admission_or_verification_trace_required")


def test_approved_memory_with_successful_admission_trace_can_write_target_backend():
    request = MemoryAdmissionRequest(
        admission_id="admit_pref",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_to_write_durable is True
    assert decision.allowed_backends == (MemoryBackend.MEMPALACE.value,)
    assert decision.blockers == ()
    assert decision.required_approvals == ("memory_admission",)
    assert "chat:offline-memory" in decision.evidence_refs
    assert "approval:multica:ZOE-123" in decision.evidence_refs


def test_failed_or_blocked_trace_blocks_even_with_approval():
    failed_trace = _success_trace(
        trace_id="trace_failed",
        outcome=ObservationOutcome.FAILED.value,
        summary="The admission check contradicted current user preference.",
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_failed",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(), failed_trace),
        approval_refs=("approval:multica:ZOE-123",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert "failed_or_blocked_trace_present" in decision.blockers


def test_failed_non_admission_trace_does_not_block_clean_admission():
    failed_recall = _success_trace(
        trace_id="trace_recall_miss",
        trace_type=ObservationTraceType.RECALL.value,
        surface="mempalace",
        outcome=ObservationOutcome.FAILED.value,
        summary="Recall did not find a useful prior memory.",
        evidence_refs=(),
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_with_recall_miss",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(), failed_recall),
        approval_refs=("approval:multica:ZOE-123",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_to_write_durable is True
    assert "failed_or_blocked_trace_present" not in decision.blockers


def test_graphiti_target_requires_relationship_or_supersession():
    request = MemoryAdmissionRequest(
        admission_id="admit_graph_no_edge",
        candidate=_fact_event(scope=MemoryScope.PROJECT.value),
        requested_by="multica",
        target_backends=(MemoryBackend.GRAPHITI.value,),
        observation_traces=(_success_trace(scope="project"),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert "graphiti_target_requires_relationship_or_supersession" in decision.blockers
    assert decision.required_approvals == ("memory_admission", "relational_truth")


def test_relational_memory_can_be_approved_for_graphiti_with_evidence():
    event = _fact_event(
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FACT.value,
        relationships=(
            MemoryRelationship(
                relationship_type=RelationshipType.TRUSTED_FOR.value,
                source="mempalace",
                target="fast_chat_recall",
            ),
        ),
        evidence_refs=("trace:mempalace-baseline",),
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_graph_edge",
        candidate=event,
        requested_by="multica",
        target_backends=(MemoryBackend.GRAPHITI.value,),
        observation_traces=(_success_trace(scope="project"),),
        approval_refs=("approval:multica:ZOE-124",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.allowed_to_write_durable is True
    assert decision.allowed_backends == (MemoryBackend.GRAPHITI.value,)


def test_trace_user_must_match_candidate_user():
    request = MemoryAdmissionRequest(
        admission_id="admit_cross_user",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(user_id="someone_else"),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    with pytest.raises(MemoryAdmissionError, match="user_id must match"):
        evaluate_memory_admission(request)


def test_trace_user_id_is_required_even_for_project_scope_traces():
    request = MemoryAdmissionRequest(
        admission_id="admit_trace_missing_user",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(scope="project", user_id=None),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    with pytest.raises(MemoryAdmissionError, match="user_id must match"):
        evaluate_memory_admission(request)


def test_read_only_or_governance_backends_are_not_admitted_write_targets():
    request = MemoryAdmissionRequest(
        admission_id="admit_read_only_backend",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.GRAPHIFY.value,),
        observation_traces=(_success_trace(),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    with pytest.raises(MemoryAdmissionError, match="not admitted write targets"):
        evaluate_memory_admission(request)


def test_invalid_candidate_errors_are_wrapped_as_memory_admission_error():
    request = MemoryAdmissionRequest(
        admission_id="admit_bad_candidate",
        candidate=_fact_event(scope="unknown_scope"),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    with pytest.raises(MemoryAdmissionError, match="candidate is invalid"):
        evaluate_memory_admission(request)


def test_invalid_trace_errors_are_wrapped_as_memory_admission_error():
    request = MemoryAdmissionRequest(
        admission_id="admit_bad_trace",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(trace_type="unknown_trace_type"),),
        approval_refs=("approval:multica:ZOE-123",),
    )

    with pytest.raises(MemoryAdmissionError, match="trace trace_admit_ok is invalid"):
        evaluate_memory_admission(request)


def test_self_evolution_memory_requires_approved_proposal_context():
    event = _fact_event(
        event_id="mem_evt_self_evolution_fix",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FIX.value,
        content="The memory admission gate fixed blind durable retain promotion.",
        evidence_refs=("pytest:test_zoe_memory_admission",),
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_self_evolution_no_proposal",
        candidate=event,
        requested_by="multica",
        target_backends=(MemoryBackend.HINDSIGHT.value,),
        observation_traces=(_success_trace(scope="project"),),
        approval_refs=("approval:multica:ZOE-125",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert "self_evolution_memory_requires_proposal_context" in decision.blockers
    assert decision.required_approvals == ("memory_admission", "self_evolution_proposal")


def test_proposal_sourced_memory_requires_proposal_context_even_for_fact_type():
    event = _fact_event(
        event_id="mem_evt_proposal_fact",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.PROPOSAL.value,
        event_type=MemoryEventType.FACT.value,
        content="A proposal-sourced fact still needs proposal context.",
        evidence_refs=("proposal:memory-fact",),
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_proposal_source_no_context",
        candidate=event,
        requested_by="multica",
        target_backends=(MemoryBackend.HINDSIGHT.value,),
        observation_traces=(_success_trace(scope="project"),),
        approval_refs=("approval:multica:ZOE-126",),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert "self_evolution_memory_requires_proposal_context" in decision.blockers


def test_self_evolution_memory_can_be_admitted_with_approved_memory_proposal():
    event = _fact_event(
        event_id="mem_evt_self_evolution_fix",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FIX.value,
        content="The memory admission gate fixed blind durable retain promotion.",
        evidence_refs=("pytest:test_zoe_memory_admission",),
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_self_evolution",
        candidate=event,
        requested_by="multica",
        target_backends=(MemoryBackend.HINDSIGHT.value,),
        observation_traces=(_success_trace(scope="project"),),
        approval_refs=("approval:multica:ZOE-125",),
        proposal=_proposal(),
    )

    decision = evaluate_memory_admission(request)

    assert decision.allowed_to_write_durable is True
    assert decision.allowed_backends == (MemoryBackend.HINDSIGHT.value,)
    assert decision.blockers == ()


def test_pending_optional_proposal_does_not_block_non_self_evolution_memory():
    request = MemoryAdmissionRequest(
        admission_id="admit_pref_with_optional_pending_proposal",
        candidate=_fact_event(),
        requested_by="multica",
        target_backends=(MemoryBackend.MEMPALACE.value,),
        observation_traces=(_success_trace(),),
        approval_refs=("approval:multica:ZOE-123",),
        proposal=_proposal(status=ProposalStatus.PENDING_APPROVAL.value),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_to_write_durable is True
    assert "proposal_must_be_approved_or_verified" not in decision.blockers


def test_self_evolution_memory_blocks_unapproved_proposal_context():
    event = _fact_event(
        event_id="mem_evt_self_evolution_draft",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FIX.value,
        content="Draft proposal should not promote trusted memory.",
        evidence_refs=("pytest:test_zoe_memory_admission",),
    )
    request = MemoryAdmissionRequest(
        admission_id="admit_self_evolution_draft",
        candidate=event,
        requested_by="multica",
        target_backends=(MemoryBackend.HINDSIGHT.value,),
        observation_traces=(_success_trace(scope="project"),),
        approval_refs=("approval:multica:ZOE-125",),
        proposal=_proposal(status=ProposalStatus.PENDING_APPROVAL.value),
    )

    decision = evaluate_memory_admission(request)

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert "proposal_must_be_approved_or_verified" in decision.blockers
