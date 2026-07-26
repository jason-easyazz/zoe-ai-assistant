import json

import httpx
import pytest

from hindsight_memory import HindsightConfig, HindsightMemoryClient
from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, build_capability_trust_update_plan
from zoe_evolution_outcome_retain import evaluate_and_retain_evolution_outcome_in_hindsight
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType

pytestmark = pytest.mark.ci_safe


def _candidate():
    return CandidateEvaluation(
        candidate_id="hindsight_reflective_memory",
        name="Hindsight reflective memory",
        source="existing_zoe",
        task="experience recall",
        score=CandidateScore(
            fit=5,
            activity=4,
            license=5,
            offline=5,
            security=5,
            footprint=3,
            tests=5,
            maintainability=4,
            overlap=4,
        ),
        evidence_refs=("docs:hindsight-bakeoff",),
        license_risk="compatible",
        offline_viability="required",
        recommendation="keep",
    )


def _signal():
    return EvolutionSignal(
        signal_id="signal_hindsight_trust",
        signal_type=EvolutionSignalType.USER_REQUEST.value,
        summary="User asked Zoe to retain verified self-evolution outcomes.",
        source="chat",
        evidence_refs=("chat:self-evolution-outcomes",),
        user_id="jason",
        scope="project",
    )


def _proposal(status=ProposalStatus.VERIFIED.value):
    return build_evolution_proposal(
        proposal_id="proposal_hindsight_trust",
        title="Trust retained Hindsight outcomes",
        problem_statement="Zoe needs evidence before promoting capability trust.",
        signals=(_signal(),),
        candidate=_candidate(),
        affected_capabilities=("hindsight_reflective_memory", "self_evolution_loop"),
        autonomy_class=TrustAutonomyClass.PROMOTE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Zoe can review capability trust after durable retained evidence exists.",
        verification_plan=("pytest:test_zoe_capability_trust_update",),
        rollback_plan="Do not promote capability trust.",
        approval_required=("pr_evidence", "memory_admission"),
        status=status,
    )


def _trace(**overrides):
    values = {
        "trace_id": "trace_hindsight_trust_verified",
        "trace_type": ObservationTraceType.VERIFICATION.value,
        "surface": "multica",
        "scope": "project",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Outcome retain bridge tests passed.",
        "evidence_refs": ("pytest:test_zoe_capability_trust_update", "pr:318"),
        "user_id": "jason",
        "subject_id": "proposal_hindsight_trust",
        "confidence": 0.93,
    }
    values.update(overrides)
    return ObservationTrace(**values)


async def _retained_outcome(proposal=None, trace=None):
    seen = {}

    async def handler(request):
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"success": True, "items_count": 1})

    config = HindsightConfig(enabled=True, bank_prefix="zoe-test", async_retain=False)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await evaluate_and_retain_evolution_outcome_in_hindsight(
            proposal or _proposal(),
            (trace or _trace(),),
            approval_refs=("approval:multica:ZOE-318",),
            client=client,
        )
    return result, seen


@pytest.mark.asyncio
async def test_capability_trust_update_candidates_require_retained_verified_outcome():
    outcome, seen = await _retained_outcome()

    plan = build_capability_trust_update_plan(outcome)

    assert plan.allowed_to_propose is True
    assert plan.blockers == ()
    assert len(plan.candidates) == 2
    by_id = {candidate.capability_id: candidate for candidate in plan.candidates}
    assert by_id["hindsight_reflective_memory"].current_trust_level == "experimental"
    assert by_id["hindsight_reflective_memory"].proposed_trust_level == "assisted"
    assert by_id["self_evolution_loop"].current_trust_level == "unknown"
    assert by_id["self_evolution_loop"].proposed_trust_level == "experimental"
    assert "approval:multica:ZOE-318" in by_id["hindsight_reflective_memory"].evidence_refs
    assert by_id["hindsight_reflective_memory"].source_event_id == seen["payload"]["items"][0]["document_id"]
    assert by_id["hindsight_reflective_memory"].retained_backend == "zoe-test-project-jason"


@pytest.mark.asyncio
async def test_capability_trust_update_blocks_unretained_outcome():
    outcome = await evaluate_and_retain_evolution_outcome_in_hindsight(
        _proposal(),
        (_trace(),),
        approval_refs=("approval:multica:ZOE-318",),
        config=HindsightConfig(enabled=False),
    )

    plan = build_capability_trust_update_plan(outcome)

    assert plan.allowed_to_propose is False
    assert plan.candidates == ()
    assert "outcome_not_retained" in plan.blockers


