from dataclasses import replace

import pytest

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES, CapabilityProfile
from zoe_capability_trust_review import (
    CAPABILITY_TRUST_REVIEW_SOURCE,
    CapabilityTrustReviewDecision,
    CapabilityTrustReviewResult,
    review_capability_trust_update_plan,
)
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
        "evidence_refs": ("pytest:test_zoe_capability_trust_review", "approval:multica:ZOE-320"),
        "source_event_id": "event_hindsight_trust",
        "source_admission_id": "admit_hindsight_trust",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _plan(*candidates, blockers=()):
    return CapabilityTrustUpdatePlan(candidates=tuple(candidates or (_candidate(),)), blockers=tuple(blockers))


def test_capability_trust_review_applies_approved_existing_profile_in_memory():
    plan = _plan()

    result = review_capability_trust_update_plan(
        plan,
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
        metadata={"pr": 320},
    )

    assert result.allowed_to_apply is True
    assert result.blockers == ()
    assert result.applied_capability_ids == ("hindsight_reflective_memory",)
    by_id = {profile.capability_id: profile for profile in result.profiles}
    promoted = by_id["hindsight_reflective_memory"]
    assert promoted.trust_level == "assisted"
    assert "approval:multica:ZOE-320" in promoted.evidence_refs
    assert promoted.metadata["trust_review"]["source"] == CAPABILITY_TRUST_REVIEW_SOURCE
    original = {profile.capability_id: profile for profile in DEFAULT_CAPABILITY_PROFILES}
    assert original["hindsight_reflective_memory"].trust_level == "experimental"


