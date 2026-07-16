import pytest
from pathlib import Path

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_promotion_handoff import (
    CAPABILITY_PROFILE_PROMOTION_HANDOFF_SOURCE,
    build_capability_profile_promotion_handoff_plan,
)
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan

pytestmark = pytest.mark.ci_safe


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


def _candidate(**overrides):
    values = {
        "capability_id": "hindsight_reflective_memory",
        "proposal_id": "proposal_hindsight_trust",
        "proposal_candidate_id": "hindsight_reflective_memory",
        "current_trust_level": "experimental",
        "proposed_trust_level": "assisted",
        "reason": "Verified retained outcome supports a reviewable promotion.",
        "evidence_refs": ("pytest:test_zoe_capability_profile_promotion_handoff", "approval:multica:ZOE-402"),
        "source_event_id": "event_hindsight_trust",
        "source_admission_id": "admit_hindsight_trust",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _review_result(*, approval_refs=("approval:multica:ZOE-402",), approved_ids=("hindsight_reflective_memory",)):
    return review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=approval_refs,
        approved_capability_ids=approved_ids,
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )


def test_profile_promotion_handoff_plan_closes_review_to_multica_packet_loop():
    plan = build_capability_profile_promotion_handoff_plan(
        _review_result(),
        source_text=_source_text(),
        pr_refs=("pr:402",),
        rollback_refs=("rollback:revert-pr-402",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion_handoff",),
        title="Promote reviewed Hindsight capability profile",
        parent_issue_id="ZOE-402",
        metadata={"operator": "test"},
    )

    assert plan.allowed_to_handoff is True
    assert plan.blockers == ()
    assert plan.metadata["source"] == CAPABILITY_PROFILE_PROMOTION_HANDOFF_SOURCE
    assert plan.metadata["extra"] == {"operator": "test"}
    assert plan.promotion_plan.metadata["extra"] == {"operator": "test"}
    assert plan.patch_plan.metadata["extra"] == {"operator": "test"}
    assert plan.handoff.metadata["extra"] == {"operator": "test"}
    assert plan.promotion_plan.allowed_to_write_patch is True
    assert plan.patch_plan.allowed_to_apply_patch is True
    assert plan.handoff.allowed_to_create_ticket is True
    assert plan.handoff.ticket_metadata["parent_issue_id"] == "ZOE-402"
    assert plan.handoff.ticket_metadata["profile_promotion"]["promoted_capability_ids"] == [
        "hindsight_reflective_memory"
    ]
    assert '-        trust_level="experimental",' in plan.handoff.patch_text
    assert '+        trust_level="assisted",' in plan.handoff.patch_text


def test_profile_promotion_handoff_plan_blocks_missing_pr_evidence_without_payload():
    plan = build_capability_profile_promotion_handoff_plan(
        _review_result(),
        source_text=_source_text(),
        pr_refs=(),
        rollback_refs=("rollback:revert-pr-402",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion_handoff",),
    )

    assert plan.allowed_to_handoff is False
    assert "missing_pr_refs" in plan.blockers
    assert "promotion_plan_not_applyable" in plan.blockers
    assert "patch_plan_not_applyable" in plan.blockers
    assert "handoff_not_createable" in plan.blockers
    assert plan.promotion_plan.records == ()
    assert plan.patch_plan.patch == ""
    assert plan.handoff.description == ""
    assert plan.handoff.ticket_metadata == {}


def test_profile_promotion_handoff_plan_blocks_stale_source_before_handoff_payload():
    stale_source = _source_text().replace('trust_level="experimental"', 'trust_level="assisted"')

    plan = build_capability_profile_promotion_handoff_plan(
        _review_result(),
        source_text=stale_source,
        pr_refs=("pr:402",),
        rollback_refs=("rollback:revert-pr-402",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion_handoff",),
    )

    assert plan.allowed_to_handoff is False
    assert "stale_source_trust_level:hindsight_reflective_memory" in plan.blockers
    assert "patch_plan_not_applyable" in plan.blockers
    assert "handoff_not_createable" in plan.blockers
    assert plan.handoff.description == ""


def test_profile_promotion_handoff_plan_serializes_nested_contract_state():
    plan = build_capability_profile_promotion_handoff_plan(
        _review_result(),
        source_text=_source_text(),
        pr_refs=("pr:402",),
        rollback_refs=("rollback:revert-pr-402",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion_handoff",),
    )

    payload = plan.to_dict()

    assert payload["allowed_to_handoff"] is True
    assert payload["promotion_plan"]["allowed_to_write_patch"] is True
    assert payload["patch_plan"]["allowed_to_apply_patch"] is True
    assert payload["handoff"]["allowed_to_create_ticket"] is True
    assert payload["metadata"]["source"] == CAPABILITY_PROFILE_PROMOTION_HANDOFF_SOURCE
