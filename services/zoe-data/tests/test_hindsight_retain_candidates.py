import pytest

from hindsight_retain_candidates import (
    HINDSIGHT_RETAIN_SOURCE,
    HindsightRetainAdmissionError,
    admit_hindsight_retain_candidate,
    build_admitted_hindsight_retain_plan,
    build_hindsight_retain_admission_request,
    build_hindsight_retain_candidate,
    create_hindsight_retain_candidate,
    evaluate_hindsight_retain_candidate_admission,
)
from hindsight_memory import HindsightConfig
from zoe_memory_admission import MemoryAdmissionDecision, MemoryAdmissionError, MemoryAdmissionStatus
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


def _event():
    return MemoryEvent(
        event_id="mem_evt_candidate",
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FAILURE.value,
        content="The weather card failed because duplicate voice queue emits raced.",
        entities=("weather_card", "voice_queue"),
        relationships=(
            MemoryRelationship(
                relationship_type=RelationshipType.FAILED_ON.value,
                source="weather_card",
                target="mobile_dashboard_render",
            ),
        ),
        evidence_refs=("trace:weather:001",),
        confidence=0.81,
    )


def _plain_event():
    return MemoryEvent(
        event_id="mem_evt_hindsight_pref",
        user_id="jason",
        scope=MemoryScope.PERSONAL.value,
        source=MemorySource.CHAT.value,
        event_type=MemoryEventType.PREFERENCE.value,
        content="Jason prefers Zoe memory to stay offline-only.",
        entities=("zoe_memory",),
        evidence_refs=("chat:offline-memory",),
        confidence=0.9,
    )


def _admission_trace(**overrides):
    values = {
        "trace_id": "trace_hindsight_retain_admit",
        "trace_type": ObservationTraceType.ADMISSION.value,
        "surface": "multica",
        "scope": MemoryScope.PROJECT.value,
        "user_id": "jason",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Hindsight retain candidate reviewed with evidence.",
        "evidence_refs": ("multica:retain-review",),
    }
    values.update(overrides)
    return ObservationTrace(**values)


def test_build_hindsight_retain_candidate_is_pending_and_evidence_tagged():
    candidate = build_hindsight_retain_candidate(_event())

    assert candidate["source"] == HINDSIGHT_RETAIN_SOURCE
    assert candidate["status"] == "pending"
    assert candidate["user_turn_id"] == "mem_evt_candidate"
    assert "hindsight-retain-candidate" in candidate["tags"]
    assert "evidence:trace:weather:001" in candidate["tags"]
    assert "relational" in candidate["tags"]
    assert candidate["metadata"]["relationships"][0]["relationship_type"] == "FAILED_ON"
    assert "zoe_hindsight_retain_candidate" in candidate["text"]
    assert '"relationship_type":"FAILED_ON"' in candidate["text"]
    assert '"evidence_refs":["trace:weather:001"]' in candidate["text"]


def test_build_hindsight_retain_admission_request_defaults_to_hindsight_target():
    request = build_hindsight_retain_admission_request(_event())

    assert request.admission_id == "admit_hindsight_retain_mem_evt_candidate"
    assert request.requested_by == HINDSIGHT_RETAIN_SOURCE
    assert request.target_backends == (MemoryBackend.HINDSIGHT.value,)
    assert request.candidate.event_id == "mem_evt_candidate"
    assert request.metadata["source"] == HINDSIGHT_RETAIN_SOURCE
    assert request.metadata["candidate_event_id"] == "mem_evt_candidate"
    assert request.metadata["extra"] == {}


def test_build_hindsight_retain_admission_request_keeps_caller_metadata_nested():
    request = build_hindsight_retain_admission_request(
        _event(),
        metadata={"source": "caller_override", "note": "operator review"},
    )

    assert request.metadata["source"] == HINDSIGHT_RETAIN_SOURCE
    assert request.metadata["candidate_event_id"] == "mem_evt_candidate"
    assert request.metadata["extra"] == {"source": "caller_override", "note": "operator review"}


def test_build_hindsight_retain_admission_request_rejects_invalid_backend():
    with pytest.raises(MemoryAdmissionError, match="not admitted write targets"):
        build_hindsight_retain_admission_request(
            _event(),
            target_backends=("cloud_memory",),
        )


