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


def test_migration_trigger_mirrors_the_policy_and_is_authoritative():
    """The 0027 trigger must OVERWRITE the autonomy columns, not merely fill NULLs.

    Filling-if-NULL let an INSERT hand itself autonomy_class='execute' for a
    non-executable type — making a proposal auto-implementable just by asking,
    which is exactly the bypass the policy exists to prevent. Only the TYPE decides.
    """
    from pathlib import Path
    mig = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0027_evolution_autonomy_contract.py"
    src = mig.read_text()
    else_branch = src[src.index('"  ELSE'):src.index('"  END IF;')]
    # unconditional assignment, no IS NULL guard
    assert "IS NULL" not in else_branch, "ELSE branch still only fills NULLs (fail-open)"
    assert "NEW.autonomy_class := 'prepare'" in else_branch
    assert "NEW.risk := 'medium'" in else_branch


def test_policy_and_migration_agree_on_the_executable_types():
    """The Python policy and the DB trigger must not drift."""
    from pathlib import Path
    mig = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
           / "0027_evolution_autonomy_contract.py").read_text()
    for t in ("intent_pattern", "user_frustration"):
        assert ea.contract_for_type(t)["autonomy_class"] == "execute"
        assert f'"{t}"' in mig or f"'{t}'" in mig
