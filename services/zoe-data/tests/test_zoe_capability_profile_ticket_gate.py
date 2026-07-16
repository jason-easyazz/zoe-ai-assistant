from pathlib import Path

import pytest

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_multica_handoff import CapabilityProfileMulticaHandoff, build_capability_profile_multica_handoff
from zoe_capability_profile_patch_writer import build_capability_profile_patch_plan
from zoe_capability_profile_promotion import build_capability_profile_promotion_plan
from zoe_capability_profile_ticket_gate import (
    CAPABILITY_PROFILE_TICKET_GATE_SOURCE,
    CapabilityProfileTicketWriterGateDecision,
    evaluate_capability_profile_ticket_writer_gate,
)
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan

pytestmark = pytest.mark.ci_safe


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


def _candidate(**overrides):
    values = {
        "capability_id": "hindsight_reflective_memory",
        "proposal_id": "proposal_hindsight_ticket_gate",
        "proposal_candidate_id": "hindsight_reflective_memory",
        "current_trust_level": "experimental",
        "proposed_trust_level": "assisted",
        "reason": "Verified retained outcome supports a reviewable promotion.",
        "evidence_refs": ("pytest:test_zoe_capability_profile_ticket_gate", "approval:multica:ZOE-409"),
        "source_event_id": "event_hindsight_ticket_gate",
        "source_admission_id": "admit_hindsight_ticket_gate",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _promotion_plan(**overrides):
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-409",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    values = {
        "pr_refs": ("pr:409",),
        "rollback_refs": ("rollback:revert-pr-409",),
        "verification_refs": ("pytest:test_zoe_capability_profile_ticket_gate",),
    }
    values.update(overrides)
    return build_capability_profile_promotion_plan(review, **values)


def _handoff():
    promotion = _promotion_plan()
    patch = build_capability_profile_patch_plan(promotion, source_text=_source_text())
    return build_capability_profile_multica_handoff(
        promotion,
        patch,
        title="Promote Hindsight reflective memory profile",
        parent_issue_id="ZOE-409",
    )


def test_profile_ticket_gate_allows_operator_approved_createable_handoff_payload():
    handoff = _handoff()

    decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
        evidence_refs=("pytest:test_zoe_capability_profile_ticket_gate",),
        metadata={"source_pr": 409},
    )

    assert decision.allowed_to_create_ticket is True
    assert decision.blockers == ()
    assert decision.metadata["source"] == CAPABILITY_PROFILE_TICKET_GATE_SOURCE
    assert decision.metadata["promoted_capability_ids"] == ["hindsight_reflective_memory"]
    assert decision.ticket_payload["title"] == handoff.title
    assert decision.ticket_payload["description"] == handoff.description
    assert decision.ticket_payload["promotion_manifest"] == handoff.promotion_manifest
    assert decision.ticket_payload["patch_text"] == handoff.patch_text
    assert "approval:operator:ZOE-409" in decision.evidence_refs


def test_profile_ticket_gate_blocks_missing_operator_approval_without_payload():
    decision = evaluate_capability_profile_ticket_writer_gate(
        _handoff(),
        operator_id="operator:jason",
        approval_refs=(),
    )

    assert decision.allowed_to_create_ticket is False
    assert "missing_ticket_writer_approval_refs" in decision.blockers
    assert decision.ticket_payload == {}


