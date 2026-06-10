"""Runtime feature-flag wrapper for Zoe memory routing.

The deterministic router can be inspected safely before it participates in
chat prompt construction or memory writes. This module keeps that runtime
contract explicit: disabled by default, observe-only when enabled, and never a
writer.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping, Sequence

from zoe_memory_router import route_memory_query
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType
from zoe_observation_trace_collector import (
    ObservationTraceCollectionResult,
    ObservationTraceCollectorPolicy,
    collect_observation_traces,
)


FEATURE_FLAG = "ZOE_MEMORY_ROUTER_RUNTIME_ENABLED"
PROMPT_PACKET_PREVIEW_FLAG = "ZOE_MEMORY_PROMPT_PACKET_PREVIEW_ENABLED"
ACTIVE_MEMORY_STATUSES = {"active", "approved", "trusted"}
UNCERTAIN_MEMORY_STATUSES = {"disputed", "uncertain"}
SUPPRESSED_MEMORY_STATUSES = {"archived"}
MEMORY_ROUTE_TRACE_COLLECTOR_POLICY = ObservationTraceCollectorPolicy(
    max_batch_size=1,
    allowed_surfaces=("memory",),
    allowed_trace_types=(ObservationTraceType.MEMORY_ROUTE.value,),
)
DEFAULT_SAMPLE_QUERIES = (
    ("default_chat", "What do I usually like for breakfast?"),
    ("experience", "What fix worked for the recurring service failure?"),
    ("relational", "Which approval superseded the old tool trust?"),
    ("self_evolution", "Create an upgrade proposal for a new capability."),
)


def memory_router_runtime_enabled(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return str(values.get(FEATURE_FLAG, "false")).strip().lower() in {"1", "true", "yes", "on"}


def memory_prompt_packet_preview_enabled(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return str(values.get(PROMPT_PACKET_PREVIEW_FLAG, "false")).strip().lower() in {"1", "true", "yes", "on"}


def compile_cached_memory_prompt_packet(
    query: str,
    cached_items: Sequence[Mapping[str, Any]],
    *,
    purpose: str = "chat",
    env: dict[str, str] | None = None,
    user_id: str | None = None,
    scope: str = "project",
    max_items: int = 3,
    max_chars: int = 480,
) -> dict[str, Any]:
    """Build a compact cited prompt-packet preview from caller-supplied cache rows.

    This function does not call memory backends and never authorizes chat prompt
    injection. It is a measurement/preview contract for cached packets only.
    """

    if max_items <= 0:
        raise ValueError("max_items must be positive")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    enabled = memory_prompt_packet_preview_enabled(env)
    route = route_memory_query(query, purpose=purpose)
    base: dict[str, Any] = {
        "enabled": enabled,
        "mode": "preview_only" if enabled else "disabled",
        "feature_flag": PROMPT_PACKET_PREVIEW_FLAG,
        "route": route.to_dict(),
        "can_inject_prompt": False,
        "can_write_memory": False,
        "source": "caller_supplied_cache",
    }
    if not enabled:
        return {**base, "packet": None, "accepted_count": 0, "candidate_count": len(cached_items), "rejected": []}

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for index, item in enumerate(cached_items):
        normalized, reason = _normalize_cached_memory_item(item, index=index, user_id=user_id, scope=scope)
        if reason:
            rejected.append({"index": str(index), "reason": reason})
            continue
        accepted.append(normalized)

    ranked = sorted(accepted, key=_prompt_packet_rank)
    selected = ranked[:max_items]
    lines = _packet_lines(selected, max_chars=max_chars)
    evidence_refs = tuple(dict.fromkeys(ref for item in selected for ref in item["evidence_refs"]))
    statuses = {item["status"]: sum(1 for selected_item in selected if selected_item["status"] == item["status"]) for item in selected}
    packet = {
        "policy": "compact_cited_cached_memory_only",
        "route_primary": route.primary.value,
        "scope": scope,
        "user_id": user_id,
        "lines": lines,
        "evidence_refs": list(evidence_refs),
        "statuses": statuses,
        "max_items": max_items,
        "max_chars": max_chars,
        "instructions": "Use as uncertain context only; explicit user corrections override memory.",
    }
    return {
        **base,
        "packet": packet,
        "accepted_count": len(selected),
        "candidate_count": len(cached_items),
        "rejected": rejected,
    }


def _normalize_cached_memory_item(
    item: Mapping[str, Any],
    *,
    index: int,
    user_id: str | None,
    scope: str,
) -> tuple[dict[str, Any], str | None]:
    content = str(item.get("content") or item.get("text") or "").strip()
    if not content:
        return {}, "content is required"
    evidence_refs = tuple(str(ref).strip() for ref in item.get("evidence_refs") or item.get("evidence") or () if str(ref).strip())
    if not evidence_refs:
        return {}, "evidence_refs are required"
    item_scope = str(item.get("scope") or "").strip()
    if not item_scope:
        return {}, "scope is required"
    item_user_id = item.get("user_id")
    if item_user_id is not None:
        item_user_id = str(item_user_id).strip() or None
    if item_scope in {"personal", "shared"} and not item_user_id:
        return {}, f"user_id is required for {item_scope} memory"
    if item_scope in {"personal", "shared"} and item_user_id and user_id is None:
        return {}, "user_id is required to include owned personal/shared memory"
    if item_user_id and user_id != item_user_id:
        return {}, "user_id mismatch"
    if not _scope_allowed(item_scope, requested_scope=scope):
        return {}, "scope mismatch"
    status = str(item.get("status") or "active").strip().lower()
    if status in SUPPRESSED_MEMORY_STATUSES:
        return {}, f"status {status!r} is suppressed"
    confidence = _confidence(item.get("confidence"))
    return {
        "id": str(item.get("event_id") or item.get("memory_id") or f"cached:{index}"),
        "content": content,
        "evidence_refs": evidence_refs,
        "scope": item_scope,
        "user_id": item_user_id,
        "status": status,
        "confidence": confidence,
        "source": str(item.get("source") or "cached"),
    }, None


def _scope_allowed(item_scope: str, *, requested_scope: str) -> bool:
    if item_scope == requested_scope:
        return True
    if item_scope == "system":
        return requested_scope in {"system", "project"}
    if item_scope == "shared":
        return requested_scope in {"shared", "personal", "project"}
    return False


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, confidence))


def _prompt_packet_rank(item: Mapping[str, Any]) -> tuple[int, float, str]:
    status = str(item["status"])
    if status in ACTIVE_MEMORY_STATUSES:
        status_rank = 0
    elif status in UNCERTAIN_MEMORY_STATUSES:
        status_rank = 1
    elif status == "superseded":
        status_rank = 2
    else:
        status_rank = 3
    return (status_rank, -float(item["confidence"]), str(item["id"]))


def _packet_lines(items: Sequence[Mapping[str, Any]], *, max_chars: int) -> list[str]:
    remaining = max_chars
    lines: list[str] = []
    for item in items:
        prefix = f"[{item['id']}] status={item['status']} confidence={item['confidence']:.2f} evidence={','.join(item['evidence_refs'])}: "
        room = max(0, remaining - len(prefix))
        if room <= 0:
            break
        content = str(item["content"])
        if len(content) > room:
            if room < 3:
                break
            content = content[: room - 3].rstrip() + "..."
        line = prefix + content
        lines.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    return lines


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
    else:
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
        trace = build_memory_route_trace(
            query,
            purpose=purpose,
            decision=decision,
            trace_id=trace_id,
            scope=scope,
            user_id=user_id,
            latency_ms=_elapsed_ms(start),
        )
        collection = collect_memory_route_trace(trace)
        decision["trace"] = trace.to_dict()
        decision["trace_collection"] = _trace_collection_summary(collection)
    return decision


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


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


def collect_memory_route_trace(trace: ObservationTrace) -> ObservationTraceCollectionResult:
    result = collect_observation_traces((trace,), policy=MEMORY_ROUTE_TRACE_COLLECTOR_POLICY)
    if not result.ok:
        reasons = "; ".join(rejection["reason"] for rejection in result.rejected)
        raise ValueError(reasons)
    return result


def _trace_collection_summary(collection: ObservationTraceCollectionResult) -> dict[str, Any]:
    payload = collection.to_dict()
    payload.pop("traces", None)
    return payload


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
        "prompt_packet_preview_enabled": memory_prompt_packet_preview_enabled(env),
        "prompt_packet_preview_flag": PROMPT_PACKET_PREVIEW_FLAG,
        "durable_writes_enabled": False,
        "sample_routes": sample_routes,
    }


__all__ = [
    "DEFAULT_SAMPLE_QUERIES",
    "FEATURE_FLAG",
    "PROMPT_PACKET_PREVIEW_FLAG",
    "MEMORY_ROUTE_TRACE_COLLECTOR_POLICY",
    "build_memory_route_trace",
    "collect_memory_route_trace",
    "compile_cached_memory_prompt_packet",
    "memory_prompt_packet_preview_enabled",
    "memory_router_runtime_enabled",
    "memory_router_runtime_status",
    "route_memory_for_runtime",
]
