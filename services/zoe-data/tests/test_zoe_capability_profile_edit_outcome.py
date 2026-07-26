from pathlib import Path

import pytest

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_edit_outcome import (
    CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE,
    build_capability_profile_edit_outcome_plan,
)
from zoe_capability_profile_multica_handoff import build_capability_profile_multica_handoff
from zoe_capability_profile_patch_writer import build_capability_profile_patch_plan
from zoe_capability_profile_pr_edit_gate import build_capability_profile_pr_edit_plan_from_ticket
from zoe_capability_profile_promotion import build_capability_profile_promotion_plan
from zoe_capability_profile_ticket_writer import create_capability_profile_handoff_ticket
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan
from zoe_memory_admission import MemoryAdmissionStatus
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType

pytestmark = pytest.mark.ci_safe


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


def _candidate():
    return CapabilityTrustUpdateCandidate(
        capability_id="hindsight_reflective_memory",
        proposal_id="proposal_profile_edit_outcome",
        proposal_candidate_id="hindsight_reflective_memory",
        current_trust_level="experimental",
        proposed_trust_level="assisted",
        reason="Verified retained outcome supports a reviewable promotion.",
        evidence_refs=("pytest:test_zoe_capability_profile_edit_outcome", "approval:multica:ZOE-431"),
        source_event_id="event_profile_edit_outcome",
        source_admission_id="admit_profile_edit_outcome",
        retained_backend="zoe-project-jason",
        metadata={"source": "evolution_outcome_retain"},
    )


def _handoff():
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-431",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    promotion = build_capability_profile_promotion_plan(
        review,
        pr_refs=("pr:431",),
        rollback_refs=("rollback:revert-pr-431",),
        verification_refs=("pytest:test_zoe_capability_profile_edit_outcome",),
    )
    patch = build_capability_profile_patch_plan(promotion, source_text=_source_text())
    return build_capability_profile_multica_handoff(
        promotion,
        patch,
        title="Promote Hindsight reflective memory profile",
        parent_issue_id="ZOE-431",
    )


class FakeMulticaClient:
    def __init__(self):
        self.create_calls = []

    async def create_issue(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"id": "issue-431", "identifier": "ZOE-431"}


async def _allowed_pr_edit_plan():
    handoff = _handoff()
    client = FakeMulticaClient()
    result = await create_capability_profile_handoff_ticket(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-431",),
        evidence_refs=("pytest:test_zoe_capability_profile_edit_outcome",),
        client=client,
    )
    assert result.created is True
    return handoff, build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-431",
        ticket_description=client.create_calls[0]["description"],
        current_source_text=_source_text(),
        patch_text=handoff.patch_text,
        promotion_manifest=handoff.promotion_manifest,
        pr_refs=("https://github.com/jason-easyazz/zoe-ai-assistant/pull/431",),
        rollback_refs=("rollback:revert-pr-431",),
        verification_refs=("pytest:test_zoe_capability_profile_edit_outcome",),
        greptile_refs=("greptile:pass:431",),
    )


def _verification_trace(**overrides):
    values = {
        "trace_id": "trace_profile_edit_verified",
        "trace_type": ObservationTraceType.VERIFICATION.value,
        "surface": "multica",
        "scope": "project",
        "outcome": ObservationOutcome.SUCCESS.value,
        "summary": "Capability profile edit PR was reviewed, tested, and merged.",
        "evidence_refs": ("pr:431:merged", "pytest:test_zoe_capability_profile_edit_outcome"),
        "user_id": "zoe_system",
        "subject_id": "ZOE-431",
        "confidence": 0.95,
    }
    values.update(overrides)
    return ObservationTrace(**values)


@pytest.mark.asyncio
async def test_verified_profile_edit_outcome_builds_memory_admission_and_trust_records():
    handoff, pr_edit_plan = await _allowed_pr_edit_plan()

    plan = build_capability_profile_edit_outcome_plan(
        pr_edit_plan,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        approval_refs=("approval:memory-admission:ZOE-431",),
        promotion_manifest=handoff.promotion_manifest,
    )

    assert plan.blockers == ()
    assert plan.allowed_to_admit_memory is True
    assert plan.admission_decision.status == MemoryAdmissionStatus.APPROVED.value
    assert plan.admission_decision.allowed_backends == (MemoryBackend.HINDSIGHT.value, MemoryBackend.GRAPHITI.value)
    assert plan.memory_candidate.event_id == "mem_evt_profile_edit_outcome_ZOE-431"
    assert plan.memory_candidate.user_id == "zoe_system"
    assert plan.memory_candidate.metadata["source"] == CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE
    assert plan.memory_candidate.entities == (
        "ZOE-431",
        "services/zoe-data/zoe_capability_profile.py",
        "hindsight_reflective_memory",
    )
    assert any(rel.relationship_type == "TRUSTED_FOR" for rel in plan.memory_candidate.relationships)
    assert plan.trust_records[0].capability_id == "hindsight_reflective_memory"
    assert plan.trust_records[0].from_trust_level == "experimental"
    assert plan.trust_records[0].to_trust_level == "assisted"
    assert "greptile:pass:431" in plan.trust_records[0].evidence_refs


