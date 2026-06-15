"""Governed, non-persistent observation trace collection.

This collector is an admission boundary for trace packets before Zoe gets any
durable trace store. It validates trace shape, rejects unsafe batches, and
returns summaries without writing to a database or memory backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from zoe_observation_trace import ObservationTrace, summarize_observation_traces


@dataclass(frozen=True)
class ObservationTraceCollectorPolicy:
    max_batch_size: int = 50
    allow_persistence: bool = False
    require_single_user_batch: bool = True
    require_single_scope_batch: bool = False
    allowed_surfaces: tuple[str, ...] = ()
    allowed_trace_types: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")
        if self.allow_persistence:
            raise ValueError("observation trace persistence is not enabled")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "max_batch_size": self.max_batch_size,
            "allow_persistence": self.allow_persistence,
            "require_single_user_batch": self.require_single_user_batch,
            "require_single_scope_batch": self.require_single_scope_batch,
            "allowed_surfaces": list(self.allowed_surfaces),
            "allowed_trace_types": list(self.allowed_trace_types),
        }


@dataclass(frozen=True)
class ObservationTraceCollectionResult:
    accepted: tuple[ObservationTrace, ...]
    rejected: tuple[dict[str, str], ...]
    persisted: bool
    policy: ObservationTraceCollectorPolicy

    @property
    def ok(self) -> bool:
        return not self.rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "accepted_count": len(self.accepted),
            "rejected": list(self.rejected),
            "persisted": self.persisted,
            "policy": self.policy.to_dict(),
            "summary": summarize_observation_traces(self.accepted) if self.accepted else {
                "trace_count": 0,
                "outcomes": {},
                "types": {},
                "p50_latency_ms": None,
                "p95_latency_ms": None,
            },
            "traces": [trace.to_dict() for trace in self.accepted],
        }


def collect_observation_traces(
    traces: Sequence[ObservationTrace],
    *,
    policy: ObservationTraceCollectorPolicy | None = None,
) -> ObservationTraceCollectionResult:
    """Validate and summarize trace packets without persisting them."""

    active_policy = policy or ObservationTraceCollectorPolicy()
    active_policy.validate()
    accepted: list[ObservationTrace] = []
    rejected: list[dict[str, str]] = []

    if not traces:
        rejected.append({"trace_id": "*batch*", "reason": "batch is empty"})
        return ObservationTraceCollectionResult(
            accepted=(),
            rejected=tuple(rejected),
            persisted=False,
            policy=active_policy,
        )

    if len(traces) > active_policy.max_batch_size:
        rejected.append(
            {
                "trace_id": "*batch*",
                "reason": f"batch size {len(traces)} exceeds max_batch_size {active_policy.max_batch_size}",
            }
        )
        return ObservationTraceCollectionResult(
            accepted=(),
            rejected=tuple(rejected),
            persisted=False,
            policy=active_policy,
        )

    for trace in traces:
        try:
            trace.validate()
            _validate_policy(trace, active_policy)
        except ValueError as exc:
            rejected.append({"trace_id": _trace_id(trace), "reason": str(exc)})
            continue
        accepted.append(trace)

    rejected.extend(_batch_rejections(traces, active_policy))
    if rejected:
        return ObservationTraceCollectionResult(
            accepted=(),
            rejected=tuple(rejected),
            persisted=False,
            policy=active_policy,
        )

    return ObservationTraceCollectionResult(
        accepted=tuple(accepted),
        rejected=(),
        persisted=False,
        policy=active_policy,
    )


def _validate_policy(trace: ObservationTrace, policy: ObservationTraceCollectorPolicy) -> None:
    if policy.allowed_surfaces and trace.surface not in policy.allowed_surfaces:
        raise ValueError(f"{trace.trace_id}: surface {trace.surface!r} is not allowed by collector policy")
    if policy.allowed_trace_types and trace.trace_type not in policy.allowed_trace_types:
        raise ValueError(f"{trace.trace_id}: trace_type {trace.trace_type!r} is not allowed by collector policy")


def _trace_id(trace: ObservationTrace) -> str:
    trace_id = getattr(trace, "trace_id", None)
    return trace_id or "<missing>"


def _batch_rejections(
    traces: Sequence[ObservationTrace],
    policy: ObservationTraceCollectorPolicy,
) -> list[dict[str, str]]:
    rejected: list[dict[str, str]] = []
    if policy.require_single_user_batch:
        users = {trace.user_id for trace in traces if trace.user_id}
        if len(users) > 1:
            rejected.append({"trace_id": "*batch*", "reason": "batch contains multiple user_id values"})
    if policy.require_single_scope_batch:
        scopes = {trace.scope for trace in traces}
        if len(scopes) > 1:
            rejected.append({"trace_id": "*batch*", "reason": "batch contains multiple scopes"})
    return rejected


__all__ = [
    "ObservationTraceCollectionResult",
    "ObservationTraceCollectorPolicy",
    "collect_observation_traces",
]
