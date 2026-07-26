import json

import pytest

import zoe_evolution_runtime_intake as runtime_intake

from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_proposal import EvolutionSignal, EvolutionSignalType, ProposalRisk, TrustAutonomyClass
from zoe_evolution_proposal_adapter import load_proposal_contract_snapshot
from zoe_evolution_runtime_intake import (
    RuntimeEvolutionProposalIntake,
    build_pi_runtime_install_proposal_intake,
    build_runtime_evolution_proposal_intake,
)
from zoe_observation_trace import ObservationTraceType
from zoe_observation_trace_collector import ObservationTraceCollectorPolicy

pytestmark = pytest.mark.ci_safe


def _signal(**overrides):
    defaults = {
        "signal_id": "sig_runtime_notice_calendar_gap",
        "signal_type": EvolutionSignalType.TOOL_GAP.value,
        "summary": "Runtime noticed Zoe lacks a reliable local calendar handoff.",
        "source": "runtime_notice:tool_gap",
        "evidence_refs": ("trace:runtime-notice-calendar-gap",),
        "user_id": "jason",
        "scope": "project",
        "metadata": {"recurrences": 3},
    }
    defaults.update(overrides)
    return EvolutionSignal(**defaults)


def _candidate(candidate_id="mcp_calendar_local", total_boost=0, **overrides):
    defaults = {
        "candidate_id": candidate_id,
        "name": "Local calendar MCP",
        "source": "mcp",
        "task": "calendar handoff",
        "score": CandidateScore(
            fit=5,
            activity=4 + total_boost,
            license=4,
            offline=5,
            security=4,
            footprint=4,
            tests=4,
            maintainability=4,
            overlap=4,
        ),
        "evidence_refs": ("github:mcp-calendar", "docs/architecture/zoe-tool-capability-inventory.md"),
        "license_risk": "compatible",
        "offline_viability": "required",
        "stars": 1200,
        "last_activity": "2026-06-08",
        "recommendation": "trial_sidecar",
    }
    defaults.update(overrides)
    return CandidateEvaluation(**defaults)


def test_runtime_intake_builds_review_only_legacy_row_with_candidate_search_evidence():
    weaker = _candidate("existing_calendar_route", total_boost=-1, source="existing_zoe", name="Existing calendar route")
    stronger = _candidate("mcp_calendar_local", total_boost=0)

    intake = build_runtime_evolution_proposal_intake(
        proposal_id="prop_runtime_calendar_gap",
        proposal_type="code_improvement",
        title="Review local calendar handoff",
        problem_statement="Zoe repeatedly needed a local calendar handoff and should evaluate existing tools first.",
        signal=_signal(),
        candidates=(weaker, stronger),
        affected_capabilities=("calendar_sync", "mcp_tools"),
        expected_benefit="Reuse an existing local-capable tool before bespoke code.",
        verification_plan=("pytest:services/zoe-data/tests/test_calendar_handoff.py",),
        rollback_plan="Reject the proposal; no runtime change has been made.",
        legacy_target_patterns=("calendar handoff",),
        metadata={"notice_source": "runtime"},
    )

    row = intake.to_legacy_row()
    payload = load_proposal_contract_snapshot(row["target_patterns"])
    evidence = json.loads(row["evidence"])

    assert row["status"] == "pending"
    assert payload is not None
    proposal = payload["proposal"]
    assert payload["legacy_writer"] == "runtime_evolution_intake"
    assert proposal["autonomy_class"] == "suggest"
    assert proposal["status"] == "pending_approval"
    assert proposal["approval_gate"]["allowed_to_execute"] is False
    assert proposal["candidate"]["candidate_id"] == "mcp_calendar_local"
    assert proposal["metadata"]["selected_candidate_id"] == "mcp_calendar_local"
    assert [candidate["candidate_id"] for candidate in proposal["metadata"]["candidate_search"]] == [
        "mcp_calendar_local",
        "existing_calendar_route",
    ]
    assert evidence["source"] == "runtime_evolution_intake"
    assert evidence["signal"]["evidence_refs"] == ["trace:runtime-notice-calendar-gap"]
    assert "github:mcp-calendar" in evidence["candidate_evidence_refs"]
    trace_collection = evidence["observation_trace_collection"]
    assert trace_collection["ok"] is True
    assert trace_collection["accepted_count"] == 1
    assert trace_collection["persisted"] is False
    trace = trace_collection["traces"][0]
    assert trace["trace_type"] == ObservationTraceType.PROPOSAL.value
    assert trace["surface"] == "local_service"
    assert trace["subject_id"] == "prop_runtime_calendar_gap"
    assert trace["related_ids"] == ["mcp_calendar_local", "existing_calendar_route"]
    assert trace["evidence_refs"] == ["trace:runtime-notice-calendar-gap"]
    assert trace["metadata"]["proposal_type"] == "code_improvement"
    assert trace["metadata"]["source"] == "runtime_evolution_intake"
    assert intake.multica_payload["contract_snapshot"] == row["target_patterns"]


