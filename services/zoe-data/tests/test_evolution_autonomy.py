"""Autonomy contract policy — the operator's execute/review-only mapping."""
import pytest

import evolution_autonomy as ea

pytestmark = pytest.mark.ci_safe


def test_intent_fix_types_are_executable():
    for t in ("intent_pattern", "user_frustration"):
        c = ea.contract_for_type(t)
        assert c["autonomy_class"] == "execute"
        assert c["approval_required"] == ["user_or_admin_for_privileged_execution"]
        assert c["risk"] == "low"


def test_security_types_are_review_only_and_security_gated():
    for t in ("security_vulnerability", "security_improvement"):
        c = ea.contract_for_type(t)
        assert c["autonomy_class"] == "prepare"          # NOT auto-executable
        assert "security_review" in c["approval_required"]  # needs a real review
        assert c["risk"] == "high"


def test_everything_else_fails_closed_review_only():
    for t in ("code_improvement", "agent_health", "charter_gap", "ux_improvement",
              "user_issue_report", "", None, "made_up_type"):
        assert ea.contract_for_type(t)["autonomy_class"] == "prepare"
