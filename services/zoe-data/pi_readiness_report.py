"""Read-only operator report for Pi-as-Zoe-core promotion readiness."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from pi_hybrid_buffer import pi_hybrid_buffer_status
from pi_intent_evidence import apply_pi_hybrid_production_labels, load_pi_hybrid_production_labels
from pi_intent_shadow import pi_intent_shadow_status
from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS

DEFAULT_EVAL_REPORT_PATH = "~/.zoe/data/pi-promotion-eval-report.json"
DEFAULT_PRODUCTION_EVIDENCE_PATH = "~/.zoe/data/pi-hybrid-production-evidence.jsonl"
DEFAULT_PRODUCTION_LABELS_PATH = "~/.zoe/data/pi-hybrid-production-labels.jsonl"


def pi_readiness_report(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Build a side-effect-free report for Zoe's Pi hybrid promotion loop."""
    values = env if env is not None else os.environ
    hybrid = pi_hybrid_buffer_status(values)
    shadow = pi_intent_shadow_status(values)
    promotion = shadow.get("promotion_report") or {}
    eval_report = _load_eval_report(values)
    benchmark = _benchmark_from_eval_report(eval_report)
    production = _load_production_evidence(values)
    benchmark_promotion = benchmark.get("promotion_report") or {}
    contract = hybrid.get("contract") or {}
    actions = promotion.get("promotion_actions") or {}
    candidate_wins = _combined_candidate_wins(promotion, benchmark_promotion)
    state = _readiness_state(contract, promotion, actions, benchmark_promotion=benchmark_promotion)
    return {
        "report_kind": "zoe_pi_readiness_report",
        "state": state,
        "summary": _summary(state, contract, shadow, promotion, actions, benchmark, production),
        "hybrid": {
            "mode": contract.get("mode"),
            "ready": bool(contract.get("ready")),
            "blockers": list(contract.get("blockers") or []),
            "warnings": list(contract.get("warnings") or []),
            "promoted_groups": list(contract.get("promoted_groups") or []),
        },
        "evidence": _evidence(shadow, promotion, benchmark, production),
        "benchmark": benchmark,
        "production_evidence": production,
        "candidates": _candidate_details(candidate_wins),
        "blocked_decisions": _blocked_decisions(promotion),
        "promotion_actions": actions,
        "next_actions": _next_actions(
            state,
            contract,
            shadow,
            promotion,
            actions,
            benchmark_promotion=benchmark_promotion,
            production=production,
        ),
    }