def test_runtime_intake_structured_metadata_overrides_caller_collisions():
    intake = build_runtime_evolution_proposal_intake(
        proposal_id="prop_runtime_metadata_collision",
        proposal_type="code_improvement",
        title="Review metadata collision",
        problem_statement="Caller metadata must not overwrite structured reviewer evidence.",
        signal=_signal(),
        candidates=(_candidate(),),
        affected_capabilities=("calendar_sync",),
        expected_benefit="Keep proposal evidence trustworthy.",
        verification_plan=("pytest:metadata-collision",),
        rollback_plan="Reject the proposal; no runtime change has been made.",
        metadata={
            "created_by": "caller",
            "candidate_search": [],
            "selected_candidate_id": "wrong",
            "caller_note": "preserved",
        },
    )
    payload = load_proposal_contract_snapshot(intake.target_patterns)

    assert payload is not None
    metadata = payload["proposal"]["metadata"]
    assert metadata["created_by"] == "runtime_evolution_intake"
    assert metadata["selected_candidate_id"] == "mcp_calendar_local"
    assert metadata["candidate_search"][0]["candidate_id"] == "mcp_calendar_local"
    assert metadata["caller_note"] == "preserved"




def test_mcp_runtime_proposal_includes_observation_trace_collection():
    intake = runtime_intake.build_mcp_runtime_evolution_proposal_intake(
        proposal_id="prop_mcp_trace_collection",
        title="Review MCP proposal",
        description="MCP proposal writers should carry accepted observation trace evidence.",
        evidence="User supplied evidence.",
        proposal_type="intent_pattern",
        user_id="jason",
    )

    evidence = json.loads(intake.evidence)
    trace = evidence["observation_trace_collection"]["traces"][0]

    assert evidence["observation_trace_collection"]["ok"] is True
    assert trace["surface"] == "local_service"
    assert trace["metadata"]["proposal_type"] == "intent_pattern"
    assert trace["subject_id"] == "prop_mcp_trace_collection"


def test_runtime_intake_trace_collection_fails_closed_when_policy_rejects_surface():
    with pytest.raises(ValueError, match="observation_trace_collection rejected"):
        build_runtime_evolution_proposal_intake(
            proposal_id="prop_runtime_rejected_trace_policy",
            proposal_type="code_improvement",
            title="Review rejected trace policy",
            problem_statement="Collector policy rejection should fail proposal intake closed.",
            signal=_signal(),
            candidates=(_candidate(),),
            affected_capabilities=("calendar_sync",),
            expected_benefit="Keep trace evidence trustworthy.",
            verification_plan=("pytest:trace-policy",),
            rollback_plan="Reject the proposal; no runtime change has been made.",
            trace_collector_policy=ObservationTraceCollectorPolicy(
                max_batch_size=1,
                allowed_surfaces=("chat",),
                allowed_trace_types=(ObservationTraceType.PROPOSAL.value,),
            ),
        )


