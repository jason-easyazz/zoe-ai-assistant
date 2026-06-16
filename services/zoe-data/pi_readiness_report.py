"""Read-only operator report for Pi-as-Zoe-core promotion readiness."""

from __future__ import annotations

import os
from typing import Any, Mapping

from pi_hybrid_buffer import pi_hybrid_buffer_status
from pi_intent_shadow import pi_intent_shadow_status
from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS


def pi_readiness_report(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Build a side-effect-free report for Zoe's Pi hybrid promotion loop."""
    values = env if env is not None else os.environ
    hybrid = pi_hybrid_buffer_status(values)
    shadow = pi_intent_shadow_status(values)
    promotion = shadow.get("promotion_report") or {}
    contract = hybrid.get("contract") or {}
    actions = promotion.get("promotion_actions") or {}
    candidate_wins = promotion.get("candidate_wins") or {}
    state = _readiness_state(contract, promotion, actions)
    return {
        "report_kind": "zoe_pi_readiness_report",
        "state": state,
        "summary": _summary(state, contract, shadow, promotion, actions),
        "hybrid": {
            "mode": contract.get("mode"),
            "ready": bool(contract.get("ready")),
            "blockers": list(contract.get("blockers") or []),
            "warnings": list(contract.get("warnings") or []),
            "promoted_groups": list(contract.get("promoted_groups") or []),
        },
        "evidence": _evidence(shadow, promotion),
        "candidates": _candidate_details(candidate_wins),
        "blocked_decisions": _blocked_decisions(promotion),
        "promotion_actions": actions,
        "next_actions": _next_actions(state, contract, shadow, promotion, actions),
    }


def _readiness_state(
    contract: Mapping[str, Any],
    promotion: Mapping[str, Any],
    actions: Mapping[str, Any],
) -> str:
    rollback_groups = list(actions.get("rollback_groups") or promotion.get("rollback_groups") or [])
    promote_groups = list(actions.get("promote_groups") or promotion.get("promotable_groups") or [])
    if contract.get("blockers"):
        return "configuration_blocked"
    if rollback_groups:
        return "rollback_required"
    if promote_groups:
        return "promotion_apply_ready"
    candidate_groups = list(((promotion.get("candidate_wins") or {}).get("groups")) or [])
    if candidate_groups:
        return "collect_more_evidence"
    if _groups_with_blocker(promotion, "baseline_not_comparable"):
        return "measure_comparable_baseline"
    if not contract.get("ready"):
        return "buffer_not_ready"
    return "shadow_collecting"


def _summary(
    state: str,
    contract: Mapping[str, Any],
    shadow: Mapping[str, Any],
    promotion: Mapping[str, Any],
    actions: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = shadow.get("report") or {}
    return {
        "state": state,
        "mode": contract.get("mode"),
        "ready": bool(contract.get("ready")),
        "label_count": int(shadow.get("label_count") or 0),
        "labeled_sample_count": int(evidence.get("labeled_sample_count") or 0),
        "candidate_win_groups": list(((promotion.get("candidate_wins") or {}).get("groups")) or []),
        "promotion_ready_groups": list(promotion.get("promotable_groups") or []),
        "rollback_groups": list(actions.get("rollback_groups") or promotion.get("rollback_groups") or []),
        "requires_operator_apply": bool(actions.get("requires_operator_apply")),
    }


def _evidence(shadow: Mapping[str, Any], promotion: Mapping[str, Any]) -> dict[str, Any]:
    report = shadow.get("report") or {}
    source = promotion.get("source_breakdown") or {}
    return {
        "record_count_window": shadow.get("record_count_window"),
        "raw_record_count_window": shadow.get("raw_record_count_window"),
        "label_count": shadow.get("label_count"),
        "accuracy_available": bool(report.get("accuracy_available")),
        "labeled_sample_count": int(report.get("labeled_sample_count") or 0),
        "labeled_sample_count_by_group": dict(report.get("labeled_sample_count_by_group") or {}),
        "sample_deficit_by_group": dict(report.get("sample_deficit_by_group") or {}),
        "real_source_sample_count_by_group": dict(source.get("real_source_sample_count_by_group") or {}),
        "real_source_sample_deficit_by_group": dict(source.get("real_source_sample_deficit_by_group") or {}),
    }


def _candidate_details(candidate_wins: Mapping[str, Any]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for item in candidate_wins.get("details") or []:
        if not isinstance(item, Mapping):
            continue
        details.append(
            {
                "intent_group": item.get("intent_group"),
                "status": item.get("status"),
                "unique_case_count": item.get("unique_case_count"),
                "unique_case_deficit": item.get("unique_case_deficit"),
                "sample_deficit": item.get("sample_deficit"),
                "real_source_sample_deficit": item.get("real_source_sample_deficit"),
                "accuracy_delta": item.get("accuracy_delta"),
                "latency_delta_ms": item.get("latency_delta_ms"),
                "pi_p95_latency_ms": item.get("pi_p95_latency_ms"),
                "zoe_p95_latency_ms": item.get("zoe_p95_latency_ms"),
                "promotion_blockers": list(item.get("promotion_blockers") or []),
            }
        )
    return details


def _blocked_decisions(promotion: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for decision in promotion.get("decisions") or []:
        if not isinstance(decision, Mapping) or int(decision.get("sample_count") or 0) <= 0:
            continue
        blockers = list(decision.get("blockers") or [])
        if not blockers:
            continue
        blocked.append(
            {
                "intent_group": decision.get("intent_group"),
                "sample_count": decision.get("sample_count"),
                "blockers": blockers,
                "accuracy_delta": decision.get("accuracy_delta"),
                "latency_delta_ms": decision.get("latency_delta_ms"),
            }
        )
    return blocked


def _next_actions(
    state: str,
    contract: Mapping[str, Any],
    shadow: Mapping[str, Any],
    promotion: Mapping[str, Any],
    actions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    next_actions: list[dict[str, Any]] = []
    blockers = list(contract.get("blockers") or [])
    if blockers:
        next_actions.append(
            {
                "kind": "fix_configuration",
                "priority": "p0",
                "detail": "Resolve hybrid buffer blockers before promoting Pi routes.",
                "blockers": blockers,
            }
        )
    rollback_groups = list(actions.get("rollback_groups") or promotion.get("rollback_groups") or [])
    if rollback_groups:
        next_actions.append(
            {
                "kind": "rollback",
                "priority": "p0",
                "detail": "Remove regressed Pi promoted groups from ZOE_PI_INTENT_PROMOTED_GROUPS.",
                "groups": rollback_groups,
                "env": dict(actions.get("env") or {}),
            }
        )
    promote_groups = list(actions.get("promote_groups") or promotion.get("promotable_groups") or [])
    if promote_groups:
        next_actions.append(
            {
                "kind": "apply_promotion",
                "priority": "p1",
                "detail": "Operator can apply these low-risk Pi groups after reviewing the report.",
                "groups": promote_groups,
                "env": dict(actions.get("env") or {}),
            }
        )
    next_actions.extend(_evidence_collection_actions(promotion))
    baseline_groups = _groups_with_blocker(promotion, "baseline_not_comparable")
    if baseline_groups:
        next_actions.append(
            {
                "kind": "measure_comparable_baseline",
                "priority": "p1",
                "detail": "Measure Pi against Zoe Agent or operator fallback latency before judging promotion.",
                "groups": baseline_groups,
            }
        )
    if not next_actions and int(shadow.get("label_count") or 0) == 0:
        next_actions.append(
            {
                "kind": "label_shadow_evidence",
                "priority": "p1",
                "detail": "Label obvious shadow records before judging Pi speed and accuracy.",
            }
        )
    if not next_actions and state in {"shadow_collecting", "collect_more_evidence"}:
        next_actions.append(
            {
                "kind": "continue_shadow_mode",
                "priority": "p2",
                "detail": "Keep Pi in shadow mode and review new low-risk intent misses.",
            }
        )
    return next_actions


def _evidence_collection_actions(promotion: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in ((promotion.get("candidate_wins") or {}).get("details") or []):
        if not isinstance(item, Mapping):
            continue
        group = str(item.get("intent_group") or "")
        if group not in LOW_RISK_PI_INTENT_GROUPS:
            continue
        blockers = set(str(blocker) for blocker in item.get("promotion_blockers") or [])
        evidence_blockers = {"insufficient_samples", "insufficient_real_source_samples"}
        if blockers and not blockers <= evidence_blockers:
            continue
        unique_deficit = int(item.get("unique_case_deficit") or item.get("sample_deficit") or 0)
        real_source_deficit = int(item.get("real_source_sample_deficit") or 0)
        if unique_deficit <= 0 and real_source_deficit <= 0:
            continue
        action = {
            "kind": "collect_labeled_evidence",
            "priority": "p1",
            "intent_group": group,
            "needed_unique_cases": unique_deficit,
            "detail": f"Collect and label {unique_deficit} more unique {group} cases before promotion.",
        }
        if real_source_deficit > 0:
            action["needed_real_source_cases"] = real_source_deficit
        actions.append(action)
    return actions


def _groups_with_blocker(promotion: Mapping[str, Any], blocker: str) -> list[str]:
    groups: list[str] = []
    for decision in promotion.get("decisions") or []:
        if not isinstance(decision, Mapping) or int(decision.get("sample_count") or 0) <= 0:
            continue
        if blocker not in set(str(item) for item in decision.get("blockers") or []):
            continue
        group = str(decision.get("intent_group") or "")
        if group in LOW_RISK_PI_INTENT_GROUPS:
            groups.append(group)
    return sorted(set(groups))


__all__ = ["pi_readiness_report"]
