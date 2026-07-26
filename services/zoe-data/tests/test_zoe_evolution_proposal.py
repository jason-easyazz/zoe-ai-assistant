import pytest

from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)

pytestmark = pytest.mark.ci_safe


def _candidate(**overrides):
    defaults = {
        "candidate_id": "mcp_calendar_candidate",
        "name": "Calendar MCP candidate",
        "source": "mcp",
        "task": "calendar sync",
        "score": CandidateScore(
            fit=5,
            activity=4,
            license=4,
            offline=5,
            security=4,
            footprint=4,
            tests=4,
            maintainability=4,
            overlap=4,
        ),
        "evidence_refs": ("github:mcp/calendar", "docs/architecture/zoe-tool-capability-inventory.md"),
        "license_risk": "compatible",
        "offline_viability": "required",
        "stars": 1200,
        "last_activity": "2026-06-01",
        "recommendation": "trial_sidecar",
    }
    defaults.update(overrides)
    return CandidateEvaluation(**defaults)


def _signal(**overrides):
    defaults = {
        "signal_id": "sig_calendar_gap",
        "signal_type": EvolutionSignalType.USER_REQUEST.value,
        "summary": "User asked Zoe to improve calendar sync reliability.",
        "source": "chat",
        "evidence_refs": ("chat:calendar-sync-request",),
        "user_id": "jason",
        "scope": "project",
    }
    defaults.update(overrides)
    return EvolutionSignal(**defaults)


def test_build_proposal_collects_signal_and_candidate_evidence():
    proposal = build_evolution_proposal(
        proposal_id="prop_calendar_mcp",
        title="Trial calendar MCP sidecar",
        problem_statement="Zoe needs a measured existing calendar capability before building a bespoke sync path.",
        signals=(_signal(),),
        candidate=_candidate(),
        affected_capabilities=("chat_router", "calendar_sync"),
        autonomy_class=TrustAutonomyClass.PREPARE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Reduce bespoke calendar sync code and improve reliability.",
        verification_plan=("pytest:tests/integration/test_chat_interface.py", "live:/api/system/status"),
        rollback_plan="Disable the sidecar profile and keep existing calendar routes.",
    )

    payload = proposal.to_dict()

    assert payload["proposal_id"] == "prop_calendar_mcp"
    assert payload["candidate"]["total_score"] == _candidate().score.total()
    assert payload["evidence_refs"] == [
        "chat:calendar-sync-request",
        "github:mcp/calendar",
        "docs/architecture/zoe-tool-capability-inventory.md",
    ]
    assert payload["approval_gate"]["allowed_to_prepare"] is True
    assert payload["approval_gate"]["allowed_to_execute"] is False


def test_signal_requires_evidence():
    signal = _signal(evidence_refs=())

    with pytest.raises(ValueError, match="evidence_refs are required"):
        signal.validate()


def test_execute_proposal_requires_approval():
    with pytest.raises(ValueError, match="execute/promote proposals require approval_required"):
        build_evolution_proposal(
            proposal_id="prop_exec",
            title="Execute install",
            problem_statement="Install a candidate.",
            signals=(_signal(),),
            candidate=_candidate(),
            affected_capabilities=("calendar_sync",),
            autonomy_class=TrustAutonomyClass.EXECUTE.value,
            risk=ProposalRisk.MEDIUM.value,
            expected_benefit="Test install.",
            verification_plan=("pytest:tests/integration/test_chat_interface.py",),
            rollback_plan="Remove the install.",
        )


def test_execute_gate_requires_pr_evidence_even_with_approval():
    with pytest.raises(ValueError, match="execute/promote proposals require pr_evidence approval"):
        build_evolution_proposal(
            proposal_id="prop_exec_missing_pr",
            title="Execute install",
            problem_statement="Install a candidate.",
            signals=(_signal(),),
            candidate=_candidate(),
            affected_capabilities=("calendar_sync",),
            autonomy_class=TrustAutonomyClass.EXECUTE.value,
            risk=ProposalRisk.MEDIUM.value,
            expected_benefit="Test install.",
            verification_plan=("pytest:tests/integration/test_chat_interface.py",),
            rollback_plan="Remove the install.",
            approval_required=("install_or_runtime_change",),
        )


def test_promote_proposal_can_prepare_with_pr_evidence_but_never_execute():
    proposal = build_evolution_proposal(
        proposal_id="prop_promote_calendar",
        title="Promote calendar candidate",
        problem_statement="Promote the measured candidate after verification.",
        signals=(_signal(signal_type=EvolutionSignalType.OUTCOME_EVAL_FAILURE.value),),
        candidate=_candidate(),
        affected_capabilities=("calendar_sync",),
        autonomy_class=TrustAutonomyClass.PROMOTE.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Make the measured capability trusted after review.",
        verification_plan=("pytest:tests/integration/test_chat_interface.py",),
        rollback_plan="Demote the capability profile to assisted.",
        approval_required=("pr_evidence",),
    )
    gate = proposal.approval_gate()

    assert gate["allowed_to_prepare"] is True
    assert gate["allowed_to_execute"] is False
    assert gate["blockers"] == []