def test_runtime_intake_can_skip_observation_trace_collection_for_legacy_callers():
    intake = build_runtime_evolution_proposal_intake(
        proposal_id="prop_runtime_trace_collection_opt_out",
        proposal_type="code_improvement",
        title="Review trace opt out",
        problem_statement="Legacy callers can opt out while migrating.",
        signal=_signal(),
        candidates=(_candidate(),),
        affected_capabilities=("calendar_sync",),
        expected_benefit="Keep migration compatibility.",
        verification_plan=("pytest:trace-opt-out",),
        rollback_plan="Reject the proposal; no runtime change has been made.",
        collect_observation_trace=False,
    )

    evidence = json.loads(intake.evidence)

    assert "observation_trace_collection" not in evidence


def test_runtime_intake_validates_direct_construction_at_init():
    with pytest.raises(ValueError, match="proposal_id is required"):
        RuntimeEvolutionProposalIntake(
            proposal_id="",
            proposal_type="code_improvement",
            title="Bad direct construction",
            description="Invalid instances should not exist until to_legacy_row.",
            evidence="{}",
            target_patterns="{}",
        )


def test_runtime_intake_preserves_candidate_blockers_without_granting_prepare():
    blocked = _candidate(
        "pi_unknown_runtime",
        source="pi",
        name="Unknown Pi package",
        score=CandidateScore(
            fit=4,
            activity=3,
            license=2,
            offline=1,
            security=2,
            footprint=2,
            tests=2,
            maintainability=3,
            overlap=3,
        ),
        license_risk="review",
        offline_viability="unknown",
        recommendation="needs_review",
    )

    intake = build_runtime_evolution_proposal_intake(
        proposal_id="prop_runtime_pi_unknown",
        proposal_type="code_improvement",
        title="Review unknown Pi package",
        problem_statement="Zoe should evaluate a Pi package but cannot assume offline readiness.",
        signal=_signal(signal_type=EvolutionSignalType.USER_REQUEST.value),
        candidates=(blocked,),
        affected_capabilities=("pi_external_runtime",),
        expected_benefit="Avoid rebuilding a capability if an offline-safe package exists.",
        verification_plan=("manual:license-and-offline-review",),
        rollback_plan="Do not install the package.",
    )
    payload = load_proposal_contract_snapshot(intake.target_patterns)

    assert payload is not None
    gate = payload["proposal"]["approval_gate"]
    assert gate["allowed_to_prepare"] is False
    assert "offline:unknown" in gate["blockers"]
    assert gate["allowed_to_execute"] is False


def test_runtime_intake_adds_required_approval_gates_for_execute_and_high_risk():
    intake = build_runtime_evolution_proposal_intake(
        proposal_id="prop_runtime_execute_high",
        proposal_type="code_improvement",
        title="Prepare sidecar start proposal",
        problem_statement="Starting a sidecar is high risk and needs approval evidence.",
        signal=_signal(),
        candidates=(_candidate(),),
        affected_capabilities=("calendar_sync",),
        expected_benefit="Measure a local sidecar safely.",
        verification_plan=("pytest:sidecar_probe",),
        rollback_plan="Stop the sidecar and revert the PR.",
        autonomy_class=TrustAutonomyClass.EXECUTE.value,
        risk=ProposalRisk.HIGH.value,
        approval_required=("sidecar_start",),
    )
    payload = load_proposal_contract_snapshot(intake.target_patterns)

    assert payload is not None
    proposal = payload["proposal"]
    assert proposal["approval_required"] == [
        "pr_evidence",
        "user_or_admin_for_privileged_execution",
        "sidecar_start",
    ]
    assert proposal["approval_gate"]["allowed_to_execute"] is False


def test_runtime_intake_rejects_missing_signal_evidence():
    with pytest.raises(ValueError, match="evidence_refs are required"):
        build_runtime_evolution_proposal_intake(
            proposal_id="prop_runtime_missing_signal_evidence",
            proposal_type="code_improvement",
            title="Bad runtime proposal",
            problem_statement="Missing evidence should fail closed.",
            signal=_signal(evidence_refs=()),
            candidates=(_candidate(),),
            affected_capabilities=("calendar_sync",),
            expected_benefit="Should not build.",
            verification_plan=("pytest",),
            rollback_plan="No-op.",
        )


