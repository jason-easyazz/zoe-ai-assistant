"""Observation and evaluation traces for Zoe memory and self-evolution.

These records are deliberately lightweight and inert: they validate the shape
of evidence Zoe should capture around recall, retain candidates, admission,
fallbacks, contradictions, proposals, verification, outcome evals, and hardware
fit. Persistence and runtime wiring can come later through a small PR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from zoe_evolution_proposal import EvolutionSignal, EvolutionSignalType


class ObservationTraceType(str, Enum):
    MEMORY_ROUTE = "memory_route"
    RECALL = "recall"
    RETAIN_CANDIDATE = "retain_candidate"
    ADMISSION = "admission"
    CONTRADICTION = "contradiction"
    FALLBACK = "fallback"
    PROPOSAL = "proposal"
    VERIFICATION = "verification"
    OUTCOME_EVAL = "outcome_eval"
    HARDWARE_BUDGET = "hardware_budget"


class ObservationOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


TRACE_SCOPES = {"personal", "shared", "ambient", "system", "project"}
TRACE_SURFACES = {
    "chat",
    "voice",
    "memory",
    "hindsight",
    "graphiti",
    "mempalace",
    "graphify",
    "multica",
    "hermes",
    "openclaw",
    "pi",
    "ui",
    "local_service",
}

_SECRET_MARKERS = ("api_key", "token", "password", "secret", "bearer", "credential", "auth_token", "authorization")
_EVIDENCE_REQUIRED_TYPES = {
    ObservationTraceType.RETAIN_CANDIDATE.value,
    ObservationTraceType.ADMISSION.value,
    ObservationTraceType.CONTRADICTION.value,
    ObservationTraceType.PROPOSAL.value,
    ObservationTraceType.VERIFICATION.value,
    ObservationTraceType.OUTCOME_EVAL.value,
}


@dataclass(frozen=True)
class ObservationTrace:
    trace_id: str
    trace_type: str
    surface: str
    scope: str
    outcome: str
    summary: str
    evidence_refs: tuple[str, ...]
    user_id: str | None = None
    subject_id: str | None = None
    related_ids: tuple[str, ...] = ()
    latency_ms: float | None = None
    helpfulness: float | None = None
    confidence: float | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def validate(self) -> None:
        if not self.trace_id:
            raise ValueError("trace_id is required")
        if self.trace_type not in {item.value for item in ObservationTraceType}:
            raise ValueError(f"{self.trace_id}: unknown trace_type {self.trace_type!r}")
        if self.surface not in TRACE_SURFACES:
            raise ValueError(f"{self.trace_id}: unknown surface {self.surface!r}")
        if self.scope not in TRACE_SCOPES:
            raise ValueError(f"{self.trace_id}: unknown scope {self.scope!r}")
        if self.outcome not in {item.value for item in ObservationOutcome}:
            raise ValueError(f"{self.trace_id}: unknown outcome {self.outcome!r}")
        if not self.summary:
            raise ValueError(f"{self.trace_id}: summary is required")
        if self.scope in {"personal", "shared"} and not self.user_id:
            raise ValueError(f"{self.trace_id}: user_id is required for {self.scope} traces")
        if self.trace_type in _EVIDENCE_REQUIRED_TYPES and not self.evidence_refs:
            raise ValueError(f"{self.trace_id}: evidence_refs are required for {self.trace_type}")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError(f"{self.trace_id}: latency_ms must be non-negative")
        for field_name in ("helpfulness", "confidence"):
            value = getattr(self, field_name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{self.trace_id}: {field_name} must be between 0 and 1")
        for name, value in self.metrics.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{self.trace_id}: metric names must be non-empty strings")
            if not isinstance(value, (int, float)):
                raise ValueError(f"{self.trace_id}: metric {name!r} must be numeric")
        _reject_secret_keys(self.metadata, trace_id=self.trace_id)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "trace_id": self.trace_id,
            "trace_type": self.trace_type,
            "surface": self.surface,
            "scope": self.scope,
            "outcome": self.outcome,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "user_id": self.user_id,
            "subject_id": self.subject_id,
            "related_ids": list(self.related_ids),
            "latency_ms": self.latency_ms,
            "helpfulness": self.helpfulness,
            "confidence": self.confidence,
            "metrics": dict(self.metrics),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


def _reject_secret_keys(value: Mapping[str, Any], *, trace_id: str, path: str = "") -> None:
    leaked = _secret_key_paths(value, path=path)
    if leaked:
        raise ValueError(f"{trace_id}: metadata may not contain secret fields: {', '.join(sorted(set(leaked)))}")


def _secret_key_paths(value: Any, *, path: str = "") -> list[str]:
    leaked: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            full_key = f"{path}.{key}" if path else str(key)
            if any(marker in str(key).lower() for marker in _SECRET_MARKERS):
                leaked.append(full_key)
            leaked.extend(_secret_key_paths(nested, path=full_key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            full_key = f"{path}[{index}]" if path else f"[{index}]"
            leaked.extend(_secret_key_paths(item, path=full_key))
    return leaked


def summarize_observation_traces(traces: Sequence[ObservationTrace]) -> dict[str, Any]:
    for trace in traces:
        trace.validate()
    latencies = sorted(trace.latency_ms for trace in traces if trace.latency_ms is not None)
    outcomes: dict[str, int] = {}
    types: dict[str, int] = {}
    for trace in traces:
        outcomes[trace.outcome] = outcomes.get(trace.outcome, 0) + 1
        types[trace.trace_type] = types.get(trace.trace_type, 0) + 1
    return {
        "trace_count": len(traces),
        "outcomes": outcomes,
        "types": types,
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
    }


def failed_outcome_trace_to_signal(
    trace: ObservationTrace,
    *,
    repeat_count: int,
    idempotency_key: str | None = None,
    existing_signal_ids: set[str] | None = None,
) -> EvolutionSignal | None:
    """Create a Notice signal from recurring failed outcome traces."""

    trace.validate()
    if trace.trace_type != ObservationTraceType.OUTCOME_EVAL.value:
        return None
    if trace.outcome not in {ObservationOutcome.FAILED.value, ObservationOutcome.BLOCKED.value}:
        return None
    if repeat_count < 2:
        return None
    signal_id = f"signal_{idempotency_key or trace.trace_id}_{repeat_count}"
    if existing_signal_ids is not None and signal_id in existing_signal_ids:
        return None
    return EvolutionSignal(
        signal_id=signal_id,
        signal_type=EvolutionSignalType.OUTCOME_EVAL_FAILURE.value,
        summary=f"{trace.summary} Repeated {repeat_count} times.",
        source=f"observation_trace:{trace.surface}",
        evidence_refs=trace.evidence_refs,
        user_id=trace.user_id,
        scope=trace.scope,
        metadata={
            "trace_id": trace.trace_id,
            "trace_type": trace.trace_type,
            "outcome": trace.outcome,
            "repeat_count": repeat_count,
            "subject_id": trace.subject_id,
        },
    )


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    rank = (len(values) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return float(values[lower] * (1 - weight) + values[upper] * weight)


__all__ = [
    "ObservationOutcome",
    "ObservationTrace",
    "ObservationTraceType",
    "TRACE_SCOPES",
    "TRACE_SURFACES",
    "failed_outcome_trace_to_signal",
    "summarize_observation_traces",
]
