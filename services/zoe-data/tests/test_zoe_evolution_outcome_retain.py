import json

import httpx
import pytest

from hindsight_memory import HindsightConfig, HindsightMemoryClient
from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_outcome_retain import (
    evaluate_and_retain_evolution_outcome_in_hindsight,
    retain_admitted_evolution_outcome_in_hindsight,
)
from zoe_evolution_outcome_admission import evaluate_evolution_outcome_admission
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)
from zoe_memory_admission import MemoryAdmissionStatus
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


def _proposal(status=ProposalStatus.VERIFIED.value):
    return build_evolution_proposal(
        proposal_id="proposal_memory_gate",
        title="Retain verified evolution outcomes",
        problem_statement="Zoe needs retained evidence for changes that worked or failed.",
        signals=(_signal(),),
        candidate=_candidate(),
        affected_capabilities=("memory_admission", "self_evolution_loop"),
        autonomy_class=TrustAutonomyClass.PROMOTE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Zoe learns from verified outcomes without blind durable writes.",
        verification_plan=("pytest:test_zoe_evolution_outcome_retain",),
        rollback_plan="Do not retain the outcome candidate.",
        approval_required=("pr_evidence", "memory_admission"),
        status=status,
    )


def _verification_trace(**overrides):
    values = {
        "trace_id": "trace_verify_memory_gate",
        "trace_type": ObservationTraceType.VERIFICATION.value,
        "surface": "multica",
        "scope": "project",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Focused tests and validators passed.",
        "evidence_refs": ("pytest:test_zoe_evolution_outcome_retain", "pr:316"),
        "user_id": "jason",
        "subject_id": "proposal_memory_gate",
        "confidence": 0.92,
    }
    values.update(overrides)
    return ObservationTrace(**values)


@pytest.mark.asyncio
async def test_retain_admitted_evolution_outcome_refuses_pending_admission():
    admission = evaluate_evolution_outcome_admission(_proposal(), (_verification_trace(),))

    result = await retain_admitted_evolution_outcome_in_hindsight(admission, config=HindsightConfig(enabled=False))

    assert admission.decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert result.execution is None
    assert result.retained is False
    assert "does not allow durable write" in result.reason


@pytest.mark.asyncio
async def test_approved_evolution_outcome_does_not_write_when_hindsight_disabled():
    result = await evaluate_and_retain_evolution_outcome_in_hindsight(
        _proposal(),
        (_verification_trace(),),
        approval_refs=("approval:multica:ZOE-316",),
        config=HindsightConfig(enabled=False),
    )

    assert result.admission.decision.status == MemoryAdmissionStatus.APPROVED.value
    assert result.execution is not None
    assert result.execution.attempted is False
    assert result.retained is False
    assert result.reason == "disabled"


@pytest.mark.asyncio
async def test_wrapper_returns_non_write_result_for_pending_admission():
    result = await evaluate_and_retain_evolution_outcome_in_hindsight(
        _proposal(),
        (_verification_trace(),),
        config=HindsightConfig(enabled=True),
    )

    assert result.admission.decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert result.execution is None
    assert result.retained is False
    assert "does not allow durable write" in result.reason


@pytest.mark.asyncio
async def test_approved_evolution_outcome_retains_exact_hindsight_payload():
    seen = {}

    async def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"success": True, "items_count": 1})

    config = HindsightConfig(enabled=True, bank_prefix="zoe-test", async_retain=False)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await evaluate_and_retain_evolution_outcome_in_hindsight(
            _proposal(),
            (_verification_trace(),),
            approval_refs=("approval:multica:ZOE-316",),
            client=client,
        )

    assert seen["path"] == "/v1/default/banks/zoe-test-project-jason/memories"
    assert seen["payload"]["async"] is False
    assert seen["payload"]["items"][0]["document_id"] == "mem_evt_evolution_outcome_proposal_memory_gate"
    assert seen["payload"]["items"][0]["content"].startswith("Zoe evolution proposal proposal_memory_gate ended as verified")
    assert result.retained is True
    assert result.reason == "retained"
    assert "approval:multica:ZOE-316" in result.execution.evidence_refs


@pytest.mark.asyncio
async def test_failed_evolution_outcome_stays_blocked_and_unexecuted():
    failed_trace = _verification_trace(
        trace_id="trace_failed_memory_gate",
        outcome=ObservationOutcome.FAILED.value,
        summary="Verification failed.",
        evidence_refs=("pytest:failed-memory-gate",),
    )
    admission = evaluate_evolution_outcome_admission(
        _proposal(status=ProposalStatus.FAILED.value),
        (failed_trace,),
        approval_refs=("approval:multica:ZOE-317",),
    )

    result = await retain_admitted_evolution_outcome_in_hindsight(admission, config=HindsightConfig(enabled=True))

    assert admission.decision.status == MemoryAdmissionStatus.BLOCKED.value
    assert result.execution is None
    assert result.retained is False
    assert "does not allow durable write" in result.reason
