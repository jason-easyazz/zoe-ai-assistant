from pathlib import Path

import pytest

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_multica_handoff import build_capability_profile_multica_handoff
from zoe_capability_profile_patch_writer import build_capability_profile_patch_plan
from zoe_capability_profile_pr_edit_gate import (
    CAPABILITY_PROFILE_PR_EDIT_GATE_SOURCE,
    CapabilityProfilePREditPlan,
    build_capability_profile_pr_edit_plan_from_ticket,
    render_capability_profile_pr_edit_patch,
)
from zoe_capability_profile_promotion import build_capability_profile_promotion_plan
from zoe_capability_profile_ticket_writer import create_capability_profile_handoff_ticket
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan

pytestmark = pytest.mark.ci_safe


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


def _candidate(**overrides):
    values = {
        "capability_id": "hindsight_reflective_memory",
        "proposal_id": "proposal_hindsight_pr_edit_gate",
        "proposal_candidate_id": "hindsight_reflective_memory",
        "current_trust_level": "experimental",
        "proposed_trust_level": "assisted",
        "reason": "Verified retained outcome supports a reviewable promotion.",
        "evidence_refs": ("pytest:test_zoe_capability_profile_pr_edit_gate", "approval:multica:ZOE-423"),
        "source_event_id": "event_hindsight_pr_edit_gate",
        "source_admission_id": "admit_hindsight_pr_edit_gate",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _handoff():
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-423",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    promotion = build_capability_profile_promotion_plan(
        review,
        pr_refs=("pr:423",),
        rollback_refs=("rollback:revert-pr-423",),
        verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
    )
    patch = build_capability_profile_patch_plan(promotion, source_text=_source_text())
    return build_capability_profile_multica_handoff(
        promotion,
        patch,
        title="Promote Hindsight reflective memory profile",
        parent_issue_id="ZOE-423",
    )


class FakeMulticaClient:
    def __init__(self):
        self.issue = {"id": "issue-423", "identifier": "ZOE-423"}
        self.create_calls = []

    async def create_issue(self, **kwargs):
        self.create_calls.append(kwargs)
        return dict(self.issue)


async def _created_ticket_description(handoff):
    client = FakeMulticaClient()
    result = await create_capability_profile_handoff_ticket(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-423",),
        evidence_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
        client=client,
    )
    assert result.created is True
    return client.create_calls[0]["description"]


@pytest.mark.asyncio
async def test_profile_pr_edit_gate_allows_ticket_backed_patch_with_pr_evidence():
    handoff = _handoff()
    description = await _created_ticket_description(handoff)

    plan = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-423",
        ticket_description=description,
        current_source_text=_source_text(),
        patch_text=handoff.patch_text,
        promotion_manifest=handoff.promotion_manifest,
        pr_refs=("https://github.com/jason-easyazz/zoe-ai-assistant/pull/423",),
        rollback_refs=("rollback:revert-pr-423",),
        verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
        greptile_refs=("greptile:pass:423",),
        metadata={"source_issue": "ZOE-423"},
    )

    assert plan.allowed_to_prepare_pr_edit is True
    assert plan.blockers == ()
    assert plan.ticket_id == "ZOE-423"
    assert plan.target_path == "services/zoe-data/zoe_capability_profile.py"
    assert plan.promoted_capability_ids == ("hindsight_reflective_memory",)
    assert plan.metadata["source"] == CAPABILITY_PROFILE_PR_EDIT_GATE_SOURCE
    assert plan.to_dict()["patch_text"] == handoff.patch_text
    assert render_capability_profile_pr_edit_patch(plan) == handoff.patch_text
    assert "+        trust_level=\"assisted\"," in plan.patch_text


@pytest.mark.asyncio
async def test_profile_pr_edit_gate_blocks_missing_greptile_evidence():
    handoff = _handoff()
    description = await _created_ticket_description(handoff)

    plan = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-423",
        ticket_description=description,
        current_source_text=_source_text(),
        patch_text=handoff.patch_text,
        promotion_manifest=handoff.promotion_manifest,
        pr_refs=("pr:423",),
        rollback_refs=("rollback:revert-pr-423",),
        verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
        greptile_refs=(),
    )

    assert plan.allowed_to_prepare_pr_edit is False
    assert "missing_greptile_refs" in plan.blockers
    assert plan.patch_text == ""
    with pytest.raises(ValueError, match="blocked plan"):
        render_capability_profile_pr_edit_patch(plan)


@pytest.mark.asyncio
async def test_profile_pr_edit_gate_blocks_stale_source_and_tampered_patch():
    handoff = _handoff()
    description = await _created_ticket_description(handoff)
    stale_source = _source_text().replace('trust_level="experimental",', 'trust_level="assisted",', 1)
    tampered_patch = handoff.patch_text.replace('trust_level="assisted",', 'trust_level="trusted",')

    plan = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-423",
        ticket_description=description,
        current_source_text=stale_source,
        patch_text=tampered_patch,
        promotion_manifest=handoff.promotion_manifest,
        pr_refs=("pr:423",),
        rollback_refs=("rollback:revert-pr-423",),
        verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
        greptile_refs=("greptile:pass:423",),
    )

    assert plan.allowed_to_prepare_pr_edit is False
    assert "stale_or_mismatched_source_sha256" in plan.blockers
    assert "patch_sha256_mismatch" in plan.blockers
    assert plan.promoted_capability_ids == ()


@pytest.mark.asyncio
async def test_profile_pr_edit_gate_reports_wrong_patch_target_header():
    handoff = _handoff()
    description = await _created_ticket_description(handoff)
    wrong_target_patch = handoff.patch_text.replace(
        "+++ b/services/zoe-data/zoe_capability_profile.py",
        "+++ b/services/zoe-data/other_profile.py",
        1,
    )

    plan = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-423",
        ticket_description=description,
        current_source_text=_source_text(),
        patch_text=wrong_target_patch,
        promotion_manifest=handoff.promotion_manifest,
        pr_refs=("pr:423",),
        rollback_refs=("rollback:revert-pr-423",),
        verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
        greptile_refs=("greptile:pass:423",),
    )

    assert plan.allowed_to_prepare_pr_edit is False
    assert "patch_sha256_mismatch" in plan.blockers
    assert "patch_target_missing_b_header" in plan.blockers
    assert "patch_target_missing_a_header" not in plan.blockers


def test_profile_pr_edit_gate_blocks_missing_ticket_metadata():
    plan = build_capability_profile_pr_edit_plan_from_ticket(
        ticket_id="ZOE-423",
        ticket_description="plain issue without Zoe block",
        current_source_text=_source_text(),
        patch_text="diff",
        promotion_manifest="{}",
        pr_refs=("pr:423",),
        rollback_refs=("rollback:revert-pr-423",),
        verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
        greptile_refs=("greptile:pass:423",),
    )

    assert plan.allowed_to_prepare_pr_edit is False
    assert plan.blockers == ("missing_zoe_ticket_metadata",)


def test_profile_pr_edit_plan_rejects_blockers_with_patch_text():
    with pytest.raises(ValueError, match="blocked profile PR edit plans"):
        CapabilityProfilePREditPlan(
            ticket_id="ZOE-423",
            target_path="services/zoe-data/zoe_capability_profile.py",
            patch_text="diff",
            promoted_capability_ids=("hindsight_reflective_memory",),
            pr_refs=("pr:423",),
            rollback_refs=("rollback:revert-pr-423",),
            verification_refs=("pytest:test_zoe_capability_profile_pr_edit_gate",),
            greptile_refs=("greptile:pass:423",),
            blockers=("manual_blocker",),
        )
