"""Speed/accuracy gates for promoting Pi intent routing.

This module is intentionally side-effect free. Runtime shadow collection and
promotion writers can call these helpers, but the first contract is the judge:
Pi only earns a route when it is more accurate and faster than Zoe's current
comparable path for a low-risk intent group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

EVAL_SOURCES = {"synthetic", "intent_miss", "chat_log", "voice_log", "known_failure"}
ROUTE_CLASSES = {"deterministic", "fallback", "extraction_failed"}
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
        if self.pi_transport not in {"print", "rpc"}:
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
    if any(sample.rollback_blocked for sample in group_samples):
        blockers.append("rollback_blocked")

    state = "promote" if not blockers else "keep_shadow"
    if promoted and {"accuracy_delta_below_threshold", "timeout_rate_too_high", "correction_rate_too_high", "latency_not_faster_than_zoe"}.intersection(blockers):
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


def summarize_pi_promotion(samples: Sequence[PiRouteSample], *, policy: PiPromotionPolicy | None = None) -> dict[str, Any]:
    active_policy = policy or PiPromotionPolicy()
    decisions = [evaluate_pi_promotion(samples, intent_group=group, policy=active_policy).to_dict() for group in sorted(LOW_RISK_PI_INTENT_GROUPS)]
    return {
        "policy": {
            "min_samples": active_policy.min_samples,
            "accuracy_win_margin": active_policy.accuracy_win_margin,
            "max_timeout_rate": active_policy.max_timeout_rate,
            "max_correction_rate": active_policy.max_correction_rate,
            "require_latency_win": active_policy.require_latency_win,
        },
        "sample_count": len(samples),
        "decisions": decisions,
        "promotable_groups": [decision["intent_group"] for decision in decisions if decision["state"] == "promote"],
        "rollback_groups": [decision["intent_group"] for decision in decisions if decision["state"] == "rollback"],
    }


def eval_cases_to_dict(cases: Sequence[PiIntentEvalCase] = DEFAULT_PI_INTENT_EVAL_CASES) -> list[dict[str, Any]]:
    return [case.to_dict() for case in cases]


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
    "PiIntentEvalCase",
    "PiPromotionDecision",
    "PiPromotionPolicy",
    "PiRouteSample",
    "eval_cases_to_dict",
    "evaluate_pi_promotion",
    "intent_group_for_intent",
    "summarize_pi_promotion",
]