def test_high_risk_proposal_requires_approval():
    with pytest.raises(ValueError, match="high-risk proposals require approval_required"):
        build_evolution_proposal(
            proposal_id="prop_high_no_approval",
            title="High risk proposal",
            problem_statement="Start a sidecar that changes runtime behavior.",
            signals=(_signal(signal_type=EvolutionSignalType.TOOL_GAP.value),),
            candidate=_candidate(),
            affected_capabilities=("calendar_sync",),
            autonomy_class=TrustAutonomyClass.SUGGEST.value,
            risk=ProposalRisk.HIGH.value,
            expected_benefit="Improve reliability.",
            verification_plan=("pytest:tests/integration/test_chat_interface.py",),
            rollback_plan="Disable the sidecar.",
        )


def test_high_risk_requires_user_or_admin_approval():
    with pytest.raises(ValueError, match="high-risk proposals require user_or_admin_for_privileged_execution approval"):
        build_evolution_proposal(
            proposal_id="prop_high_missing_user",
            title="High risk proposal",
            problem_statement="Start a sidecar that changes runtime behavior.",
            signals=(_signal(signal_type=EvolutionSignalType.TOOL_GAP.value),),
            candidate=_candidate(),
            affected_capabilities=("calendar_sync",),
            autonomy_class=TrustAutonomyClass.SUGGEST.value,
            risk=ProposalRisk.HIGH.value,
            expected_benefit="Improve reliability.",
            verification_plan=("pytest:tests/integration/test_chat_interface.py",),
            rollback_plan="Disable the sidecar.",
            approval_required=("sidecar_start",),
        )


def test_high_risk_proposal_can_prepare_with_user_or_admin_approval():
    proposal = build_evolution_proposal(
        proposal_id="prop_high_with_user",
        title="High risk proposal",
        problem_statement="Start a sidecar that changes runtime behavior.",
        signals=(_signal(signal_type=EvolutionSignalType.TOOL_GAP.value),),
        candidate=_candidate(),
        affected_capabilities=("calendar_sync",),
        autonomy_class=TrustAutonomyClass.SUGGEST.value,
        risk=ProposalRisk.HIGH.value,
        expected_benefit="Improve reliability.",
        verification_plan=("pytest:tests/integration/test_chat_interface.py",),
        rollback_plan="Disable the sidecar.",
        approval_required=("sidecar_start", "user_or_admin_for_privileged_execution"),
    )
    gate = proposal.approval_gate()

    assert gate["allowed_to_prepare"] is True
    assert gate["blockers"] == []


def test_approved_status_requires_approval_gate():
    with pytest.raises(ValueError, match="approved/verified proposals require approval evidence gates"):
        build_evolution_proposal(
            proposal_id="prop_approved_without_gate",
            title="Approved without gate",
            problem_statement="This status should not be accepted without approvals.",
            signals=(_signal(),),
            candidate=_candidate(),
            affected_capabilities=("calendar_sync",),
            autonomy_class=TrustAutonomyClass.SUGGEST.value,
            risk=ProposalRisk.LOW.value,
            expected_benefit="Improve reliability.",
            verification_plan=("pytest:tests/integration/test_chat_interface.py",),
            rollback_plan="No-op.",
            status=ProposalStatus.APPROVED.value,
        )


def test_unknown_approval_class_is_rejected():
    with pytest.raises(ValueError, match="unknown approval class"):
        build_evolution_proposal(
            proposal_id="prop_bad_approval_class",
            title="Bad approval class",
            problem_statement="Typoed approval classes should fail at construction.",
            signals=(_signal(),),
            candidate=_candidate(),
            affected_capabilities=("calendar_sync",),
            autonomy_class=TrustAutonomyClass.SUGGEST.value,
            risk=ProposalRisk.LOW.value,
            expected_benefit="Catch approval typos early.",
            verification_plan=("pytest:tests/integration/test_chat_interface.py",),
            rollback_plan="No-op.",
            approval_required=("pr-evidence",),
        )


def test_candidate_gate_blocks_unknown_offline_candidate():
    proposal = build_evolution_proposal(
        proposal_id="prop_pi_unknown",
        title="Review Pi package",
        problem_statement="Pi package needs review before adoption.",
        signals=(_signal(signal_type=EvolutionSignalType.TOOL_GAP.value),),
        candidate=_candidate(
            candidate_id="pi_unknown",
            name="Unknown Pi package",
            source="pi",
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
        ),
        affected_capabilities=("pi_external_runtime",),
        autonomy_class=TrustAutonomyClass.SUGGEST.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Reuse existing Pi capability if it is safe and offline-capable.",
        verification_plan=("candidate scoring review",),
        rollback_plan="Do not install the package.",
    )

    gate = proposal.approval_gate()

    assert gate["allowed_to_prepare"] is False
    assert "offline:unknown" in gate["blockers"]
    assert "score_below_threshold" in gate["blockers"]
