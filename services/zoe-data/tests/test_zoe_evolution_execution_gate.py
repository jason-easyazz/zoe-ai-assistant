import pytest
from zoe_evolution_execution_gate import evaluate_execution_gate
from zoe_evolution_proposal import ProposalRisk, TrustAutonomyClass

pytestmark = pytest.mark.ci_safe


def _proposal(**overrides):
    payload = {
        "proposal_id": "prop_install_pi",
        "autonomy_class": TrustAutonomyClass.EXECUTE.value,
        "risk": ProposalRisk.PRIVILEGED.value,
        "approval_required": [
            "install_or_runtime_change",
            "pr_evidence",
            "security_review",
            "user_or_admin_for_privileged_execution",
        ],
    }
    payload.update(overrides)
    return payload


def test_execution_gate_fails_closed_without_approval_evidence():
    decision = evaluate_execution_gate(_proposal())

    assert decision.allowed_to_execute is False
    assert decision.missing_approval_classes == (
        "install_or_runtime_change",
        "pr_evidence",
        "security_review",
        "user_or_admin_for_privileged_execution",
    )
    assert "missing approval evidence" in decision.blockers[-1]


def test_execution_gate_allows_when_required_evidence_refs_exist():
    decision = evaluate_execution_gate(
        _proposal(),
        approval_refs=(
            "approval:install_or_runtime_change:jason:2026-06-10",
            "approval:security_review:jason:2026-06-10",
            "approval:admin:jason:2026-06-10",
            "pr:https://github.com/jason-easyazz/zoe-ai-assistant/pull/301",
        ),
    )

    assert decision.allowed_to_execute is True
    assert decision.missing_approval_classes == ()
    assert decision.unknown_approval_classes == ()
    assert decision.blockers == ()


def test_non_execute_proposal_cannot_execute_even_with_evidence():
    decision = evaluate_execution_gate(
        _proposal(autonomy_class=TrustAutonomyClass.SUGGEST.value),
        approval_refs=(
            "approval:install_or_runtime_change:jason:2026-06-10",
            "approval:security_review:jason:2026-06-10",
            "approval:admin:jason:2026-06-10",
            "pr:https://github.com/jason-easyazz/zoe-ai-assistant/pull/301",
        ),
    )

    assert decision.allowed_to_execute is False
    assert "proposal autonomy class is not executable" in decision.blockers


def test_unknown_approval_class_blocks_execution():
    decision = evaluate_execution_gate(
        _proposal(approval_required=["telepathy"]),
        approval_refs=("approval:telepathy:jason",),
    )

    assert decision.allowed_to_execute is False
    assert decision.missing_approval_classes == ()
    assert decision.unknown_approval_classes == ("telepathy",)
    assert "unknown approval class: telepathy" in decision.blockers
    assert not any(blocker == "missing approval evidence: telepathy" for blocker in decision.blockers)


def test_promote_memory_admission_requires_memory_approval_and_pr():
    decision = evaluate_execution_gate(
        _proposal(
            proposal_id="prop_promote_memory",
            autonomy_class=TrustAutonomyClass.PROMOTE.value,
            approval_required=["memory_admission", "pr_evidence"],
        ),
        approval_refs=(
            "approval:memory_admission:trace-123",
            "github_pr:https://github.com/jason-easyazz/zoe-ai-assistant/pull/302",
        ),
    )

    assert decision.allowed_to_execute is True


def test_missing_proposal_id_blocks_execution():
    decision = evaluate_execution_gate(
        _proposal(proposal_id=""),
        approval_refs=(
            "approval:install_or_runtime_change:jason:2026-06-10",
            "approval:security_review:jason:2026-06-10",
            "approval:admin:jason:2026-06-10",
            "pr:https://github.com/jason-easyazz/zoe-ai-assistant/pull/301",
        ),
    )

    assert decision.allowed_to_execute is False
    assert "proposal_id is required" in decision.blockers


def test_missing_approval_required_blocks_execution():
    decision = evaluate_execution_gate(_proposal(approval_required=[]))

    assert decision.allowed_to_execute is False
    assert "approval_required is required before execution" in decision.blockers
