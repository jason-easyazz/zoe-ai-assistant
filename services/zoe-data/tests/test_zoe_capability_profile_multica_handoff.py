import json
from pathlib import Path

import pytest

from multica_ticket_contract import parse_ticket_block
from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_multica_handoff import (
    CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE,
    CapabilityProfileMulticaHandoff,
    build_capability_profile_multica_handoff,
    render_capability_profile_multica_handoff_payload,
)
from zoe_capability_profile_patch_writer import build_capability_profile_patch_plan
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
        "evidence_refs": ("pytest:test_zoe_capability_profile_multica_handoff", "approval:multica:ZOE-376"),
        "source_event_id": "event_hindsight_trust",
        "source_admission_id": "admit_hindsight_trust",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _promotion_plan(**overrides):
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:multica:ZOE-376",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    values = {
        "pr_refs": ("pr:376",),
        "rollback_refs": ("rollback:revert-pr-376",),
        "verification_refs": ("pytest:test_zoe_capability_profile_multica_handoff",),
    }
    values.update(overrides)
    return build_capability_profile_promotion_plan(review, **values)


def _patch_plan(promotion=None):
    return build_capability_profile_patch_plan(
        promotion or _promotion_plan(),
        source_text=_source_text(),
        metadata={"pr": 376},
    )


def test_profile_multica_handoff_builds_ticket_packet_with_patch_and_manifest():
    promotion = _promotion_plan()
    patch = _patch_plan(promotion)

    handoff = build_capability_profile_multica_handoff(
        promotion,
        patch,
        title="Promote Hindsight reflective memory profile",
        parent_issue_id="ZOE-376",
        metadata={"operator": "test"},
    )

    assert handoff.allowed_to_create_ticket is True
    assert handoff.blockers == ()
    assert handoff.metadata["source"] == CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE
    assert "hindsight_reflective_memory" in handoff.promotion_manifest
    assert '-        trust_level="experimental",' in handoff.patch_text
    assert '+        trust_level="assisted",' in handoff.patch_text
    parsed = parse_ticket_block(handoff.description)
    assert parsed == handoff.ticket_metadata
    assert parsed["zoe_kind"] == "capability_profile_promotion"
    assert parsed["evidence_profile"] == "code"
    assert parsed["parent_issue_id"] == "ZOE-376"
    assert parsed["profile_promotion"]["source"] == CAPABILITY_PROFILE_MULTICA_HANDOFF_SOURCE
    assert parsed["profile_promotion"]["promoted_capability_ids"] == ["hindsight_reflective_memory"]
    assert parsed["profile_promotion"]["target_path"] == "services/zoe-data/zoe_capability_profile.py"
    assert parsed["profile_promotion"]["source_sha256"] == patch.source_sha256
    assert "promotion_manifest_sha256" in parsed["profile_promotion"]
    assert "patch_sha256" in parsed["profile_promotion"]


def test_profile_multica_handoff_payload_is_stable_json():
    handoff = build_capability_profile_multica_handoff(_promotion_plan(), _patch_plan())

    payload = json.loads(render_capability_profile_multica_handoff_payload(handoff))

    assert payload["title"] == "Apply governed Zoe capability profile promotion"
    assert payload["description"] == handoff.description
    assert payload["promotion_manifest"] == handoff.promotion_manifest
    assert payload["patch_text"] == handoff.patch_text


def test_profile_multica_handoff_blocks_unapplyable_plans():
    promotion = _promotion_plan(pr_refs=(), rollback_refs=(), verification_refs=())
    patch = _patch_plan(promotion)

    handoff = build_capability_profile_multica_handoff(promotion, patch)

    assert handoff.allowed_to_create_ticket is False
    assert "promotion_plan_not_applyable" in handoff.blockers
    assert "missing_pr_refs" in handoff.blockers
    assert "patch_plan_not_applyable" in handoff.blockers
    assert handoff.description == ""
    with pytest.raises(ValueError, match="blocked"):
        render_capability_profile_multica_handoff_payload(handoff)


def test_profile_multica_handoff_blocks_mismatched_patch_capabilities():
    promotion = _promotion_plan()
    patch = _patch_plan()
    mismatched_patch = type(patch)(
        target_path=patch.target_path,
        source_sha256=patch.source_sha256,
        patch=patch.patch,
        records=(),
    )

    handoff = build_capability_profile_multica_handoff(promotion, mismatched_patch)

    assert handoff.allowed_to_create_ticket is False
    assert "patch_plan_not_applyable" in handoff.blockers
    assert "capability_id_mismatch" in handoff.blockers


def test_profile_multica_handoff_blocks_mismatched_target_path():
    promotion = _promotion_plan()
    patch = _patch_plan(promotion)
    mismatched_patch = type(patch)(
        target_path="services/zoe-data/other_capability_profile.py",
        source_sha256=patch.source_sha256,
        patch=patch.patch,
        records=patch.records,
        metadata=patch.metadata,
    )

    handoff = build_capability_profile_multica_handoff(promotion, mismatched_patch)

    assert handoff.allowed_to_create_ticket is False
    assert "target_path_mismatch" in handoff.blockers


def test_profile_multica_handoff_blocks_missing_title():
    handoff = build_capability_profile_multica_handoff(_promotion_plan(), _patch_plan(), title="   ")

    assert handoff.allowed_to_create_ticket is False
    assert "missing_title" in handoff.blockers
    assert handoff.description == ""
    assert handoff.ticket_metadata == {}


def test_profile_multica_handoff_blocks_missing_source_sha256():
    promotion = _promotion_plan()
    patch = _patch_plan(promotion)
    missing_sha_patch = type(patch)(
        target_path=patch.target_path,
        source_sha256="",
        patch=patch.patch,
        records=patch.records,
        metadata=patch.metadata,
    )

    handoff = build_capability_profile_multica_handoff(promotion, missing_sha_patch)

    assert handoff.allowed_to_create_ticket is False
    assert "missing_source_sha256" in handoff.blockers


def test_blocked_profile_multica_handoff_cannot_carry_payloads():
    with pytest.raises(ValueError, match="blocked capability profile handoffs"):
        CapabilityProfileMulticaHandoff(
            title="blocked",
            description="payload",
            ticket_metadata={},
            promotion_manifest="manifest",
            patch_text="patch",
            blockers=("manual_blocker",),
        )

    with pytest.raises(ValueError, match="blocked capability profile handoffs"):
        CapabilityProfileMulticaHandoff(
            title="blocked",
            description="",
            ticket_metadata={"stale": "metadata"},
            promotion_manifest="",
            patch_text="",
            blockers=("manual_blocker",),
        )
