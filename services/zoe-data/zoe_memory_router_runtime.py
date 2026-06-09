"""Runtime feature-flag wrapper for Zoe memory routing.

The deterministic router can be inspected safely before it participates in
chat prompt construction or memory writes. This module keeps that runtime
contract explicit: disabled by default, observe-only when enabled, and never a
writer.
"""

from __future__ import annotations

import os
import time
from typing import Any, Sequence

from zoe_memory_router import route_memory_query
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType


FEATURE_FLAG = "ZOE_MEMORY_ROUTER_RUNTIME_ENABLED"
DEFAULT_SAMPLE_QUERIES = (
    ("default_chat", "What do I usually like for breakfast?"),
    ("experience", "What fix worked for the recurring service failure?"),
    ("relational", "Which approval superseded the old tool trust?"),
    ("self_evolution", "Create an upgrade proposal for a new capability."),
)


def memory_router_runtime_enabled(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return str(values.get(FEATURE_FLAG, "false")).strip().lower() in {"1", "true", "yes", "on"}


def route_memory_for_runtime(
    query: str,
    *,
    purpose: str = "chat",
    env: dict[str, str] | None = None,
    include_trace: bool = False,
    trace_id: str | None = None,
    scope: str = "system",
    user_id: str | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    enabled = memory_router_runtime_enabled(env)
    if not enabled:
        decision = {
            "enabled": False,
            "mode": "disabled",
            "route": None,
            "can_inject_prompt": False,
            "can_write_memory": False,
            "reason": "runtime flag disabled",
        }
        if include_trace:
            decision["trace"] = build_memory_route_trace(
                query,
                purpose=purpose,
                decision=decision,
                trace_id=trace_id,
                scope=scope,
                user_id=user_id,
                latency_ms=_elapsed_ms(start),
            ).to_dict()
        return decision
    route = route_memory_query(query, purpose=purpose)
    decision = {
        "enabled": True,
        "mode": "observe_only",
        "route": route.to_dict(),
        "can_inject_prompt": False,
        "can_write_memory": False,
        "reason": "runtime flag enabled for observation only",
    }
    if include_trace:
        decision["trace"] = build_memory_route_trace(
            query,
            purpose=purpose,
            decision=decision,
            trace_id=trace_id,
            scope=scope,
            user_id=user_id,
            latency_ms=_elapsed_ms(start),
        ).to_dict()
    return decision


def build_memory_route_trace(
    query: str,
    *,
    purpose: str,
    decision: dict[str, Any],
    trace_id: str | None = None,
    scope: str = "system",
    user_id: str | None = None,
    latency_ms: float | None = None,
) -> ObservationTrace:
    route = decision.get("route") or {}
    primary = route.get("primary")
    secondary = list(route.get("secondary") or ())
    enabled = bool(decision.get("enabled"))
    outcome = ObservationOutcome.SUCCESS.value if enabled and primary else ObservationOutcome.SKIPPED.value
    summary = "Memory router selected an observe-only route." if primary else "Memory router skipped routing."
    return ObservationTrace(
        trace_id=trace_id or f"trace_memory_route_{purpose}_{'enabled' if enabled else 'disabled'}",
        trace_type=ObservationTraceType.MEMORY_ROUTE.value,
        surface="memory",
        scope=scope,
        user_id=user_id,
        outcome=outcome,
        summary=summary,
        evidence_refs=(),
        latency_ms=latency_ms,
        confidence=1.0,
        metadata={
            "purpose": purpose,
            "query_length": len(query),
            "enabled": enabled,
            "mode": decision.get("mode"),
            "primary": primary,
            "secondary": secondary,
            "latency_budget_ms": route.get("latency_budget_ms"),
            "can_inject_prompt": bool(decision.get("can_inject_prompt")),
            "can_write_memory": bool(decision.get("can_write_memory")),
            "reason": decision.get("reason"),
            "route_reason": route.get("reason"),
        },
    )


def memory_router_runtime_status(
    *,
    env: dict[str, str] | None = None,
    include_samples: bool | None = None,
    include_traces: bool = False,
    samples: Sequence[tuple[str, str]] = DEFAULT_SAMPLE_QUERIES,
) -> dict[str, Any]:
    enabled = memory_router_runtime_enabled(env)
    sample_routes = []
    if include_samples is None:
        include_samples = enabled
    if include_samples:
        for sample_id, query in samples:
            sample_routes.append(
                {
                    "id": sample_id,
                    "query": query,
                    "decision": route_memory_for_runtime(
                        query,
                        env=env,
                        include_trace=include_traces,
                        trace_id=f"trace_memory_route_sample_{sample_id}",
                    ),
                }
            )
    return {
        "ok": True,
        "surface": "zoe_memory_router",
        "feature_flag": FEATURE_FLAG,
        "enabled": enabled,
        "mode": "observe_only" if enabled else "disabled",
        "default_enabled": False,
        "chat_hot_path_enabled": False,
        "prompt_injection_enabled": False,
        "durable_writes_enabled": False,
        "sample_routes": sample_routes,
    }


__all__ = [
    "DEFAULT_SAMPLE_QUERIES",
    "FEATURE_FLAG",
    "build_memory_route_trace",
    "memory_router_runtime_enabled",
    "memory_router_runtime_status",
    "route_memory_for_runtime",
]


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)
