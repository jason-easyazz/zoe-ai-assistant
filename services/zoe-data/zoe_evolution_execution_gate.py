"""Execution approval gates for Zoe self-evolution proposals.

Proposal creation and Multica admission can prepare work, but privileged
execution needs explicit evidence that each required approval class was met.
This module is intentionally deterministic and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from zoe_evolution_proposal import TrustAutonomyClass


_APPROVAL_REF_PREFIX = {
    "device_action_confirmation": ("approval:device_action_confirmation:",),
    "install_or_runtime_change": ("approval:install_or_runtime_change:",),
    "license_review": ("approval:license_review:",),
    "memory_admission": ("approval:memory_admission:",),
    "pr_evidence": ("pr:", "github_pr:"),
    "secret_access": ("approval:secret_access:",),
    "security_review": ("approval:security_review:",),
    "sidecar_start": ("approval:sidecar_start:",),
    "user_or_admin_for_privileged_execution": (
        "approval:user:",
        "approval:admin:",
        "approval:user_or_admin_for_privileged_execution:",
    ),
}


@dataclass(frozen=True)
class ExecutionGateDecision:
    proposal_id: str
    allowed_to_execute: bool
    missing_approval_classes: tuple[str, ...]
    unknown_approval_classes: tuple[str, ...]
    blockers: tuple[str, ...]
    approval_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "allowed_to_execute": self.allowed_to_execute,
            "missing_approval_classes": list(self.missing_approval_classes),
            "unknown_approval_classes": list(self.unknown_approval_classes),
            "blockers": list(self.blockers),
            "approval_refs": list(self.approval_refs),
        }


def evaluate_execution_gate(
    proposal: Mapping[str, Any],
    *,
    approval_refs: Sequence[str] = (),
) -> ExecutionGateDecision:
    """Return whether a proposal has enough evidence to execute.

    The proposal payload should be the `proposal` object from a
    `zoe_evolution_proposal` contract snapshot. Unknown approval classes are
    treated as blockers so execution fails closed.
    """

    proposal_id = str(proposal.get("proposal_id") or "")
    autonomy_class = str(proposal.get("autonomy_class") or "")
    approval_required = tuple(str(item) for item in proposal.get("approval_required") or ())
    refs = tuple(str(item) for item in approval_refs if str(item).strip())
    blockers: list[str] = []

    if not proposal_id:
        blockers.append("proposal_id is required")
    if autonomy_class not in {TrustAutonomyClass.EXECUTE.value, TrustAutonomyClass.PROMOTE.value}:
        blockers.append("proposal autonomy class is not executable")
    if not approval_required:
        blockers.append("approval_required is required before execution")

    missing: list[str] = []
    unknown: list[str] = []
    for approval_class in approval_required:
        prefixes = _APPROVAL_REF_PREFIX.get(approval_class)
        if prefixes is None:
            unknown.append(approval_class)
            blockers.append(f"unknown approval class: {approval_class}")
            continue
        if not any(ref.startswith(prefix) for prefix in prefixes for ref in refs):
            missing.append(approval_class)

    if missing:
        blockers.append("missing approval evidence: " + ", ".join(sorted(set(missing))))

    return ExecutionGateDecision(
        proposal_id=proposal_id,
        allowed_to_execute=not blockers,
        missing_approval_classes=tuple(sorted(set(missing))),
        unknown_approval_classes=tuple(sorted(set(unknown))),
        blockers=tuple(blockers),
        approval_refs=refs,
    )


__all__ = ["ExecutionGateDecision", "evaluate_execution_gate"]
