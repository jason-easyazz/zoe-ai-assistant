import json
from pathlib import Path

import httpx
import pytest

from hindsight_memory import HindsightConfig, HindsightMemoryClient
from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_capability_outcome_profile_handoff import (
    CAPABILITY_OUTCOME_PROFILE_HANDOFF_SOURCE,
    build_capability_outcome_profile_handoff_plan,
)
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


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


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
        signal_id="signal_hindsight_profile_handoff",
        signal_type=EvolutionSignalType.USER_REQUEST.value,
        summary="User asked Zoe to promote capability trust only after retained evidence.",
        source="chat",
        evidence_refs=("chat:capability-profile-handoff",),
        user_id="jason",
        scope="project",
    )


def _proposal(status=ProposalStatus.VERIFIED.value):
    return build_evolution_proposal(
        proposal_id="proposal_hindsight_profile_handoff",
        title="Promote retained Hindsight outcome through profile handoff",
        problem_statement="Zoe needs one governed path from retained outcomes to profile promotion handoffs.",
        signals=(_signal(),),
        candidate=_candidate(),
        affected_capabilities=("hindsight_reflective_memory", "self_evolution_loop"),
        autonomy_class=TrustAutonomyClass.PROMOTE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Zoe can prepare profile promotions from retained outcomes without direct writes.",
        verification_plan=("pytest:test_zoe_capability_outcome_profile_handoff",),
        rollback_plan="Do not promote capability trust.",
        approval_required=("pr_evidence", "memory_admission"),
        status=status,
    )


def _trace(**overrides):
    values = {
        "trace_id": "trace_hindsight_profile_handoff_verified",
        "trace_type": ObservationTraceType.VERIFICATION.value,
        "surface": "multica",
        "scope": "project",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Outcome profile handoff tests passed.",
        "evidence_refs": ("pytest:test_zoe_capability_outcome_profile_handoff", "pr:406"),
        "user_id": "jason",
        "subject_id": "proposal_hindsight_profile_handoff",
        "confidence": 0.94,
    }
    values.update(overrides)
    return ObservationTrace(**values)


async def _retained_outcome(proposal=None, trace=None, *, enabled=True):
    seen = {}

    async def handler(request):
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"success": True, "items_count": 1})

    config = HindsightConfig(enabled=enabled, bank_prefix="zoe-test", async_retain=False)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await evaluate_and_retain_evolution_outcome_in_hindsight(
            proposal or _proposal(),
            (trace or _trace(),),
            approval_refs=("approval:memory:ZOE-406",),
            client=client,
        )
    return result, seen


