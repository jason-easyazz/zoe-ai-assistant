"""Runtime feature-flag wrapper for Zoe memory routing.

The deterministic router can be inspected safely before it participates in
chat prompt construction or memory writes. This module keeps that runtime
contract explicit: disabled by default, observe-only when enabled, and never a
writer.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from zoe_memory_router import route_memory_query


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
) -> dict[str, Any]:
    enabled = memory_router_runtime_enabled(env)
    if not enabled:
        return {
            "enabled": False,
            "mode": "disabled",
            "route": None,
            "can_inject_prompt": False,
            "can_write_memory": False,
            "reason": "runtime flag disabled",
        }
    route = route_memory_query(query, purpose=purpose)
    return {
        "enabled": True,
        "mode": "observe_only",
        "route": route.to_dict(),
        "can_inject_prompt": False,
        "can_write_memory": False,
        "reason": "runtime flag enabled for observation only",
    }


def memory_router_runtime_status(
    *,
    env: dict[str, str] | None = None,
    include_samples: bool | None = None,
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
                    "decision": route_memory_for_runtime(query, env=env),
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
    "memory_router_runtime_enabled",
    "memory_router_runtime_status",
    "route_memory_for_runtime",
]
