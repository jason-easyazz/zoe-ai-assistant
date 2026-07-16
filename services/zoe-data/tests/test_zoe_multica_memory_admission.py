import pytest
from multica_ticket_contract import describe_ticket
from zoe_memory_admission import MemoryAdmissionStatus
from zoe_memory_contract import (
    MemoryEvent,
    MemoryEventType,
    MemoryRelationship,
    MemoryScope,
    MemorySource,
    RelationshipType,
)
from zoe_memory_router import MemoryBackend
from zoe_multica_memory_admission import (
    DEFAULT_MULTICA_MEMORY_TARGET_BACKENDS,
    build_multica_memory_admission_request,
    evaluate_multica_memory_admission,
)

pytestmark = pytest.mark.ci_safe


def _event(**overrides):
    values = {
        "event_id": "mem_evt_multica_pref",
        "user_id": "jason",
        "scope": MemoryScope.PERSONAL.value,
        "source": MemorySource.CHAT.value,
        "event_type": MemoryEventType.PREFERENCE.value,
        "content": "Jason prefers Zoe memory to remain local and offline.",
        "evidence_refs": ("chat:offline-memory",),
        "confidence": 0.91,
    }
    values.update(overrides)
    return MemoryEvent(**values)


def _issue(metadata=None, **overrides):
    values = {
        "id": "ZOE-123",
        "title": "Review Zoe memory retain candidate",
        "description": describe_ticket(
            "Review the candidate before durable memory promotion.",
            metadata=metadata or {},
        ),
    }
    values.update(overrides)
    return values


def test_multica_record_without_explicit_approval_stays_pending():
    decision = evaluate_multica_memory_admission(_issue(), _event())

    assert decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert decision.allowed_to_write_durable is False
    assert decision.allowed_backends == ()
    assert decision.blockers == ("approval_required", "successful_admission_or_verification_trace_required")
    assert decision.required_approvals == ("memory_admission",)
    assert decision.evidence_refs == ("chat:offline-memory",)


def test_explicit_multica_memory_approval_builds_success_trace_and_allows_write():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-123",
            "memory_admission_evidence_refs": ["review:human-approved"],
            "memory_admission_approval_refs": ["approval:operator:jason"],
        }
    )

    request = build_multica_memory_admission_request(issue, _event())
    decision = evaluate_multica_memory_admission(issue, _event())

    assert request.approval_refs == ("approval:multica:ZOE-123", "approval:operator:jason")
    assert len(request.observation_traces) == 1
    trace = request.observation_traces[0]
    assert trace.outcome == "success"
    assert trace.evidence_refs == ("multica:ZOE-123", "review:human-approved")
    assert trace.user_id == "jason"

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_to_write_durable is True
    assert decision.allowed_backends == DEFAULT_MULTICA_MEMORY_TARGET_BACKENDS
    assert "approval:multica:ZOE-123" in decision.evidence_refs
    assert "approval:operator:jason" in decision.evidence_refs


def test_blocked_multica_record_blocks_even_when_approval_flag_is_present():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-124",
            "blocked_reason": "Candidate contradicts newer user correction.",
        }
    )

    decision = evaluate_multica_memory_admission(issue, _event())

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert "failed_or_blocked_trace_present" in decision.blockers
    assert "successful_admission_or_verification_trace_required" in decision.blockers
    assert "approval:multica:ZOE-124" in decision.evidence_refs
    assert "multica:ZOE-124" in decision.evidence_refs


def test_blocked_multica_record_without_approval_flag_fails_closed():
    issue = _issue(
        {
            "memory_admission_review_id": "ZOE-124B",
            "blocked_reason": "Reviewer rejected the candidate.",
        }
    )

    decision = evaluate_multica_memory_admission(issue, _event())

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert decision.blockers == (
        "approval_required",
        "successful_admission_or_verification_trace_required",
        "failed_or_blocked_trace_present",
    )
    assert "approval:multica:ZOE-124B" not in decision.evidence_refs
    assert "multica:ZOE-124B" in decision.evidence_refs


def test_description_nulls_do_not_clear_raw_metadata_blocker():
    issue = _issue(
        {"memory_admission_approved": True, "memory_admission_review_id": "ZOE-124C"},
    )
    issue["metadata"] = {"blocked_reason": "Raw review metadata blocked promotion."}

    decision = evaluate_multica_memory_admission(issue, _event())

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert "failed_or_blocked_trace_present" in decision.blockers
    assert "multica:ZOE-124C" in decision.evidence_refs


def test_none_items_are_ignored_in_metadata_reference_sequences():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-124D",
            "memory_admission_evidence_refs": [None, "review:human-approved"],
            "memory_admission_approval_refs": [None, "approval:operator:jason"],
        }
    )

    request = build_multica_memory_admission_request(issue, _event())
    decision = evaluate_multica_memory_admission(issue, _event())

    assert request.approval_refs == ("approval:multica:ZOE-124D", "approval:operator:jason")
    assert request.observation_traces[0].evidence_refs == ("multica:ZOE-124D", "review:human-approved")
    assert "None" not in decision.evidence_refs


def test_graphiti_target_from_multica_metadata_still_requires_relationship():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-125",
            "memory_admission_target_backends": [MemoryBackend.GRAPHITI.value],
        }
    )

    decision = evaluate_multica_memory_admission(issue, _event(scope=MemoryScope.PROJECT.value))

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert "graphiti_target_requires_relationship_or_supersession" in decision.blockers
    assert decision.required_approvals == ("memory_admission", "relational_truth")


def test_relational_candidate_can_be_admitted_to_graphiti_from_multica_metadata():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-126",
            "memory_admission_target_backends": [MemoryBackend.GRAPHITI.value],
        }
    )
    event = _event(
        event_id="mem_evt_graph_edge",
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

    decision = evaluate_multica_memory_admission(issue, event)

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_to_write_durable is True
    assert decision.allowed_backends == (MemoryBackend.GRAPHITI.value,)


def test_explicit_target_backends_override_multica_metadata():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-127",
            "memory_admission_target_backends": [MemoryBackend.GRAPHITI.value],
        }
    )

    decision = evaluate_multica_memory_admission(
        issue,
        _event(),
        target_backends=(MemoryBackend.MEMPALACE.value,),
    )

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_backends == (MemoryBackend.MEMPALACE.value,)


def test_none_items_are_ignored_in_metadata_target_backends():
    issue = _issue(
        {
            "memory_admission_approved": True,
            "memory_admission_review_id": "ZOE-128",
            "memory_admission_target_backends": [None, MemoryBackend.MEMPALACE.value],
        }
    )

    decision = evaluate_multica_memory_admission(issue, _event())

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_backends == (MemoryBackend.MEMPALACE.value,)
