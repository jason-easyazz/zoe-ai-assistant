"""Autonomy contract policy for evolution proposals — WHICH proposal types Zoe
may implement autonomously (write + gated-merge her own code) once an admin
approves, vs. which stay review-only.

This is a DELIBERATE OPERATOR OVERRIDE of the framework default. The evolution
framework marks all auto-generated proposals `PREPARE` ("review-only, no
execution is granted"). The operator chose (2026-07-24) to grant EXECUTE to a
NARROW set of low-risk, reversible types — the intent-handling fixes that proved
out end-to-end (e.g. PR #1555, the pharmacy business-hours fix) — while keeping
everything broad or security-touching review-only.

`autonomy_class` is the property the execution gate reads (only EXECUTE/PROMOTE
are auto-executable). `approval_required` is the approval evidence the gate
demands; an admin's approval satisfies `user_or_admin_for_privileged_execution`,
so an EXECUTE proposal with only that requirement auto-runs on admin approval.
A `security_review` requirement is NOT satisfiable by admin approval alone, so
security proposals never auto-run without that separate evidence.

This module is the app-side SOURCE OF TRUTH. The DB trigger + backfill installed
by alembic migration 0027 MIRROR this exact policy; keep them in sync (the test
suite pins the mapping).
"""
from __future__ import annotations

# Narrow, low-risk, reversible types the operator opted into auto-execution.
_EXECUTE_TYPES = frozenset({"intent_pattern", "user_frustration"})
# Types that must clear a human security review before any execution.
_SECURITY_TYPES = frozenset({"security_vulnerability", "security_improvement"})

_PRIVILEGED = "user_or_admin_for_privileged_execution"
_SECURITY_REVIEW = "security_review"


def contract_for_type(proposal_type: str | None) -> dict:
    """Return {autonomy_class, approval_required, risk} for a proposal type.

    Fail-safe default is review-only (`prepare`): an unrecognised type is never
    auto-executable."""
    t = (proposal_type or "").strip().lower()
    if t in _EXECUTE_TYPES:
        # DELIBERATELY no `pr_evidence` here, although the canonical intake maps
        # EXECUTE -> pr_evidence. In that framework, "execute" meant promoting an
        # ALREADY-BUILT PR, so PR evidence had to exist up front. In this lane the
        # PR is the OUTPUT of execution (approve -> board issue -> Omnigent builds
        # the PR -> focused tests + review gate + greploop merge), so requiring
        # pr_evidence pre-execution is unsatisfiable by construction — and the
        # evidence the class exists to guarantee is enforced downstream by the
        # PR's own merge gates instead.
        return {
            "autonomy_class": "execute",
            "approval_required": [_PRIVILEGED],
            "risk": "low",
        }
    if t in _SECURITY_TYPES:
        return {
            "autonomy_class": "prepare",  # review-only; needs a real security review
            "approval_required": [_SECURITY_REVIEW, _PRIVILEGED],
            "risk": "high",
        }
    return {
        "autonomy_class": "prepare",
        "approval_required": [_PRIVILEGED],
        "risk": "medium",
    }