def test_build_hindsight_retain_admission_request_rejects_mismatched_trace_user():
    with pytest.raises(MemoryAdmissionError, match="user_id must match"):
        build_hindsight_retain_admission_request(
            _plain_event(),
            observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value, user_id="someone_else"),),
        )


def test_hindsight_retain_candidate_admission_stays_pending_without_approval_or_trace():
    decision = evaluate_hindsight_retain_candidate_admission(_plain_event())

    assert decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert decision.allowed_to_keep_pending is True
    assert decision.allowed_to_write_durable is False
    assert decision.allowed_backends == ()
    assert decision.blockers == ("approval_required", "successful_admission_or_verification_trace_required")


def test_hindsight_retain_candidate_admission_can_approve_hindsight_with_evidence():
    decision = evaluate_hindsight_retain_candidate_admission(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )

    assert decision.status == MemoryAdmissionStatus.APPROVED.value
    assert decision.allowed_to_write_durable is True
    assert decision.allowed_backends == (MemoryBackend.HINDSIGHT.value,)
    assert decision.blockers == ()
    assert "chat:offline-memory" in decision.evidence_refs
    assert "multica:retain-review" in decision.evidence_refs
    assert "approval:multica:retain-review" in decision.evidence_refs


def test_admit_hindsight_retain_candidate_stub_is_pending_and_non_writing():
    result = admit_hindsight_retain_candidate(" mem_evt_candidate ")

    assert result.event_id == "mem_evt_candidate"
    assert result.admission_id == "admit_hindsight_retain_mem_evt_candidate"
    assert result.status == MemoryAdmissionStatus.PENDING_REVIEW
    assert result.allowed_to_write_durable is False
    assert result.reason == "admission_worker_not_wired"
    assert result.evidence_refs == ()
    assert result.side_effects == ()
    assert result.to_dict() == {
        "event_id": "mem_evt_candidate",
        "admission_id": "admit_hindsight_retain_mem_evt_candidate",
        "status": MemoryAdmissionStatus.PENDING_REVIEW.value,
        "allowed_to_write_durable": False,
        "reason": "admission_worker_not_wired",
        "evidence_refs": [],
        "side_effects": [],
    }


def test_admit_hindsight_retain_candidate_requires_event_id():
    with pytest.raises(HindsightRetainAdmissionError, match="event_id is required"):
        admit_hindsight_retain_candidate(" ")


def test_admit_hindsight_retain_candidate_rejects_non_string_event_id():
    with pytest.raises(TypeError, match="event_id must be str"):
        admit_hindsight_retain_candidate(None)  # type: ignore[arg-type]


def test_admitted_hindsight_retain_plan_requires_approved_hindsight_decision():
    request = build_hindsight_retain_admission_request(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )
    decision = evaluate_hindsight_retain_candidate_admission(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )

    plan = build_admitted_hindsight_retain_plan(
        request,
        decision,
        config=HindsightConfig(bank_prefix="zoe-test", async_retain=False),
    )

    payload = plan.to_dict()
    assert payload["admission_id"] == "admit_hindsight_retain_mem_evt_hindsight_pref"
    assert payload["event_id"] == "mem_evt_hindsight_pref"
    assert payload["bank_id"] == "zoe-test-personal-jason"
    assert payload["payload"]["async"] is False
    assert payload["payload"]["items"][0]["document_id"] == "mem_evt_hindsight_pref"
    assert payload["payload"]["items"][0]["content"] == "Jason prefers Zoe memory to stay offline-only."
    assert "approval:multica:retain-review" in payload["evidence_refs"]


def test_admitted_hindsight_retain_plan_payload_is_immutable():
    request = build_hindsight_retain_admission_request(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )
    decision = evaluate_hindsight_retain_candidate_admission(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )

    plan = build_admitted_hindsight_retain_plan(request, decision)

    with pytest.raises(TypeError):
        plan.payload["items"] = ()
    assert isinstance(plan.to_dict()["payload"]["items"], list)


