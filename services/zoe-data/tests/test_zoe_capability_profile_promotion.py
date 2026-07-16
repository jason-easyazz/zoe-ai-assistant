import json

import pytest

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_promotion import (
    CAPABILITY_PROFILE_PROMOTION_SOURCE,
    CapabilityProfilePromotionPlan,
    CapabilityProfilePromotionRecord,
    build_capability_profile_promotion_plan,
    render_capability_profile_promotion_manifest,
)
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan

pytestmark = pytest.mark.ci_safe


def _candidate(**overrides):
    values = {
        "capability_id": "hindsight_reflective_memory",
        "proposal_id": "proposal_hindsight_trust",
        "proposal_candidate_id": "hindsight_reflective_memory",
        "current_trust_level": "experimental",
        "proposed_trust_level": "assisted",
        "reason": "Verified retained outcome supports a reviewable promotion.",
        "evidence_refs": ("pytest:test_zoe_capability_profile_promotion", "approval:multica:ZOE-326"),
        "source_event_id": "event_hindsight_trust",
        "source_admission_id": "admit_hindsight_trust",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _review_result():
    plan = CapabilityTrustUpdatePlan(candidates=(_candidate(),))
    return review_capability_trust_update_plan(
        plan,
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-326",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )


def test_profile_promotion_plan_requires_clean_review_and_pr_evidence():
    review = _review_result()

    plan = build_capability_profile_promotion_plan(
        review,
        pr_refs=("pr:326",),
        rollback_refs=("rollback:revert-pr-326",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion",),
    )

    assert plan.allowed_to_write_patch is True
    assert plan.blockers == ()
    assert plan.promoted_capability_ids == ("hindsight_reflective_memory",)
    record = plan.records[0]
    assert record.capability_id == "hindsight_reflective_memory"
    assert record.from_trust_level == "experimental"
    assert record.to_trust_level == "assisted"
    assert record.profile_snapshot["trust_level"] == "assisted"
    assert "pr:326" in record.pr_refs
    assert "rollback:revert-pr-326" in record.rollback_refs
    assert record.metadata["source"] == CAPABILITY_PROFILE_PROMOTION_SOURCE


def test_profile_promotion_manifest_is_deterministic_json():
    plan = build_capability_profile_promotion_plan(
        _review_result(),
        pr_refs=("pr:326",),
        rollback_refs=("rollback:revert-pr-326",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion",),
    )

    rendered = render_capability_profile_promotion_manifest(plan)
    payload = json.loads(rendered)

    assert rendered.endswith("\n")
    assert payload["allowed_to_write_patch"] is True
    assert payload["records"][0]["capability_id"] == "hindsight_reflective_memory"
    assert payload["records"][0]["profile_snapshot"]["trust_level"] == "assisted"


def test_profile_promotion_plan_only_manifests_approved_decisions():
    approved = _candidate()
    rejected = _candidate(
        capability_id="openclaw_fallback",
        proposal_id="proposal_openclaw_trust",
        proposal_candidate_id="openclaw_fallback",
        current_trust_level="assisted",
        proposed_trust_level="trusted",
        source_event_id="event_openclaw_trust",
        source_admission_id="admit_openclaw_trust",
    )
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(approved, rejected)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-326",),
        approved_capability_ids=("hindsight_reflective_memory",),
        rejected_capability_ids=("openclaw_fallback",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    plan = build_capability_profile_promotion_plan(
        review,
        pr_refs=("pr:326",),
        rollback_refs=("rollback:revert-pr-326",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion",),
    )

    assert plan.allowed_to_write_patch is True
    assert plan.promoted_capability_ids == ("hindsight_reflective_memory",)
    assert len(plan.records) == 1
    assert plan.records[0].capability_id == "hindsight_reflective_memory"
    assert "openclaw_fallback" not in plan.promoted_capability_ids


def test_profile_promotion_plan_blocks_missing_pr_rollback_or_verification_refs():
    plan = build_capability_profile_promotion_plan(
        _review_result(),
        pr_refs=(),
        rollback_refs=(),
        verification_refs=(),
    )

    assert plan.allowed_to_write_patch is False
    assert plan.records == ()
    assert "missing_pr_refs" in plan.blockers
    assert "missing_rollback_refs" in plan.blockers
    assert "missing_verification_refs" in plan.blockers
    with pytest.raises(ValueError, match="blocked plan"):
        render_capability_profile_promotion_manifest(plan)


def test_profile_promotion_plan_blocks_unclean_review_result():
    candidate = _candidate(capability_id="self_evolution_loop", current_trust_level="unknown", proposed_trust_level="experimental")
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(candidate,)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-326",),
        approved_capability_ids=("self_evolution_loop",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    plan = build_capability_profile_promotion_plan(
        review,
        pr_refs=("pr:326",),
        rollback_refs=("rollback:revert-pr-326",),
        verification_refs=("pytest:test_zoe_capability_profile_promotion",),
    )

    assert review.allowed_to_apply is False
    assert plan.allowed_to_write_patch is False
    assert "review_not_applyable" in plan.blockers
    assert "missing_review_profiles" in plan.blockers


def test_profile_promotion_plan_validates_record_and_plan_invariants():
    review = _review_result()
    promoted_profile = review.profiles[0].to_dict()

    with pytest.raises(ValueError, match="promotion must change"):
        CapabilityProfilePromotionRecord(
            capability_id="hindsight_reflective_memory",
            from_trust_level="assisted",
            to_trust_level="assisted",
            target_path="services/zoe-data/zoe_capability_profile.py",
            decision_id="trust_review_proposal_hindsight_trust_hindsight_reflective_memory",
            proposal_id="proposal_hindsight_trust",
            approval_refs=("approval:multica:ZOE-326",),
            evidence_refs=("pytest:test_zoe_capability_profile_promotion",),
            pr_refs=("pr:326",),
            rollback_refs=("rollback:revert-pr-326",),
            verification_refs=("pytest:test_zoe_capability_profile_promotion",),
            profile_snapshot={**promoted_profile, "capability_id": "hindsight_reflective_memory", "trust_level": "assisted"},
        )

    with pytest.raises(ValueError, match="blocked profile promotion plans"):
        CapabilityProfilePromotionPlan(
            target_path="services/zoe-data/zoe_capability_profile.py",
            records=(
                build_capability_profile_promotion_plan(
                    review,
                    pr_refs=("pr:326",),
                    rollback_refs=("rollback:revert-pr-326",),
                    verification_refs=("pytest:test_zoe_capability_profile_promotion",),
                ).records[0],
            ),
            blockers=("manual_blocker",),
        )