@pytest.mark.asyncio
async def test_outcome_profile_handoff_closes_retained_outcome_to_multica_packet_loop():
    outcome, seen = await _retained_outcome()

    plan = build_capability_outcome_profile_handoff_plan(
        outcome,
        source_text=_source_text(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-406",),
        approved_capability_ids=("hindsight_reflective_memory",),
        rejected_capability_ids=("self_evolution_loop",),
        pr_refs=("pr:406",),
        rollback_refs=("rollback:revert-pr-406",),
        verification_refs=("pytest:test_zoe_capability_outcome_profile_handoff",),
        title="Promote retained Hindsight capability profile",
        parent_issue_id="ZOE-406",
        metadata={"operator": "test"},
    )

    assert plan.allowed_to_handoff is True
    assert plan.blockers == ()
    assert plan.metadata["source"] == CAPABILITY_OUTCOME_PROFILE_HANDOFF_SOURCE
    assert plan.trust_update_plan.allowed_to_propose is True
    assert len(plan.trust_update_plan.candidates) == 2
    assert plan.trust_update_plan.candidates[0].source_event_id == seen["payload"]["items"][0]["document_id"]
    assert plan.review_result.allowed_to_apply is True
    assert plan.review_result.applied_capability_ids == ("hindsight_reflective_memory",)
    assert plan.promotion_handoff_plan.allowed_to_handoff is True
    assert plan.promotion_handoff_plan.handoff.allowed_to_create_ticket is True
    assert plan.promotion_handoff_plan.handoff.ticket_metadata["parent_issue_id"] == "ZOE-406"
    assert plan.promotion_handoff_plan.handoff.ticket_metadata["profile_promotion"]["promoted_capability_ids"] == [
        "hindsight_reflective_memory"
    ]
    assert '-        trust_level="experimental",' in plan.promotion_handoff_plan.handoff.patch_text
    assert '+        trust_level="assisted",' in plan.promotion_handoff_plan.handoff.patch_text


@pytest.mark.asyncio
async def test_outcome_profile_handoff_blocks_without_trust_review_approval_refs():
    outcome, _seen = await _retained_outcome()

    plan = build_capability_outcome_profile_handoff_plan(
        outcome,
        source_text=_source_text(),
        reviewer_id="multica:reviewer",
        approval_refs=(),
        approved_capability_ids=("hindsight_reflective_memory",),
        pr_refs=("pr:406",),
        rollback_refs=("rollback:revert-pr-406",),
        verification_refs=("pytest:test_zoe_capability_outcome_profile_handoff",),
    )

    assert plan.allowed_to_handoff is False
    assert "missing_approval_refs" in plan.blockers
    assert "trust_review_not_applyable" in plan.blockers
    assert "profile_promotion_handoff_not_createable" in plan.blockers
    assert plan.promotion_handoff_plan.handoff.ticket_metadata == {}


@pytest.mark.asyncio
async def test_outcome_profile_handoff_blocks_without_pr_evidence_before_payload():
    outcome, _seen = await _retained_outcome()

    plan = build_capability_outcome_profile_handoff_plan(
        outcome,
        source_text=_source_text(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-406",),
        approved_capability_ids=("hindsight_reflective_memory",),
        pr_refs=(),
        rollback_refs=("rollback:revert-pr-406",),
        verification_refs=("pytest:test_zoe_capability_outcome_profile_handoff",),
    )

    assert plan.allowed_to_handoff is False
    assert "missing_pr_refs" in plan.blockers
    assert "profile_promotion_handoff_not_createable" in plan.blockers
    assert plan.promotion_handoff_plan.promotion_plan.records == ()
    assert plan.promotion_handoff_plan.patch_plan.patch == ""
    assert plan.promotion_handoff_plan.handoff.description == ""


@pytest.mark.asyncio
async def test_outcome_profile_handoff_blocks_unretained_outcome():
    outcome, _seen = await _retained_outcome(enabled=False)

    plan = build_capability_outcome_profile_handoff_plan(
        outcome,
        source_text=_source_text(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-406",),
        approved_capability_ids=("hindsight_reflective_memory",),
        pr_refs=("pr:406",),
        rollback_refs=("rollback:revert-pr-406",),
        verification_refs=("pytest:test_zoe_capability_outcome_profile_handoff",),
    )

    assert plan.allowed_to_handoff is False
    assert "outcome_not_retained" in plan.blockers
    assert "trust_update_not_proposable" in plan.blockers
    assert plan.trust_update_plan.candidates == ()
    assert plan.promotion_handoff_plan.handoff.ticket_metadata == {}


@pytest.mark.asyncio
async def test_outcome_profile_handoff_serializes_nested_contract_state():
    outcome, _seen = await _retained_outcome()

    plan = build_capability_outcome_profile_handoff_plan(
        outcome,
        source_text=_source_text(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-406",),
        approved_capability_ids=("hindsight_reflective_memory",),
        rejected_capability_ids=("self_evolution_loop",),
        pr_refs=("pr:406",),
        rollback_refs=("rollback:revert-pr-406",),
        verification_refs=("pytest:test_zoe_capability_outcome_profile_handoff",),
    )

    payload = plan.to_dict()

    assert payload["allowed_to_handoff"] is True
    assert payload["metadata"]["source"] == CAPABILITY_OUTCOME_PROFILE_HANDOFF_SOURCE
    assert payload["trust_update_plan"]["allowed_to_propose"] is True
    assert payload["review_result"]["allowed_to_apply"] is True
    assert payload["promotion_handoff_plan"]["handoff"]["allowed_to_create_ticket"] is True