def test_profile_ticket_gate_blocks_missing_operator_id_without_payload():
    decision = evaluate_capability_profile_ticket_writer_gate(
        _handoff(),
        operator_id="",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "missing_operator_id" in decision.blockers
    assert decision.ticket_payload == {}


def test_profile_ticket_gate_blocks_blocked_handoff_without_payload():
    promotion = _promotion_plan(pr_refs=())
    patch = build_capability_profile_patch_plan(promotion, source_text=_source_text())
    handoff = build_capability_profile_multica_handoff(promotion, patch)

    decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "handoff_not_createable" in decision.blockers
    assert "missing_pr_refs" in decision.blockers
    assert decision.ticket_payload == {}


def test_profile_ticket_gate_blocks_tampered_patch_hash():
    handoff = _handoff()
    tampered = CapabilityProfileMulticaHandoff(
        title=handoff.title,
        description=handoff.description,
        ticket_metadata=handoff.ticket_metadata,
        promotion_manifest=handoff.promotion_manifest,
        patch_text=handoff.patch_text + "# tampered\n",
        metadata=handoff.metadata,
    )

    decision = evaluate_capability_profile_ticket_writer_gate(
        tampered,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "patch_hash_mismatch" in decision.blockers
    assert decision.ticket_payload == {}


def test_profile_ticket_gate_blocks_missing_profile_promotion_metadata():
    handoff = _handoff()
    missing_metadata = CapabilityProfileMulticaHandoff(
        title=handoff.title,
        description=handoff.description,
        ticket_metadata={},
        promotion_manifest=handoff.promotion_manifest,
        patch_text=handoff.patch_text,
        metadata=handoff.metadata,
    )

    decision = evaluate_capability_profile_ticket_writer_gate(
        missing_metadata,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "missing_profile_promotion_metadata" in decision.blockers
    assert decision.ticket_payload == {}


def test_profile_ticket_gate_decision_invariants():
    with pytest.raises(ValueError, match="allowed ticket gate decisions cannot carry blockers"):
        CapabilityProfileTicketWriterGateDecision(
            allowed_to_create_ticket=True,
            blockers=("manual_blocker",),
            operator_id="operator:jason",
            approval_refs=("approval:operator:ZOE-409",),
            evidence_refs=("approval:operator:ZOE-409",),
            ticket_payload={"title": "x"},
        )

    with pytest.raises(ValueError, match="blocked ticket gate decisions cannot carry ticket_payload"):
        CapabilityProfileTicketWriterGateDecision(
            allowed_to_create_ticket=False,
            blockers=("manual_blocker",),
            operator_id="operator:jason",
            approval_refs=(),
            evidence_refs=(),
            ticket_payload={"title": "x"},
        )



def _handoff_with_metadata(handoff, *, top_level=None, profile_promotion=None):
    ticket_metadata = dict(handoff.ticket_metadata)
    ticket_metadata.update(top_level or {})
    promotion_metadata = dict(ticket_metadata.get("profile_promotion", {}))
    promotion_metadata.update(profile_promotion or {})
    ticket_metadata["profile_promotion"] = promotion_metadata
    return CapabilityProfileMulticaHandoff(
        title=handoff.title,
        description=handoff.description,
        ticket_metadata=ticket_metadata,
        promotion_manifest=handoff.promotion_manifest,
        patch_text=handoff.patch_text,
        metadata=handoff.metadata,
    )


def test_profile_ticket_gate_blocks_tampered_promotion_manifest_hash():
    handoff = _handoff()
    tampered = CapabilityProfileMulticaHandoff(
        title=handoff.title,
        description=handoff.description,
        ticket_metadata=handoff.ticket_metadata,
        promotion_manifest=handoff.promotion_manifest + "{}\n",
        patch_text=handoff.patch_text,
        metadata=handoff.metadata,
    )

    decision = evaluate_capability_profile_ticket_writer_gate(
        tampered,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "promotion_manifest_hash_mismatch" in decision.blockers
    assert decision.ticket_payload == {}


def test_profile_ticket_gate_blocks_invalid_zoe_kind():
    handoff = _handoff_with_metadata(_handoff(), top_level={"zoe_kind": "other_kind"})

    decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "invalid_zoe_kind" in decision.blockers


def test_profile_ticket_gate_blocks_invalid_profile_promotion_source():
    handoff = _handoff_with_metadata(_handoff(), profile_promotion={"source": "manual"})

    decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "invalid_profile_promotion_source" in decision.blockers


def test_profile_ticket_gate_blocks_missing_promoted_capability_ids():
    handoff = _handoff_with_metadata(_handoff(), profile_promotion={"promoted_capability_ids": []})

    decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "missing_promoted_capability_ids" in decision.blockers


def test_profile_ticket_gate_blocks_missing_source_sha256():
    handoff = _handoff_with_metadata(_handoff(), profile_promotion={"source_sha256": ""})

    decision = evaluate_capability_profile_ticket_writer_gate(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-409",),
    )

    assert decision.allowed_to_create_ticket is False
    assert "missing_source_sha256" in decision.blockers


def test_profile_ticket_gate_blocked_decisions_require_blockers():
    with pytest.raises(ValueError, match="blocked ticket gate decisions require blockers"):
        CapabilityProfileTicketWriterGateDecision(
            allowed_to_create_ticket=False,
            blockers=(),
            operator_id="operator:jason",
            approval_refs=(),
            evidence_refs=(),
            ticket_payload={},
        )
