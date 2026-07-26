import pytest
from pathlib import Path

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_patch_writer import (
    CAPABILITY_PROFILE_PATCH_WRITER_SOURCE,
    CapabilityProfilePatchPlan,
    CapabilityProfilePatchRecord,
    build_capability_profile_patch_plan,
    render_capability_profile_patch,
)
from zoe_capability_profile_promotion import build_capability_profile_promotion_plan
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
        "evidence_refs": ("pytest:test_zoe_capability_profile_patch_writer", "approval:multica:ZOE-329"),
        "source_event_id": "event_hindsight_trust",
        "source_admission_id": "admit_hindsight_trust",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _promotion_plan():
    return build_capability_profile_promotion_plan(
        _review_result(),
        pr_refs=("pr:329",),
        rollback_refs=("rollback:revert-pr-329",),
        verification_refs=("pytest:test_zoe_capability_profile_patch_writer",),
    )


def _review_result():
    return review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-329",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )


def test_profile_patch_writer_renders_deterministic_unified_diff():
    plan = build_capability_profile_patch_plan(
        _promotion_plan(),
        source_text=_source_text(),
        metadata={"pr": 329},
    )

    patch = render_capability_profile_patch(plan)

    assert plan.allowed_to_apply_patch is True
    assert plan.blockers == ()
    assert plan.patched_capability_ids == ("hindsight_reflective_memory",)
    assert plan.records[0].metadata["source"] == CAPABILITY_PROFILE_PATCH_WRITER_SOURCE
    assert plan.source_sha256
    assert "--- a/services/zoe-data/zoe_capability_profile.py" in patch
    assert "+++ b/services/zoe-data/zoe_capability_profile.py" in patch
    assert '-        trust_level="experimental",' in patch
    assert '+        trust_level="assisted",' in patch


def test_profile_patch_writer_blocks_stale_source_trust_level():
    stale_source = _source_text().replace('trust_level="experimental",', 'trust_level="assisted",', 1)

    plan = build_capability_profile_patch_plan(
        _promotion_plan(),
        source_text=stale_source,
    )

    assert plan.allowed_to_apply_patch is False
    assert plan.patch == ""
    assert plan.records == ()
    assert "stale_source_trust_level:hindsight_reflective_memory" in plan.blockers
    with pytest.raises(ValueError, match="blocked plan"):
        render_capability_profile_patch(plan)


def test_profile_patch_writer_blocks_missing_source_profile():
    source = _source_text().replace('capability_id="hindsight_reflective_memory"', 'capability_id="missing_hindsight"', 1)

    plan = build_capability_profile_patch_plan(
        _promotion_plan(),
        source_text=source,
    )

    assert plan.allowed_to_apply_patch is False
    assert "missing_source_profile:hindsight_reflective_memory" in plan.blockers


def test_profile_patch_writer_blocks_unapplyable_promotion_plan():
    blocked_promotion = build_capability_profile_promotion_plan(
        _review_result(),
        pr_refs=(),
        rollback_refs=(),
        verification_refs=(),
    )

    plan = build_capability_profile_patch_plan(
        blocked_promotion,
        source_text=_source_text(),
    )

    assert plan.allowed_to_apply_patch is False
    assert "promotion_plan_not_applyable" in plan.blockers
    assert "missing_pr_refs" in plan.blockers
    assert plan.patch == ""


def test_profile_patch_writer_validates_record_and_plan_invariants():
    with pytest.raises(ValueError, match="patch must change"):
        CapabilityProfilePatchRecord(
            capability_id="hindsight_reflective_memory",
            from_trust_level="assisted",
            to_trust_level="assisted",
            target_path="services/zoe-data/zoe_capability_profile.py",
            source_line=1,
        )

    record = build_capability_profile_patch_plan(_promotion_plan(), source_text=_source_text()).records[0]
    with pytest.raises(ValueError, match="blocked profile patch plans"):
        CapabilityProfilePatchPlan(
            target_path="services/zoe-data/zoe_capability_profile.py",
            source_sha256="abc",
            patch="diff",
            records=(record,),
            blockers=("manual_blocker",),
        )