def test_admitted_hindsight_retain_plan_defaults_to_env_config(monkeypatch):
    monkeypatch.setenv("HINDSIGHT_ENABLED", "false")
    monkeypatch.setenv("HINDSIGHT_BANK_PREFIX", "zoe-env")
    monkeypatch.setenv("HINDSIGHT_ASYNC_RETAIN", "false")
    request = build_hindsight_retain_admission_request(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )
    decision = evaluate_hindsight_retain_candidate_admission(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )

    plan = build_admitted_hindsight_retain_plan(request, decision)

    assert plan.bank_id == "zoe-env-personal-jason"
    assert plan.payload["async"] is False


def test_admitted_hindsight_retain_plan_rejects_pending_decision():
    request = build_hindsight_retain_admission_request(_plain_event())
    decision = evaluate_hindsight_retain_candidate_admission(_plain_event())

    with pytest.raises(HindsightRetainAdmissionError, match="does not allow durable write"):
        build_admitted_hindsight_retain_plan(request, decision)


def test_admitted_hindsight_retain_plan_rejects_wrong_backend():
    event = _event()
    request = build_hindsight_retain_admission_request(
        event,
        target_backends=(MemoryBackend.GRAPHITI.value,),
        observation_traces=(_admission_trace(),),
        approval_refs=("approval:multica:retain-review",),
    )
    decision = evaluate_hindsight_retain_candidate_admission(
        event,
        target_backends=(MemoryBackend.GRAPHITI.value,),
        observation_traces=(_admission_trace(),),
        approval_refs=("approval:multica:retain-review",),
    )

    with pytest.raises(HindsightRetainAdmissionError, match="does not target Hindsight"):
        build_admitted_hindsight_retain_plan(request, decision)


def test_admitted_hindsight_retain_plan_rejects_mismatched_decision():
    request = build_hindsight_retain_admission_request(
        _plain_event(),
        observation_traces=(_admission_trace(scope=MemoryScope.PERSONAL.value),),
        approval_refs=("approval:multica:retain-review",),
    )
    decision = MemoryAdmissionDecision(
        admission_id="admit_other",
        status=MemoryAdmissionStatus.APPROVED.value,
        allowed_to_keep_pending=True,
        allowed_to_write_durable=True,
        allowed_backends=(MemoryBackend.HINDSIGHT.value,),
        blockers=(),
        required_approvals=("memory_admission",),
        evidence_refs=("approval:multica:retain-review",),
    )

    with pytest.raises(HindsightRetainAdmissionError, match="does not match request"):
        build_admitted_hindsight_retain_plan(request, decision)


def test_hindsight_retain_candidate_admission_blocks_graphiti_without_relationships():
    event = MemoryEvent(
        event_id="mem_evt_plain_fact",
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FACT.value,
        content="Plain fact without graph relationship.",
        evidence_refs=("trace:plain:001",),
        confidence=0.8,
    )

    decision = evaluate_hindsight_retain_candidate_admission(
        event,
        target_backends=(MemoryBackend.GRAPHITI.value,),
        observation_traces=(_admission_trace(),),
        approval_refs=("approval:multica:retain-review",),
    )

    assert decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert decision.allowed_to_write_durable is False
    assert "graphiti_target_requires_relationship_or_supersession" in decision.blockers


@pytest.mark.asyncio
async def test_create_hindsight_retain_candidate_uses_memory_service_pending_ingest():
    seen = {}

    class FakeMemoryService:
        async def ingest(self, text, **kwargs):
            seen["text"] = text
            seen.update(kwargs)
            return {"id": "zoe_pending_mem"}

    result = await create_hindsight_retain_candidate(_event(), memory_service=FakeMemoryService())

    assert result == {"id": "zoe_pending_mem"}
    assert seen["source"] == HINDSIGHT_RETAIN_SOURCE
    assert "zoe_hindsight_retain_candidate" in seen["text"]
    assert '"relationship_type":"FAILED_ON"' in seen["text"]
    assert seen["status"] == "pending"
    assert seen["memory_type"] == "failure"
    assert seen["user_turn_id"] == "mem_evt_candidate"
    assert seen["scope"] == "project"
    assert seen["source_excerpt"].startswith("The weather card failed")
    assert seen["metadata"]["relationships"][0]["relationship_type"] == "FAILED_ON"
    assert seen["metadata"]["evidence_refs"] == ["trace:weather:001"]
    assert "hindsight-retain-candidate" in seen["tags"]
