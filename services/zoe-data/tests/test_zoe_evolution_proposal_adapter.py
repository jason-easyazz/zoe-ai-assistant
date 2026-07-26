import pytest
import json

from zoe_evolution_proposal_adapter import (
    build_legacy_evolution_proposal_contract,
    build_mcp_evolution_proposal_contract,
    dump_legacy_evolution_proposal_contract,
    dump_mcp_evolution_proposal_contract,
    load_proposal_contract_snapshot,
    normalize_mcp_evolution_proposal_type,
)

pytestmark = pytest.mark.ci_safe


def test_mcp_contract_snapshot_is_valid_and_review_only():
    payload = build_mcp_evolution_proposal_contract(
        proposal_id="prop123",
        title="Improve weather fallback",
        description="Weather fallback failed twice and needs a measured fix.",
        evidence="tool_run:weather:failed twice",
        proposal_type="agent_health",
        user_id="jason",
    )

    proposal = payload["proposal"]

    assert payload["schema"] == "zoe_evolution_proposal"
    assert proposal["status"] == "pending_approval"
    assert proposal["autonomy_class"] == "suggest"
    assert proposal["approval_gate"]["allowed_to_execute"] is False
    assert proposal["signals"][0]["signal_type"] == "outcome_eval_failure"
    assert proposal["signals"][0]["scope"] == "personal"
    assert "agent_runtime" in proposal["affected_capabilities"]
    assert "mcp:create_evolution_proposal:prop123" in proposal["evidence_refs"]
    assert "evidence:mcp:create_evolution_proposal:prop123" in proposal["evidence_refs"]


def test_mcp_contract_snapshot_has_source_evidence_when_freeform_evidence_is_empty():
    payload = build_mcp_evolution_proposal_contract(
        proposal_id="prop_no_evidence",
        title="Review an intent gap",
        description="Zoe missed a repeated natural language intent.",
        evidence="",
        proposal_type="intent_pattern",
    )

    proposal = payload["proposal"]

    assert proposal["signals"][0]["signal_type"] == "repeated_failure"
    assert proposal["evidence_refs"] == ["mcp:create_evolution_proposal:prop_no_evidence"]
    assert proposal["signals"][0]["scope"] == "system"


def test_unknown_legacy_type_falls_back_to_intent_pattern():
    payload = build_mcp_evolution_proposal_contract(
        proposal_id="prop_unknown",
        title="Unknown proposal type",
        description="Unknown legacy types should not bypass the proposal contract.",
        evidence="manual observation",
        proposal_type="surprise",
    )

    proposal = payload["proposal"]

    assert proposal["metadata"]["legacy_proposal_type"] == "intent_pattern"
    assert proposal["signals"][0]["signal_type"] == "repeated_failure"


def test_normalize_mcp_evolution_proposal_type_matches_contract_fallback():
    assert normalize_mcp_evolution_proposal_type("code_improvement") == "code_improvement"
    assert normalize_mcp_evolution_proposal_type("surprise") == "intent_pattern"


def test_dump_and_load_contract_snapshot_round_trip():
    raw = dump_mcp_evolution_proposal_contract(
        proposal_id="prop_round_trip",
        title="Round trip",
        description="Contract payload should survive target_patterns JSON storage.",
        evidence="trace:round-trip",
        proposal_type="code_improvement",
        multica_issue_id="issue-123",
    )

    payload = load_proposal_contract_snapshot(raw)

    assert payload is not None
    assert json.loads(raw)["proposal"]["multica_issue_id"] == "issue-123"
    assert payload["proposal"]["signals"][0]["signal_type"] == "tool_gap"


def test_legacy_contract_snapshot_preserves_target_patterns_and_writer():
    payload = build_legacy_evolution_proposal_contract(
        proposal_id="prop_notice",
        title="Intent gap",
        description="Zoe missed related intent messages.",
        evidence="trace:intent-gap",
        proposal_type="intent_pattern",
        legacy_writer="evolution_notice:intent_miss_cluster",
        target_patterns=("one", "two"),
    )

    proposal = payload["proposal"]

    assert payload["legacy_writer"] == "evolution_notice:intent_miss_cluster"
    assert proposal["metadata"]["legacy_target_patterns"] == ["one", "two"]
    assert proposal["signals"][0]["metadata"]["legacy_target_patterns"] == ["one", "two"]
    assert proposal["candidate"]["metadata"]["legacy_target_patterns"] == ["one", "two"]


def test_legacy_contract_snapshot_supports_user_issue_report():
    raw = dump_legacy_evolution_proposal_contract(
        proposal_id="prop_user_issue",
        title="User report",
        description="User explicitly reported a problem.",
        evidence="trace:user-report",
        proposal_type="user_issue_report",
        legacy_writer="evolution_notice:user_issue_report",
        user_id="jason",
        target_patterns=("reported problem",),
    )

    payload = load_proposal_contract_snapshot(raw)

    assert payload is not None
    proposal = payload["proposal"]
    assert proposal["metadata"]["legacy_proposal_type"] == "user_issue_report"
    assert proposal["signals"][0]["signal_type"] == "user_request"
    assert proposal["signals"][0]["scope"] == "personal"


def test_loader_rejects_legacy_non_contract_payloads():
    assert load_proposal_contract_snapshot(None) is None
    assert load_proposal_contract_snapshot("not json") is None
    assert load_proposal_contract_snapshot('{"legacy": true}') is None
    assert load_proposal_contract_snapshot('["pattern1", "pattern2"]') is None
