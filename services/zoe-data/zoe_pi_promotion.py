"""Speed/accuracy gates for promoting Pi intent routing.

This module is intentionally side-effect free. Runtime shadow collection and
promotion writers can call these helpers, but the first contract is the judge:
Pi only earns a route when it is more accurate and faster than Zoe's current
comparable path for a low-risk intent group.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


LOW_RISK_PI_INTENT_GROUPS = {
    "weather": {"weather"},
    "reminders": {"reminder_list", "reminder_create"},
    "lists": {"list_show", "list_add", "list_remove"},
    "timers": {"timer_create"},
    "calculations": {"calculate"},
    "daily_briefing": {"daily_briefing"},
}

PRIVILEGED_INTENTS = {
    "extend_capability",
    "user_issue_report",
    "memory_write",
    "device_control",
    "secret_use",
    "payment",
    "install_runtime",
}

# Static eval-case labels. Runtime-only sources such as pi_intent_shadow are
# intentionally excluded because they are produced after collection/labeling.
EVAL_SOURCES = {"synthetic", "intent_miss", "chat_log", "voice_log", "known_failure"}
# Sources counted as real/log-derived promotion evidence. Synthetic cases remain
# useful smoke tests, but should be visible separately in promotion reports.
REAL_PROMOTION_EVIDENCE_SOURCES = {"intent_miss", "chat_log", "voice_log", "known_failure", "pi_intent_shadow"}
ROUTE_CLASSES = {"deterministic", "fallback", "extraction_failed"}
PI_TRANSPORTS = {"print", "rpc"}
DECISION_STATES = {"promote", "keep_shadow", "rollback", "blocked"}


@dataclass(frozen=True)
class PiIntentEvalCase:
    case_id: str
    text: str
    expected_intent: str | None
    intent_group: str
    route_class: str
    source: str = "synthetic"
    negative: bool = False

    def validate(self) -> None:
        if not self.case_id:
            raise ValueError("case_id is required")
        if not self.text:
            raise ValueError(f"{self.case_id}: text is required")
        if self.intent_group not in LOW_RISK_PI_INTENT_GROUPS and self.intent_group != "chat":
            raise ValueError(f"{self.case_id}: unknown intent_group {self.intent_group!r}")
        if self.route_class not in ROUTE_CLASSES:
            raise ValueError(f"{self.case_id}: unknown route_class {self.route_class!r}")
        if self.source not in EVAL_SOURCES:
            raise ValueError(f"{self.case_id}: unknown source {self.source!r}")
        if self.negative and self.expected_intent is not None:
            raise ValueError(f"{self.case_id}: negative cases must not expect an intent")
        if not self.negative and not self.expected_intent:
            raise ValueError(f"{self.case_id}: expected_intent is required")
        if self.expected_intent in PRIVILEGED_INTENTS:
            raise ValueError(f"{self.case_id}: privileged intents are not eligible for Pi auto-promotion")
        if (
            not self.negative
            and self.intent_group != "chat"
            and self.expected_intent not in LOW_RISK_PI_INTENT_GROUPS[self.intent_group]
        ):
            raise ValueError(f"{self.case_id}: expected_intent does not belong to intent_group {self.intent_group!r}")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, source_path: str = "<memory>") -> "PiIntentEvalCase":
        case = cls(
            case_id=str(payload.get("case_id") or "").strip(),
            text=str(payload.get("text") or "").strip(),
            expected_intent=_optional_str(payload.get("expected_intent")),
            intent_group=str(payload.get("intent_group") or "").strip(),
            route_class=str(payload.get("route_class") or "").strip(),
            source=str(payload.get("source") or "synthetic").strip(),
            negative=_bool_value(payload.get("negative", False)),
        )
        try:
            case.validate()
        except ValueError as exc:
            raise ValueError(f"{source_path}: {exc}") from exc
        return case

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "case_id": self.case_id,
            "text": self.text,
            "expected_intent": self.expected_intent,
            "intent_group": self.intent_group,
            "route_class": self.route_class,
            "source": self.source,
            "negative": self.negative,
        }


@dataclass(frozen=True)
class PiRouteSample:
    case_id: str
    intent_group: str
    expected_intent: str | None
    zoe_intent: str | None
    pi_intent: str | None
    zoe_latency_ms: float
    pi_latency_ms: float
    pi_confidence: float = 0.0
    pi_transport: str = "rpc"
    route_class: str = "fallback"
    user_corrected: bool = False
    timed_out: bool = False
    rollback_blocked: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.case_id:
            raise ValueError("case_id is required")
        if self.intent_group not in LOW_RISK_PI_INTENT_GROUPS:
            raise ValueError(f"{self.case_id}: intent_group is not auto-promotable")
        if self.expected_intent in PRIVILEGED_INTENTS or self.pi_intent in PRIVILEGED_INTENTS:
            raise ValueError(f"{self.case_id}: privileged intents cannot be auto-promoted")
        if self.zoe_latency_ms < 0 or self.pi_latency_ms < 0:
            raise ValueError(f"{self.case_id}: latency must be non-negative")
        if not 0 <= self.pi_confidence <= 1:
            raise ValueError(f"{self.case_id}: pi_confidence must be between 0 and 1")
        if self.route_class not in ROUTE_CLASSES:
            raise ValueError(f"{self.case_id}: unknown route_class {self.route_class!r}")
        if self.pi_transport not in PI_TRANSPORTS:
            raise ValueError(f"{self.case_id}: unknown pi_transport {self.pi_transport!r}")
        _reject_secret_keys(self.metadata, sample_id=self.case_id)

    @property
    def zoe_correct(self) -> bool:
        return self.zoe_intent == self.expected_intent

    @property
    def pi_correct(self) -> bool:
        return self.pi_intent == self.expected_intent and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "case_id": self.case_id,
            "intent_group": self.intent_group,
            "expected_intent": self.expected_intent,
            "zoe_intent": self.zoe_intent,
            "pi_intent": self.pi_intent,
            "zoe_latency_ms": self.zoe_latency_ms,
            "pi_latency_ms": self.pi_latency_ms,
            "pi_confidence": self.pi_confidence,
            "pi_transport": self.pi_transport,
            "route_class": self.route_class,
            "zoe_correct": self.zoe_correct,
            "pi_correct": self.pi_correct,
            "user_corrected": self.user_corrected,
            "timed_out": self.timed_out,
            "rollback_blocked": self.rollback_blocked,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PiPromotionPolicy:
    min_samples: int = 30
    accuracy_win_margin: float = 0.05
    max_timeout_rate: float = 0.05
    max_correction_rate: float = 0.03
    require_latency_win: bool = True
    require_comparable_baseline: bool = True

    def validate(self) -> None:
        if self.min_samples <= 0:
            raise ValueError("min_samples must be positive")
        for field_name in ("accuracy_win_margin", "max_timeout_rate", "max_correction_rate"):
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class PiPromotionDecision:
    intent_group: str
    state: str
    blockers: tuple[str, ...]
    sample_count: int
    zoe_accuracy: float
    pi_accuracy: float
    accuracy_delta: float
    zoe_p95_latency_ms: float | None
    pi_p95_latency_ms: float | None
    latency_delta_ms: float | None
    timeout_rate: float
    correction_rate: float

    def to_dict(self) -> dict[str, Any]:
        if self.state not in DECISION_STATES:
            raise ValueError(f"unknown decision state {self.state!r}")
        return {
            "intent_group": self.intent_group,
            "state": self.state,
            "blockers": list(self.blockers),
            "sample_count": self.sample_count,
            "zoe_accuracy": self.zoe_accuracy,
            "pi_accuracy": self.pi_accuracy,
            "accuracy_delta": self.accuracy_delta,
            "zoe_p95_latency_ms": self.zoe_p95_latency_ms,
            "pi_p95_latency_ms": self.pi_p95_latency_ms,
            "latency_delta_ms": self.latency_delta_ms,
            "timeout_rate": self.timeout_rate,
            "correction_rate": self.correction_rate,
        }


DEFAULT_PI_INTENT_EVAL_CASES: tuple[PiIntentEvalCase, ...] = (
    PiIntentEvalCase("weather_rain_later", "rain later", "weather", "weather", "fallback"),
    PiIntentEvalCase("weather_jacket", "need a jacket tonight", "weather", "weather", "fallback"),
    PiIntentEvalCase("reminder_due", "anything due today", "reminder_list", "reminders", "fallback"),
    PiIntentEvalCase("reminder_create", "remind me to call mum", "reminder_create", "reminders", "extraction_failed"),
    PiIntentEvalCase("list_show", "what is on the shopping list", "list_show", "lists", "deterministic"),
    PiIntentEvalCase("list_add", "add bread to shopping", "list_add", "lists", "extraction_failed"),
    PiIntentEvalCase("timer", "timer for ten minutes", "timer_create", "timers", "fallback"),
    PiIntentEvalCase("calc", "what is 18 times 7", "calculate", "calculations", "fallback"),
    PiIntentEvalCase("briefing", "what is my day looking like", "daily_briefing", "daily_briefing", "fallback"),
    PiIntentEvalCase("casual_breakfast", "I like the breakfast service", None, "chat", "fallback", negative=True),
    PiIntentEvalCase("casual_memory", "that movie was pretty good", None, "chat", "fallback", negative=True),
)


def intent_group_for_intent(intent: str | None) -> str | None:
    if not intent:
        return None
    for group, intents in LOW_RISK_PI_INTENT_GROUPS.items():
        if intent in intents:
            return group
    return None


def evaluate_pi_promotion(
    samples: Sequence[PiRouteSample],
    *,
    intent_group: str,
    policy: PiPromotionPolicy | None = None,
    promoted: bool = False,
) -> PiPromotionDecision:
    active_policy = policy or PiPromotionPolicy()
    active_policy.validate()
    if intent_group not in LOW_RISK_PI_INTENT_GROUPS:
        return _empty_decision(intent_group, state="blocked", blockers=("intent_group_not_allowlisted",))
    group_samples = [sample for sample in samples if sample.intent_group == intent_group]
    for sample in group_samples:
        sample.validate()
    sample_count = len(group_samples)
    if sample_count == 0:
        state = "rollback" if promoted else "keep_shadow"
        return _empty_decision(intent_group, state=state, blockers=("insufficient_samples",))

    zoe_accuracy = _rate(sample.zoe_correct for sample in group_samples)
    pi_accuracy = _rate(sample.pi_correct for sample in group_samples)
    accuracy_delta = pi_accuracy - zoe_accuracy
    zoe_p95 = _percentile([sample.zoe_latency_ms for sample in group_samples], 95)
    pi_p95 = _percentile([sample.pi_latency_ms for sample in group_samples], 95)
    latency_delta = None if zoe_p95 is None or pi_p95 is None else zoe_p95 - pi_p95
    timeout_rate = _rate(sample.timed_out for sample in group_samples)
    correction_rate = _rate(sample.user_corrected for sample in group_samples)
    blockers: list[str] = []
    if sample_count < active_policy.min_samples:
        blockers.append("insufficient_samples")
    if accuracy_delta < active_policy.accuracy_win_margin:
        blockers.append("accuracy_delta_below_threshold")
    if active_policy.require_latency_win and (latency_delta is None or latency_delta <= 0):
        blockers.append("latency_not_faster_than_zoe")
    if timeout_rate > active_policy.max_timeout_rate:
        blockers.append("timeout_rate_too_high")
    if correction_rate > active_policy.max_correction_rate:
        blockers.append("correction_rate_too_high")
    if active_policy.require_comparable_baseline and any(
        sample.metadata.get("baseline_comparable") is False for sample in group_samples
    ):
        blockers.append("baseline_not_comparable")
    if any(sample.rollback_blocked for sample in group_samples):
        blockers.append("rollback_blocked")

    state = "promote" if not blockers else "keep_shadow"
    rollback_blockers = {
        "accuracy_delta_below_threshold",
        "insufficient_samples",
        "timeout_rate_too_high",
        "correction_rate_too_high",
        "latency_not_faster_than_zoe",
    }
    # Non-comparable baseline evidence blocks promotion, but does not prove a promoted route regressed.
    if promoted and rollback_blockers.intersection(blockers):
        state = "rollback"
    if "rollback_blocked" in blockers:
        state = "blocked"
    return PiPromotionDecision(
        intent_group=intent_group,
        state=state,
        blockers=tuple(blockers),
        sample_count=sample_count,
        zoe_accuracy=zoe_accuracy,
        pi_accuracy=pi_accuracy,
        accuracy_delta=accuracy_delta,
        zoe_p95_latency_ms=zoe_p95,
        pi_p95_latency_ms=pi_p95,
        latency_delta_ms=latency_delta,
        timeout_rate=timeout_rate,
        correction_rate=correction_rate,
    )


def summarize_pi_promotion(
    samples: Sequence[PiRouteSample],
    *,
    policy: PiPromotionPolicy | None = None,
    promoted_groups: Sequence[str] | None = None,
) -> dict[str, Any]:
    active_policy = policy or PiPromotionPolicy()
    active_promoted_groups = set(promoted_groups or ())
    unknown_groups = sorted(active_promoted_groups - set(LOW_RISK_PI_INTENT_GROUPS))
    if unknown_groups:
        raise ValueError(f"unknown promoted_groups: {', '.join(unknown_groups)}")
    decisions = [
        evaluate_pi_promotion(
            samples,
            intent_group=group,
            policy=active_policy,
            promoted=group in active_promoted_groups,
        ).to_dict()
        for group in sorted(LOW_RISK_PI_INTENT_GROUPS)
    ]
    promotable_groups = [decision["intent_group"] for decision in decisions if decision["state"] == "promote"]
    rollback_groups = [decision["intent_group"] for decision in decisions if decision["state"] == "rollback"]
    return {
        "policy": {
            "min_samples": active_policy.min_samples,
            "accuracy_win_margin": active_policy.accuracy_win_margin,
            "max_timeout_rate": active_policy.max_timeout_rate,
            "max_correction_rate": active_policy.max_correction_rate,
            "require_latency_win": active_policy.require_latency_win,
            "require_comparable_baseline": active_policy.require_comparable_baseline,
        },
        "sample_count": len(samples),
        "promoted_groups": sorted(active_promoted_groups),
        "decisions": decisions,
        "promotable_groups": promotable_groups,
        "rollback_groups": rollback_groups,
        "route_class_breakdown": build_pi_route_class_breakdown(samples),
        "transport_breakdown": build_pi_transport_breakdown(samples),
        "source_breakdown": build_pi_source_breakdown(samples, policy=active_policy),
        "failure_examples": build_pi_failure_examples(samples),
        "promotion_actions": build_pi_promotion_actions(
            current_promoted_groups=sorted(active_promoted_groups),
            promotable_groups=promotable_groups,
            rollback_groups=rollback_groups,
        ),
    }


def build_pi_route_class_breakdown(samples: Sequence[PiRouteSample]) -> dict[str, dict[str, Any]]:
    breakdown: dict[str, dict[str, Any]] = {}
    for route_class in sorted(ROUTE_CLASSES):
        route_samples = [sample for sample in samples if sample.route_class == route_class]
        for sample in route_samples:
            sample.validate()
        zoe_p95 = _percentile([sample.zoe_latency_ms for sample in route_samples], 95)
        pi_p95 = _percentile([sample.pi_latency_ms for sample in route_samples], 95)
        zoe_accuracy = _rate(sample.zoe_correct for sample in route_samples)
        pi_accuracy = _rate(sample.pi_correct for sample in route_samples)
        breakdown[route_class] = {
            "sample_count": len(route_samples),
            "zoe_accuracy": zoe_accuracy,
            "pi_accuracy": pi_accuracy,
            "accuracy_delta": pi_accuracy - zoe_accuracy,
            "zoe_p95_latency_ms": zoe_p95,
            "pi_p95_latency_ms": pi_p95,
            "latency_delta_ms": None if zoe_p95 is None or pi_p95 is None else zoe_p95 - pi_p95,
            "timeout_rate": _rate(sample.timed_out for sample in route_samples),
            "correction_rate": _rate(sample.user_corrected for sample in route_samples),
        }
    return breakdown


def build_pi_transport_breakdown(samples: Sequence[PiRouteSample]) -> dict[str, dict[str, Any]]:
    """Summarize Pi print and RPC evidence separately.

    Empty buckets retain the existing promotion-report convention: rates and
    accuracy fields are 0.0, while sample_count=0 is the no-evidence signal.
    """
    breakdown: dict[str, dict[str, Any]] = {}
    for transport in sorted(PI_TRANSPORTS):
        transport_samples = [sample for sample in samples if sample.pi_transport == transport]
        for sample in transport_samples:
            sample.validate()
        zoe_p95 = _percentile([sample.zoe_latency_ms for sample in transport_samples], 95)
        pi_p95 = _percentile([sample.pi_latency_ms for sample in transport_samples], 95)
        zoe_accuracy = _rate(sample.zoe_correct for sample in transport_samples)
        pi_accuracy = _rate(sample.pi_correct for sample in transport_samples)
        breakdown[transport] = {
            "sample_count": len(transport_samples),
            "zoe_accuracy": zoe_accuracy,
            "pi_accuracy": pi_accuracy,
            "accuracy_delta": pi_accuracy - zoe_accuracy,
            "zoe_p95_latency_ms": zoe_p95,
            "pi_p95_latency_ms": pi_p95,
            "latency_delta_ms": None if zoe_p95 is None or pi_p95 is None else zoe_p95 - pi_p95,
            "timeout_rate": _rate(sample.timed_out for sample in transport_samples),
            "correction_rate": _rate(sample.user_corrected for sample in transport_samples),
        }
    return breakdown


def build_pi_source_breakdown(
    samples: Sequence[PiRouteSample],
    *,
    policy: PiPromotionPolicy | None = None,
) -> dict[str, Any]:
    """Summarize where promotion samples came from.

    This is reporting-only evidence. Promotion decisions still use the explicit
    speed/accuracy gates, while operators can see whether the sample set is
    synthetic-heavy or backed by runtime/log-derived labels.
    """
    active_policy = policy or PiPromotionPolicy()
    source_counts: dict[str, int] = {}
    source_counts_by_group: dict[str, dict[str, int]] = {
        group: {} for group in sorted(LOW_RISK_PI_INTENT_GROUPS)
    }
    real_counts_by_group = {group: 0 for group in sorted(LOW_RISK_PI_INTENT_GROUPS)}
    real_sample_count = 0
    for sample in samples:
        sample.validate()
        source = _sample_source(sample)
        source_counts[source] = source_counts.get(source, 0) + 1
        group_counts = source_counts_by_group.setdefault(sample.intent_group, {})
        group_counts[source] = group_counts.get(source, 0) + 1
        if source in REAL_PROMOTION_EVIDENCE_SOURCES:
            real_sample_count += 1
            real_counts_by_group[sample.intent_group] = real_counts_by_group.get(sample.intent_group, 0) + 1
    real_deficits = {
        group: max(0, active_policy.min_samples - real_counts_by_group.get(group, 0))
        for group in sorted(LOW_RISK_PI_INTENT_GROUPS)
    }
    return {
        "sample_count": len(samples),
        "source_counts": dict(sorted(source_counts.items())),
        "source_counts_by_group": {
            group: dict(sorted(counts.items())) for group, counts in sorted(source_counts_by_group.items())
        },
        "real_source_sample_count": real_sample_count,
        "synthetic_sample_count": source_counts.get("synthetic", 0),
        "unknown_source_sample_count": source_counts.get("unknown", 0),
        "real_source_sample_count_by_group": real_counts_by_group,
        "real_source_sample_deficit_by_group": real_deficits,
        "real_source_ready_groups": [group for group in sorted(real_deficits) if real_deficits[group] == 0],
    }


def build_pi_failure_examples(samples: Sequence[PiRouteSample], *, limit: int = 5) -> list[dict[str, Any]]:
    examples: list[tuple[tuple[int, float], dict[str, Any]]] = []
    for sample in samples:
        sample.validate()
        reasons = _failure_reasons(sample)
        if not reasons:
            continue
        severity = 0
        if sample.rollback_blocked:
            severity = max(severity, 4)
        if sample.user_corrected:
            severity = max(severity, 3)
        if sample.timed_out:
            severity = max(severity, 2)
        if "pi_wrong_intent" in reasons:
            severity = max(severity, 1)
        source = _optional_str(sample.metadata.get("source")) if isinstance(sample.metadata, Mapping) else None
        examples.append(
            (
                (severity, sample.pi_latency_ms),
                {
                    "case_id": sample.case_id,
                    "intent_group": sample.intent_group,
                    "expected_intent": sample.expected_intent,
                    "zoe_intent": sample.zoe_intent,
                    "pi_intent": sample.pi_intent,
                    "route_class": sample.route_class,
                    "pi_transport": sample.pi_transport,
                    "reasons": reasons,
                    "timed_out": sample.timed_out,
                    "user_corrected": sample.user_corrected,
                    "rollback_blocked": sample.rollback_blocked,
                    "zoe_latency_ms": sample.zoe_latency_ms,
                    "pi_latency_ms": sample.pi_latency_ms,
                    "source": source,
                },
            )
        )
    ranked = sorted(examples, key=lambda item: (-item[0][0], -item[0][1], item[1]["case_id"]))
    return [example for _, example in ranked[: max(0, limit)]]


def build_pi_promotion_actions(
    *,
    current_promoted_groups: Sequence[str],
    promotable_groups: Sequence[str],
    rollback_groups: Sequence[str],
) -> dict[str, Any]:
    current = set(current_promoted_groups)
    promotable = set(promotable_groups)
    rollback = set(rollback_groups)
    unknown = sorted((current | promotable | rollback) - set(LOW_RISK_PI_INTENT_GROUPS))
    if unknown:
        raise ValueError(f"unknown promotion action groups: {', '.join(unknown)}")
    overlap = sorted(promotable & rollback)
    if overlap:
        raise ValueError(f"conflicting promotion action groups: {', '.join(overlap)}")
    next_promoted = sorted((current | promotable) - rollback)
    promote = sorted(promotable - current)
    remove = sorted(rollback & current)
    keep = sorted(current - set(remove))
    return {
        "promote_groups": promote,
        "rollback_groups": remove,
        "keep_promoted_groups": keep,
        "next_promoted_groups": next_promoted,
        "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": ",".join(next_promoted)},
        "requires_operator_apply": bool(promote or remove),
    }


def load_pi_intent_eval_cases(path: str | Path) -> list[PiIntentEvalCase]:
    target = Path(path)
    raw = target.read_text(encoding="utf-8")
    if target.suffix == ".jsonl":
        cases: list[PiIntentEvalCase] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{target}:{line_number}: invalid JSONL row: {exc.msg}") from exc
            if not isinstance(payload, Mapping):
                raise ValueError(f"{target}:{line_number}: eval case row must be an object")
            cases.append(PiIntentEvalCase.from_mapping(payload, source_path=f"{target}:{line_number}"))
        return _dedupe_eval_cases(cases)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{target}: invalid JSON: {exc.msg}") from exc
    if isinstance(payload, Mapping):
        rows = payload.get("cases")
    else:
        rows = payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError(f"{target}: eval cases must be a JSON array or object with a cases array")
    cases: list[PiIntentEvalCase] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ValueError(f"{target}:{index}: eval case must be an object")
        cases.append(PiIntentEvalCase.from_mapping(row, source_path=f"{target}:{index}"))
    return _dedupe_eval_cases(cases)


def merge_pi_intent_eval_cases(*case_groups: Sequence[PiIntentEvalCase]) -> list[PiIntentEvalCase]:
    merged: list[PiIntentEvalCase] = []
    for cases in case_groups:
        merged.extend(cases)
    return _dedupe_eval_cases(merged)

def summarize_eval_case_sources(cases: Sequence[PiIntentEvalCase]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        case.validate()
        counts[case.source] = counts.get(case.source, 0) + 1
    return dict(sorted(counts.items()))

def eval_cases_to_dict(cases: Sequence[PiIntentEvalCase] = DEFAULT_PI_INTENT_EVAL_CASES) -> list[dict[str, Any]]:
    return [case.to_dict() for case in cases]


def _sample_source(sample: PiRouteSample) -> str:
    if isinstance(sample.metadata, Mapping):
        source = _optional_str(sample.metadata.get("source"))
        if source:
            return source
    return "unknown"


def _failure_reasons(sample: PiRouteSample) -> list[str]:
    reasons: list[str] = []
    if sample.rollback_blocked:
        reasons.append("rollback_blocked")
    if sample.user_corrected:
        reasons.append("user_corrected")
    if sample.timed_out:
        reasons.append("timed_out")
    if sample.pi_intent != sample.expected_intent:
        reasons.append("pi_wrong_intent")
    return reasons


def _dedupe_eval_cases(cases: Sequence[PiIntentEvalCase]) -> list[PiIntentEvalCase]:
    seen: set[str] = set()
    output: list[PiIntentEvalCase] = []
    for case in cases:
        case.validate()
        if case.case_id in seen:
            raise ValueError(f"duplicate eval case_id: {case.case_id}")
        seen.add(case.case_id)
        output.append(case)
    return output


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _empty_decision(intent_group: str, *, state: str, blockers: tuple[str, ...]) -> PiPromotionDecision:
    return PiPromotionDecision(
        intent_group=intent_group,
        state=state,
        blockers=blockers,
        sample_count=0,
        zoe_accuracy=0.0,
        pi_accuracy=0.0,
        accuracy_delta=0.0,
        zoe_p95_latency_ms=None,
        pi_p95_latency_ms=None,
        latency_delta_ms=None,
        timeout_rate=0.0,
        correction_rate=0.0,
    )


def _rate(values) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(1 for item in items if item) / len(items)


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    clean = sorted(float(value) for value in values)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(clean) - 1)
    weight = rank - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def _reject_secret_keys(value: Mapping[str, Any], *, sample_id: str, path: str = "") -> None:
    for key, nested in value.items():
        full_key = f"{path}.{key}" if path else str(key)
        if any(marker in str(key).lower() for marker in ("api_key", "token", "password", "secret", "authorization")):
            raise ValueError(f"{sample_id}: metadata may not contain secret field {full_key}")
        if isinstance(nested, Mapping):
            _reject_secret_keys(nested, sample_id=sample_id, path=full_key)


__all__ = [
    "DEFAULT_PI_INTENT_EVAL_CASES",
    "LOW_RISK_PI_INTENT_GROUPS",
    "PRIVILEGED_INTENTS",
    "PI_TRANSPORTS",
    "PiIntentEvalCase",
    "PiPromotionDecision",
    "PiPromotionPolicy",
    "PiRouteSample",
    "build_pi_failure_examples",
    "build_pi_route_class_breakdown",
    "build_pi_source_breakdown",
    "build_pi_transport_breakdown",
    "build_pi_promotion_actions",
    "eval_cases_to_dict",
    "evaluate_pi_promotion",
    "intent_group_for_intent",
    "load_pi_intent_eval_cases",
    "merge_pi_intent_eval_cases",
    "summarize_eval_case_sources",
    "summarize_pi_promotion",
]