@pytest.mark.asyncio
async def test_profile_edit_outcome_without_memory_approval_stays_pending():
    handoff, pr_edit_plan = await _allowed_pr_edit_plan()

    plan = build_capability_profile_edit_outcome_plan(
        pr_edit_plan,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        promotion_manifest=handoff.promotion_manifest,
    )

    assert plan.allowed_to_admit_memory is False
    assert plan.admission_decision.status == MemoryAdmissionStatus.PENDING_REVIEW.value
    assert plan.admission_decision.blockers == ("approval_required",)
    assert plan.trust_records


@pytest.mark.asyncio
async def test_profile_edit_outcome_blocks_when_pr_edit_gate_was_blocked():
    _handoff, pr_edit_plan = await _allowed_pr_edit_plan()
    blocked = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-431",
        ticket_description="plain ticket without Zoe metadata",
        current_source_text=_source_text(),
        patch_text="diff",
        promotion_manifest="{}",
        pr_refs=("pr:431",),
        rollback_refs=("rollback:revert-pr-431",),
        verification_refs=("pytest:test_zoe_capability_profile_edit_outcome",),
        greptile_refs=("greptile:pass:431",),
    )

    plan = build_capability_profile_edit_outcome_plan(
        blocked,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        promotion_manifest="{}",
    )

    assert plan.allowed_to_admit_memory is False
    assert "pr_edit_plan_not_allowed" in plan.blockers
    assert plan.memory_candidate is None
    assert plan.admission_decision is None
    assert plan.trust_records == ()
    assert pr_edit_plan.allowed_to_prepare_pr_edit is True



@pytest.mark.asyncio
async def test_profile_edit_outcome_blocks_unsupported_target_backend_without_raising():
    handoff, pr_edit_plan = await _allowed_pr_edit_plan()

    plan = build_capability_profile_edit_outcome_plan(
        pr_edit_plan,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        promotion_manifest=handoff.promotion_manifest,
        target_backends=("cloud_memory",),
    )

    assert plan.allowed_to_admit_memory is False
    assert "unsupported_target_backend:cloud_memory" in plan.blockers
    assert plan.admission_decision is None
    assert plan.memory_candidate is None


@pytest.mark.asyncio
async def test_profile_edit_outcome_blocks_unchanged_trust_manifest_without_raising():
    handoff, pr_edit_plan = await _allowed_pr_edit_plan()
    manifest = handoff.promotion_manifest.replace('"to_trust_level": "assisted"', '"to_trust_level": "experimental"')

    plan = build_capability_profile_edit_outcome_plan(
        pr_edit_plan,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        promotion_manifest=manifest,
    )

    assert plan.allowed_to_admit_memory is False
    assert "unchanged_trust_level:hindsight_reflective_memory" in plan.blockers
    assert plan.trust_records == ()
    assert plan.memory_candidate is None


@pytest.mark.asyncio
async def test_profile_edit_outcome_blocks_from_trust_matching_default_to_trust_without_raising():
    handoff, pr_edit_plan = await _allowed_pr_edit_plan()
    manifest = handoff.promotion_manifest.replace('"from_trust_level": "experimental"', '"from_trust_level": "applied"')
    manifest = manifest.replace('"to_trust_level": "assisted",\n', '')

    plan = build_capability_profile_edit_outcome_plan(
        pr_edit_plan,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        promotion_manifest=manifest,
    )

    assert plan.allowed_to_admit_memory is False
    assert "unchanged_trust_level:hindsight_reflective_memory" in plan.blockers
    assert plan.trust_records == ()
    assert plan.memory_candidate is None

@pytest.mark.asyncio
async def test_profile_edit_outcome_rejects_invalid_manifest_before_memory_candidate():
    _handoff, pr_edit_plan = await _allowed_pr_edit_plan()

    plan = build_capability_profile_edit_outcome_plan(
        pr_edit_plan,
        verification_traces=(_verification_trace(),),
        user_id="zoe_system",
        promotion_manifest="not-json",
    )

    assert plan.allowed_to_admit_memory is False
    assert "invalid_promotion_manifest" in plan.blockers
    assert plan.memory_candidate is None
    assert plan.trust_records == ()