def _readiness_state(
    contract: Mapping[str, Any],
    promotion: Mapping[str, Any],
    actions: Mapping[str, Any],
    *,
    benchmark_promotion: Mapping[str, Any] | None = None,
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
    benchmark_candidate_groups = list((((benchmark_promotion or {}).get("candidate_wins") or {}).get("groups")) or [])
    if candidate_groups or benchmark_candidate_groups:
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
    benchmark: Mapping[str, Any] | None = None,
    production: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = shadow.get("report") or {}
    benchmark = benchmark or {}
    production = production or {}
    return {
        "state": state,
        "mode": contract.get("mode"),
        "ready": bool(contract.get("ready")),
        "label_count": int(shadow.get("label_count") or 0),
        "labeled_sample_count": int(evidence.get("labeled_sample_count") or 0),
        "candidate_win_groups": list(((promotion.get("candidate_wins") or {}).get("groups")) or []),
        "benchmark_candidate_win_groups": list(((benchmark.get("candidate_wins") or {}).get("groups")) or []),
        "promotion_ready_groups": list(promotion.get("promotable_groups") or []),
        "rollback_groups": list(actions.get("rollback_groups") or promotion.get("rollback_groups") or []),
        "requires_operator_apply": bool(actions.get("requires_operator_apply")),
        "production_record_count": int(production.get("record_count") or 0),
        "production_accepted_count": int(production.get("accepted_count") or 0),
        "production_label_count": int(production.get("label_count") or 0),
        "production_unlabeled_count": int(production.get("unlabeled_count") or 0),
        "production_groups": list((production.get("by_group") or {}).keys()),
    }


def _evidence(
    shadow: Mapping[str, Any],
    promotion: Mapping[str, Any],
    benchmark: Mapping[str, Any] | None = None,
    production: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = shadow.get("report") or {}
    source = promotion.get("source_breakdown") or {}
    benchmark = benchmark or {}
    production = production or {}
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
        "benchmark_report_path": benchmark.get("path"),
        "benchmark_loaded": bool(benchmark.get("loaded")),
        "benchmark_candidate_win_groups": list((benchmark.get("candidate_wins") or {}).get("groups") or []),
        "production_record_count": int(production.get("record_count") or 0),
        "production_accepted_count": int(production.get("accepted_count") or 0),
        "production_label_count": int(production.get("label_count") or 0),
        "production_unlabeled_count": int(production.get("unlabeled_count") or 0),
        "production_record_count_by_group": {
            group: int(stats.get("record_count") or 0)
            for group, stats in (production.get("by_group") or {}).items()
            if isinstance(stats, Mapping)
        },
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
    *,
    benchmark_promotion: Mapping[str, Any] | None = None,
    production: Mapping[str, Any] | None = None,
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
    shadow_evidence_actions = _evidence_collection_actions(promotion)
    next_actions.extend(shadow_evidence_actions)
    shadow_action_groups = {
        str(action.get("intent_group")) for action in shadow_evidence_actions if action.get("intent_group")
    }
    next_actions.extend(
        action
        for action in _evidence_collection_actions(benchmark_promotion or {}, source="benchmark")
        if str(action.get("intent_group")) not in shadow_action_groups
    )
    baseline_groups = _groups_with_blocker(promotion, "baseline_not_comparable")
    benchmark_groups = _benchmark_candidate_groups(benchmark_promotion)
    if baseline_groups and benchmark_groups:
        baseline_groups = [g for g in baseline_groups if g not in benchmark_groups]
    if baseline_groups:
        next_actions.append(
            {
                "kind": "measure_comparable_baseline",
                "priority": "p1",
                "detail": "Measure Pi against Zoe Agent or operator fallback latency before judging promotion.",
                "groups": baseline_groups,
            }
        )
    next_actions.extend(_production_evidence_actions(production or {}))
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


def _evidence_collection_actions(promotion: Mapping[str, Any], *, source: str = "shadow") -> list[dict[str, Any]]:
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
        if unique_deficit > 0 and real_source_deficit > 0:
            detail = (
                f"Collect and label {unique_deficit} more unique {group} cases "
                f"({real_source_deficit} must be real/log-derived) before promotion."
            )
        elif real_source_deficit > 0:
            detail = (
                f"Collect {real_source_deficit} real/log-derived {group} samples "
                f"(pi_intent_shadow, intent_miss, chat_log, etc.) before promotion."
            )
        else:
            detail = f"Collect and label {unique_deficit} more unique {group} cases before promotion."
        action = {
            "kind": "collect_labeled_evidence",
            "priority": "p1",
            "intent_group": group,
            "needed_unique_cases": unique_deficit,
            "detail": detail,
        }
        if source != "shadow":
            action["evidence_source"] = source
        if real_source_deficit > 0:
            action["needed_real_source_cases"] = real_source_deficit
        actions.append(action)
    return actions


def _production_evidence_actions(production: Mapping[str, Any]) -> list[dict[str, Any]]:
    unlabeled = int(production.get("unlabeled_count") or 0)
    if unlabeled <= 0:
        return []
    groups = sorted(
        group
        for group, stats in (production.get("by_group") or {}).items()
        if isinstance(stats, Mapping) and int(stats.get("unlabeled_count") or 0) > 0
    )
    return [
        {
            "kind": "label_production_evidence",
            "priority": "p1",
            "detail": f"Label {unlabeled} Pi hybrid production evidence records before promotion scoring.",
            "path": production.get("path"),
            "labels_path": production.get("labels_path"),
            "groups": groups,
        }
    ]


def _load_production_evidence(env: Mapping[str, str]) -> dict[str, Any]:
    raw_path = (env.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH") or DEFAULT_PRODUCTION_EVIDENCE_PATH).strip()
    raw_labels_path = (env.get("ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH") or DEFAULT_PRODUCTION_LABELS_PATH).strip()
    enabled = _env_bool(env.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"), default=False)
    if not raw_path:
        return {"enabled": enabled, "loaded": False, "path": None, "labels_path": None, "record_count": 0}
    path = Path(raw_path).expanduser()
    labels_path = Path(raw_labels_path).expanduser() if raw_labels_path else None
    if not enabled:
        return {
            "enabled": False,
            "loaded": False,
            "path": str(path),
            "labels_path": str(labels_path) if labels_path else None,
            "record_count": 0,
            "disabled": True,
        }
    if not path.exists():
        return {
            "enabled": enabled,
            "loaded": False,
            "path": str(path),
            "labels_path": str(labels_path) if labels_path else None,
            "record_count": 0,
        }
    records: list[dict[str, Any]] = []
    invalid_lines = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    invalid_lines += 1
                    continue
                if isinstance(payload, Mapping):
                    records.append(dict(payload))
                else:
                    invalid_lines += 1
    except OSError as exc:
        return {
            "enabled": enabled,
            "loaded": False,
            "path": str(path),
            "labels_path": str(labels_path) if labels_path else None,
            "record_count": 0,
            "error": exc.__class__.__name__,
        }
    label_error = None
    try:
        labels = load_pi_hybrid_production_labels(str(labels_path)) if labels_path else {}
    except OSError as exc:
        labels = {}
        label_error = exc.__class__.__name__
    labeled_records = apply_pi_hybrid_production_labels(records, labels)
    summary = _summarize_production_records(labeled_records)
    result = {
        "enabled": enabled,
        "loaded": True,
        "path": str(path),
        "labels_path": str(labels_path) if labels_path else None,
        "label_count": len(labels),
        "invalid_lines": invalid_lines,
        **summary,
    }
    if label_error:
        result["label_error"] = label_error
    return result


def _summarize_production_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_group: dict[str, dict[str, Any]] = {}
    accepted_count = 0
    route_change_count = 0
    unlabeled_count = 0
    recent: list[dict[str, Any]] = []
    for record in records:
        group = _production_group(record)
        stats = by_group.setdefault(
            group,
            {
                "record_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "route_change_count": 0,
                "unlabeled_count": 0,
                "pi_latency_ms": [],
                "safe_fulfillment_latency_ms": [],
            },
        )
        stats["record_count"] += 1
        if record.get("accepted"):
            accepted_count += 1
            stats["accepted_count"] += 1
        else:
            stats["rejected_count"] += 1
        if record.get("production_route_change"):
            route_change_count += 1
            stats["route_change_count"] += 1
        if not record.get("outcome_label") and not record.get("negative"):
            unlabeled_count += 1
            stats["unlabeled_count"] += 1
        _append_number(stats["pi_latency_ms"], record.get("pi_latency_ms"))
        _append_number(stats["safe_fulfillment_latency_ms"], record.get("safe_fulfillment_latency_ms"))
        recent.append(_compact_production_record(record, group))
    compact_groups: dict[str, dict[str, Any]] = {}
    for group, stats in sorted(by_group.items()):
        compact_groups[group] = {
            "record_count": stats["record_count"],
            "accepted_count": stats["accepted_count"],
            "rejected_count": stats["rejected_count"],
            "route_change_count": stats["route_change_count"],
            "unlabeled_count": stats["unlabeled_count"],
            "pi_p95_latency_ms": _p95(stats["pi_latency_ms"]),
            "safe_fulfillment_p95_latency_ms": _p95(stats["safe_fulfillment_latency_ms"]),
        }
    return {
        "record_count": len(records),
        "accepted_count": accepted_count,
        "rejected_count": len(records) - accepted_count,
        "route_change_count": route_change_count,
        "unlabeled_count": unlabeled_count,
        "by_group": compact_groups,
        "recent": recent[-5:],
    }


def _production_group(record: Mapping[str, Any]) -> str:
    for key in ("intent_group", "pi_intent_group", "intent", "pi_intent"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def _compact_production_record(record: Mapping[str, Any], group: str) -> dict[str, Any]:
    return {
        "ts": record.get("ts"),
        "text_hash": record.get("text_hash"),
        "intent_group": group,
        "accepted": bool(record.get("accepted")),
        "reason": record.get("reason"),
        "intent": record.get("intent"),
        "pi_intent": record.get("pi_intent"),
        "pi_latency_ms": _float_or_none(record.get("pi_latency_ms")),
        "safe_fulfillment_latency_ms": _float_or_none(record.get("safe_fulfillment_latency_ms")),
        "production_route_change": bool(record.get("production_route_change")),
        "outcome_label": record.get("outcome_label"),
        "outcome_label_source": record.get("outcome_label_source"),
        "text_preview": record.get("text_preview"),
    }


def _append_number(values: list[float], value: Any) -> None:
    number = _float_or_none(value)
    if number is not None:
        values.append(number)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_eval_report(env: Mapping[str, str]) -> dict[str, Any]:
    raw_path = (env.get("ZOE_PI_PROMOTION_EVAL_REPORT_PATH") or DEFAULT_EVAL_REPORT_PATH).strip()
    if not raw_path:
        return {"loaded": False, "path": None}
    path = Path(raw_path).expanduser()
    if not path.exists():
        return {"loaded": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"loaded": False, "path": str(path), "error": exc.__class__.__name__}
    if not isinstance(payload, Mapping):
        return {"loaded": False, "path": str(path), "error": "invalid_report"}
    return {"loaded": True, "path": str(path), "payload": payload}


def _benchmark_from_eval_report(eval_report: Mapping[str, Any]) -> dict[str, Any]:
    payload = eval_report.get("payload") if isinstance(eval_report, Mapping) else None
    if not isinstance(payload, Mapping):
        return {"loaded": bool(eval_report.get("loaded")), "path": eval_report.get("path"), "error": eval_report.get("error")}
    promotion = payload.get("promotion_report") if isinstance(payload.get("promotion_report"), Mapping) else {}
    return {
        "loaded": True,
        "path": eval_report.get("path"),
        "readiness": payload.get("readiness") if isinstance(payload.get("readiness"), Mapping) else {},
        "promotion_report": promotion,
        "candidate_wins": promotion.get("candidate_wins") or {},
        "source_breakdown": promotion.get("source_breakdown") or {},
    }


def _combined_candidate_wins(
    promotion: Mapping[str, Any], benchmark_promotion: Mapping[str, Any] | None
) -> dict[str, Any]:
    primary = promotion.get("candidate_wins") if isinstance(promotion.get("candidate_wins"), Mapping) else {}
    benchmark = (benchmark_promotion or {}).get("candidate_wins")
    if not isinstance(benchmark, Mapping) or not benchmark.get("details"):
        return dict(primary)
    if not primary.get("details"):
        return {**benchmark, "source": "benchmark"}
    details = list(primary.get("details") or [])
    seen = {str(item.get("intent_group")) for item in details if isinstance(item, Mapping)}
    for item in benchmark.get("details") or []:
        if isinstance(item, Mapping) and str(item.get("intent_group")) not in seen:
            details.append({**item, "source": "benchmark"})
    groups = sorted({str(item.get("intent_group")) for item in details if isinstance(item, Mapping) and item.get("intent_group")})
    return {**primary, "details": details, "groups": groups}


def _benchmark_candidate_groups(benchmark_promotion: Mapping[str, Any] | None) -> list[str]:
    return list((((benchmark_promotion or {}).get("candidate_wins") or {}).get("groups")) or [])


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


__all__ = ["pi_readiness_report", "DEFAULT_EVAL_REPORT_PATH", "DEFAULT_PRODUCTION_EVIDENCE_PATH", "DEFAULT_PRODUCTION_LABELS_PATH"]