def test_runtime_intake_rejects_empty_candidate_search():
    with pytest.raises(ValueError, match="at least one candidate is required"):
        build_runtime_evolution_proposal_intake(
            proposal_id="prop_runtime_missing_candidates",
            proposal_type="code_improvement",
            title="Bad runtime proposal",
            problem_statement="Search/Evaluate must produce at least one candidate.",
            signal=_signal(),
            candidates=(),
            affected_capabilities=("calendar_sync",),
            expected_benefit="Should not build.",
            verification_plan=("pytest",),
            rollback_plan="No-op.",
        )


def _pi_probe(**overrides):
    base_config = {
        "enabled": False,
        "allow_execution": False,
        "offline_only": True,
        "local_model_required": True,
        "local_model_configured": False,
        "command": "pi",
        "cwd": "/home/zoe/assistant",
        "agent_dir": None,
        "timeout_seconds": 2.0,
    }
    config_override = overrides.pop("config", None)
    defaults = {
        "ok": False,
        "acceptable": True,
        "status": "disabled",
        "reason": "ZOE_PI_ENABLED is false",
        "config": {**base_config, **(config_override or {})},
        "tools": {"node": None, "npm": None, "pi": None},
        "agent_files": [],
    }
    defaults.update(overrides)
    return defaults


def test_pi_runtime_install_proposal_is_inert_and_blocked_until_prerequisites_pass():
    intake = build_pi_runtime_install_proposal_intake(
        proposal_id="prop_pi_runtime_install_test",
        user_id="jason",
        probe_result=_pi_probe(),
    )

    row = intake.to_legacy_row()
    payload = load_proposal_contract_snapshot(row["target_patterns"])
    evidence = json.loads(row["evidence"])

    assert row["status"] == "pending"
    assert payload is not None
    proposal = payload["proposal"]
    assert proposal["candidate"]["candidate_id"] == "pi_runtime_reuse"
    assert proposal["autonomy_class"] == "prepare"
    assert proposal["risk"] == "privileged"
    assert proposal["approval_gate"]["allowed_to_prepare"] is False
    assert proposal["approval_gate"]["allowed_to_execute"] is False
    assert proposal["approval_gate"]["blockers"] == ["offline:partial", "score_below_threshold"]
    assert proposal["approval_required"] == [
        "user_or_admin_for_privileged_execution",
        "security_review",
        "install_or_runtime_change",
        "license_review",
        "pr_evidence",
    ]
    assert proposal["metadata"]["pi_runtime_probe"]["tools"] == {"node": None, "npm": None, "pi": None}
    assert proposal["metadata"]["pi_candidate_gate"]["allowed"] is False
    assert evidence["signal"]["metadata"]["probe_status"] == "disabled"
    assert evidence["observation_trace_collection"]["ok"] is True
    assert evidence["observation_trace_collection"]["traces"][0]["metadata"]["proposal_type"] == "code_improvement"
    assert intake.multica_payload["contract_snapshot"] == row["target_patterns"]


def test_pi_runtime_install_proposal_does_not_mark_partial_offline_ready_without_local_model():
    base = _pi_probe()
    probe = _pi_probe(ok=True, status="available", config={**base["config"], "local_model_configured": False})

    intake = build_pi_runtime_install_proposal_intake(probe_result=probe)
    payload = load_proposal_contract_snapshot(intake.target_patterns)

    assert payload is not None
    candidate = payload["proposal"]["candidate"]
    assert candidate["metadata"]["offline_ready"] is False
    assert "offline:partial" in payload["proposal"]["approval_gate"]["blockers"]


def test_pi_runtime_install_proposal_reports_missing_pi_candidate(monkeypatch):
    monkeypatch.setattr(runtime_intake, "EXAMPLE_CANDIDATES", ())

    with pytest.raises(ValueError, match="pi_runtime_reuse"):
        runtime_intake.build_pi_runtime_install_proposal_intake(probe_result=_pi_probe())