def test_capability_trust_review_blocks_unknown_profile_until_profile_contract_exists():
    plan = _plan(_candidate(capability_id="self_evolution_loop", current_trust_level="unknown", proposed_trust_level="experimental"))

    result = review_capability_trust_update_plan(
        plan,
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("self_evolution_loop",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is False
    assert result.applied_capability_ids == ()
    assert result.profiles == ()
    assert "unknown_capability_profile:self_evolution_loop" in result.blockers
    assert result.decisions[0].approved is False


def test_capability_trust_review_fails_closed_without_approval_refs():
    result = review_capability_trust_update_plan(
        _plan(),
        reviewer_id="multica:reviewer",
        approval_refs=(),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is False
    assert "missing_approval_refs" in result.blockers
    assert result.profiles == ()
    assert result.decisions[0].approved is False
    assert result.decisions[0].reason.count("missing_approval_refs") == 1


def test_capability_trust_review_fails_closed_when_plan_has_blockers():
    result = review_capability_trust_update_plan(
        _plan(blockers=("outcome_not_retained",)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is False
    assert "outcome_not_retained" in result.blockers
    assert result.decisions[0].approved is False
    assert result.profiles == ()


def test_capability_trust_review_fails_closed_without_reviewer_id():
    result = review_capability_trust_update_plan(
        _plan(),
        reviewer_id="",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is False
    assert result.decisions == ()
    assert result.profiles == ()
    assert "missing_reviewer_id" in result.blockers


def test_capability_trust_review_blocks_stale_current_profile_level():
    stale_profiles = tuple(
        replace(profile, trust_level="assisted")
        if profile.capability_id == "hindsight_reflective_memory"
        else profile
        for profile in DEFAULT_CAPABILITY_PROFILES
    )

    result = review_capability_trust_update_plan(
        _plan(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=stale_profiles,
    )

    assert result.allowed_to_apply is False
    assert "stale_current_trust_level:hindsight_reflective_memory" in result.blockers
    assert result.profiles == ()


def test_capability_trust_review_rejects_unapproved_candidate():
    result = review_capability_trust_update_plan(
        _plan(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=(),
        rejected_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is False
    assert result.blockers == ()
    assert result.profiles == ()
    assert result.decisions[0].approved is False
    assert "review_rejected:hindsight_reflective_memory" in result.decisions[0].reason
    assert "not_approved:hindsight_reflective_memory" in result.decisions[0].reason


def test_capability_trust_review_keeps_candidate_blockers_isolated():
    first = _candidate(
        capability_id="hindsight_reflective_memory",
        current_trust_level="experimental",
        proposed_trust_level="assisted",
    )
    second = _candidate(
        capability_id="openclaw_fallback",
        current_trust_level="assisted",
        proposed_trust_level="trusted",
    )

    result = review_capability_trust_update_plan(
        _plan(first, second),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=(),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    first_reason = result.decisions[0].reason
    second_reason = result.decisions[1].reason
    assert result.allowed_to_apply is False
    assert "not_approved:hindsight_reflective_memory" in first_reason
    assert "not_approved:openclaw_fallback" in second_reason
    assert "hindsight_reflective_memory" not in second_reason
    assert result.profiles == ()


def test_capability_trust_review_applies_approved_profiles_when_other_candidates_are_rejected():
    approved = _candidate(
        capability_id="hindsight_reflective_memory",
        current_trust_level="experimental",
        proposed_trust_level="assisted",
    )
    rejected = _candidate(
        capability_id="openclaw_fallback",
        current_trust_level="assisted",
        proposed_trust_level="trusted",
    )

    result = review_capability_trust_update_plan(
        _plan(approved, rejected),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-322",),
        approved_capability_ids=("hindsight_reflective_memory",),
        rejected_capability_ids=("openclaw_fallback",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is True
    assert result.applied_capability_ids == ("hindsight_reflective_memory",)
    assert result.decisions[0].approved is True
    assert result.decisions[1].approved is False
    assert result.blockers == ()
    by_id = {profile.capability_id: profile for profile in result.profiles}
    assert by_id["hindsight_reflective_memory"].trust_level == "assisted"
    assert by_id["openclaw_fallback"].trust_level == "assisted"
    assert "review_rejected:openclaw_fallback" in result.decisions[1].reason


def test_capability_trust_review_rejected_unknown_profile_does_not_block_approved_profile():
    approved = _candidate(
        capability_id="hindsight_reflective_memory",
        current_trust_level="experimental",
        proposed_trust_level="assisted",
    )
    rejected_unknown = _candidate(
        capability_id="self_evolution_loop",
        current_trust_level="unknown",
        proposed_trust_level="experimental",
    )

    result = review_capability_trust_update_plan(
        _plan(approved, rejected_unknown),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-406",),
        approved_capability_ids=("hindsight_reflective_memory",),
        rejected_capability_ids=("self_evolution_loop",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is True
    assert result.applied_capability_ids == ("hindsight_reflective_memory",)
    assert result.blockers == ()
    assert result.decisions[0].approved is True
    assert result.decisions[1].approved is False
    assert "review_rejected:self_evolution_loop" in result.decisions[1].reason
    assert "unknown_capability_profile:self_evolution_loop" not in result.decisions[1].reason


def test_capability_trust_review_clears_profiles_when_any_candidate_is_invalid():
    approved = _candidate(
        capability_id="hindsight_reflective_memory",
        current_trust_level="experimental",
        proposed_trust_level="assisted",
    )
    invalid = _candidate(
        capability_id="openclaw_fallback",
        current_trust_level="unknown",
        proposed_trust_level="trusted",
    )

    result = review_capability_trust_update_plan(
        _plan(approved, invalid),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-322",),
        approved_capability_ids=("hindsight_reflective_memory", "openclaw_fallback"),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )

    assert result.allowed_to_apply is False
    assert result.applied_capability_ids == ()
    assert result.decisions[0].approved is True
    assert result.decisions[1].approved is False
    assert "stale_current_trust_level:openclaw_fallback" in result.blockers
    assert result.profiles == ()


def test_capability_trust_review_rejects_invalid_promoted_profile():
    profile = CapabilityProfile(
        capability_id="scratch_memory_candidate",
        name="Scratch memory candidate",
        owner_surface="memory",
        task_types=("memory_recall",),
        trust_level="experimental",
        offline_mode="required",
        evidence_refs=("docs:test",),
    )
    candidate = _candidate(
        capability_id="scratch_memory_candidate",
        current_trust_level="experimental",
        proposed_trust_level="trusted",
    )

    result = review_capability_trust_update_plan(
        _plan(candidate),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("scratch_memory_candidate",),
        profiles=(profile,),
    )

    assert result.allowed_to_apply is False
    assert "invalid_promoted_profile:scratch_memory_candidate" in result.blockers
    assert result.decisions[0].approved is False
    assert result.profiles == ()


def test_capability_trust_review_decision_metadata_is_read_only_and_serializable():
    result = review_capability_trust_update_plan(
        _plan(),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-320",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    decision = result.decisions[0]

    with pytest.raises(TypeError):
        decision.metadata["source"] = "mutated"
    payload = result.to_dict()
    assert payload["allowed_to_apply"] is True
    assert payload["decisions"][0]["metadata"]["source"] == CAPABILITY_TRUST_REVIEW_SOURCE


def test_capability_trust_review_decision_requires_approval_refs_when_approved():
    with pytest.raises(ValueError, match="approval_refs"):
        CapabilityTrustReviewDecision(
            decision_id="trust_review_invalid",
            candidate=_candidate(),
            reviewer_id="multica:reviewer",
            approved=True,
            reason="invalid approved decision",
            approval_refs=(),
            evidence_refs=("pytest:test_zoe_capability_trust_review",),
        )


def test_capability_trust_review_result_rejects_blocked_applyable_profiles():
    with pytest.raises(ValueError, match="blocked trust review results"):
        CapabilityTrustReviewResult(
            decisions=(),
            profiles=DEFAULT_CAPABILITY_PROFILES,
            blockers=("manual_blocker",),
        )