@pytest.mark.asyncio
async def test_capability_trust_update_blocks_failed_proposal():
    failed_trace = _trace(
        trace_id="trace_hindsight_trust_failed",
        outcome=ObservationOutcome.FAILED.value,
        summary="Verification failed.",
    )
    outcome = await evaluate_and_retain_evolution_outcome_in_hindsight(
        _proposal(status=ProposalStatus.FAILED.value),
        (failed_trace,),
        approval_refs=("approval:multica:ZOE-319",),
        config=HindsightConfig(enabled=True),
    )

    plan = build_capability_trust_update_plan(outcome)

    assert plan.allowed_to_propose is False
    assert "proposal_not_verified" in plan.blockers
    assert "admission_not_durable" in plan.blockers


@pytest.mark.asyncio
async def test_capability_trust_update_metadata_is_read_only_and_serializable():
    outcome, _seen = await _retained_outcome()

    plan = build_capability_trust_update_plan(outcome)
    candidate = plan.candidates[0]

    with pytest.raises(TypeError):
        candidate.metadata["source"] = "mutated"
    payload = plan.to_dict()
    assert payload["allowed_to_propose"] is True
    assert payload["candidates"][0]["metadata"]["source"] == "evolution_outcome_retain"


@pytest.mark.asyncio
async def test_capability_trust_update_skips_privileged_noop_candidate():
    proposal = build_evolution_proposal(
        proposal_id="proposal_privileged_trust",
        title="Keep Multica privilege intact",
        problem_statement="Privileged capabilities should never receive demotion candidates.",
        signals=(_signal(),),
        candidate=_candidate(),
        affected_capabilities=("multica_governance",),
        autonomy_class=TrustAutonomyClass.PROMOTE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Zoe can review privileged evidence without lowering the trust class.",
        verification_plan=("pytest:test_zoe_capability_trust_update",),
        rollback_plan="Do not change Multica trust.",
        approval_required=("pr_evidence", "memory_admission"),
        status=ProposalStatus.VERIFIED.value,
    )
    trace = _trace(subject_id="proposal_privileged_trust")
    outcome, _seen = await _retained_outcome(proposal=proposal, trace=trace)

    plan = build_capability_trust_update_plan(outcome)

    assert plan.allowed_to_propose is False
    assert plan.candidates == ()


@pytest.mark.asyncio
async def test_capability_trust_update_skips_trusted_noop_candidate():
    proposal = build_evolution_proposal(
        proposal_id="proposal_trusted_noop",
        title="Avoid trusted trust no-op",
        problem_statement="Already trusted capabilities should not create no-op trust candidates.",
        signals=(_signal(),),
        candidate=_candidate(),
        affected_capabilities=("chat_router",),
        autonomy_class=TrustAutonomyClass.PROMOTE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Reviewers only see meaningful trust promotions.",
        verification_plan=("pytest:test_zoe_capability_trust_update",),
        rollback_plan="Do not change chat router trust.",
        approval_required=("pr_evidence", "memory_admission"),
        status=ProposalStatus.VERIFIED.value,
    )
    trace = _trace(subject_id="proposal_trusted_noop")
    outcome, _seen = await _retained_outcome(proposal=proposal, trace=trace)

    plan = build_capability_trust_update_plan(outcome)

    assert plan.allowed_to_propose is False
    assert plan.candidates == ()


def test_capability_trust_update_candidate_validates_trust_levels():
    with pytest.raises(ValueError, match="unknown proposed_trust_level"):
        CapabilityTrustUpdateCandidate(
            capability_id="hindsight_reflective_memory",
            proposal_id="proposal_hindsight_trust",
            proposal_candidate_id="hindsight_reflective_memory",
            current_trust_level="experimental",
            proposed_trust_level="magic",
            reason="invalid trust level",
            evidence_refs=("pytest:test_zoe_capability_trust_update",),
            source_event_id="event_invalid_trust",
            source_admission_id="admit_invalid_trust",
            retained_backend="zoe-test-project-jason",
            metadata={},
        )

    with pytest.raises(ValueError, match="would demote"):
        CapabilityTrustUpdateCandidate(
            capability_id="multica_governance",
            proposal_id="proposal_hindsight_trust",
            proposal_candidate_id="hindsight_reflective_memory",
            current_trust_level="privileged",
            proposed_trust_level="trusted",
            reason="demotion should fail",
            evidence_refs=("pytest:test_zoe_capability_trust_update",),
            source_event_id="event_demote",
            source_admission_id="admit_demote",
            retained_backend="zoe-test-project-jason",
            metadata={},
        )
