"""Measure cached memory prompt packet compilation from MemoryService reads.

This module is intentionally read/measure oriented. It can ingest synthetic rows
through MemoryService for a benchmark run, loads them back through the existing
prompt-cache read path, and feeds those caller-supplied rows into the disabled-
by-default prompt packet preview compiler. It does not authorize prompt
injection or durable writes outside the explicit synthetic setup step.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Protocol, Sequence

from mempalace_baseline import BASELINE_CASES, MemPalaceBaselineCase, percentile
from zoe_memory_router_runtime import PROMPT_PACKET_PREVIEW_FLAG, _elapsed_ms, compile_cached_memory_prompt_packet


@dataclass(frozen=True)
class PromptPacketMeasureCase:
    case_id: str
    query: str
    scope: str = "project"
    user_id: str = "zoe-prompt-packet-measure"


class PromptPacketMemoryService(Protocol):
    async def ingest(self, text: str, **kwargs: Any) -> Any: ...

    async def load_for_prompt(self, user_id: str, *, limit: int = 20) -> Sequence[Any]: ...


DEFAULT_MEASURE_USER_ID = "zoe-prompt-packet-measure"
SYNTHETIC_MEASURE_USER_PREFIX = "zoe-prompt-packet-measure"


MEASURE_CASES: tuple[PromptPacketMeasureCase, ...] = (
    PromptPacketMeasureCase(
        case_id="experience_packet",
        query="What fix worked for the recurring weather response failure?",
    ),
    PromptPacketMeasureCase(
        case_id="governance_packet",
        query="Which governance layer gates Zoe self-evolution memory?",
    ),
    PromptPacketMeasureCase(
        case_id="tool_packet",
        query="Which lane should Zoe prefer for planning and Greptile repair loops?",
    ),
)


def memory_ref_to_cached_item(ref: Any, *, default_scope: str = "project") -> dict[str, Any]:
    metadata = dict(getattr(ref, "metadata", {}) or {})
    evidence_refs = _metadata_sequence(metadata.get("evidence_refs"))
    return {
        "event_id": metadata.get("event_id") or getattr(ref, "id", ""),
        "content": getattr(ref, "text", ""),
        "scope": metadata.get("scope") or default_scope,
        "user_id": metadata.get("user_id") or metadata.get("wing"),
        "status": metadata.get("status") or "active",
        "confidence": metadata.get("confidence"),
        "source": metadata.get("source") or "MemoryService.load_for_prompt",
        "evidence_refs": evidence_refs,
    }


def memory_refs_to_cached_items(refs: Sequence[Any], *, default_scope: str = "project") -> list[dict[str, Any]]:
    return [memory_ref_to_cached_item(ref, default_scope=default_scope) for ref in refs]


def _metadata_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return (text,)
        return _metadata_sequence(loaded)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


async def seed_prompt_packet_measure_memories(
    service: PromptPacketMemoryService,
    *,
    user_id: str = DEFAULT_MEASURE_USER_ID,
    cases: Sequence[MemPalaceBaselineCase] = BASELINE_CASES,
) -> int:
    count = 0
    for case in cases:
        await service.ingest(
            case.text,
            user_id=user_id,
            source="prompt_packet_measure",
            user_turn_id=f"prompt-packet-measure:{case.case_id}",
            memory_type=case.memory_type,
            confidence=0.95,
            status="approved",
            tags=["prompt_packet_measure", case.case_id],
            scope="project",
            metadata={
                "event_id": f"prompt_packet_measure_{case.case_id}",
                "scope": "project",
                "evidence_refs": [f"fixture:mempalace_baseline:{case.case_id}"],
            },
        )
        count += 1
    return count


async def measure_cached_prompt_packets(
    service: PromptPacketMemoryService,
    *,
    user_id: str = DEFAULT_MEASURE_USER_ID,
    cases: Sequence[PromptPacketMeasureCase] = MEASURE_CASES,
    prompt_limit: int = 20,
    max_items: int = 3,
    max_chars: int = 480,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        read_started = time.perf_counter()
        refs = await service.load_for_prompt(user_id, limit=prompt_limit)
        read_latency_ms = _elapsed_ms(read_started)
        cached_items = memory_refs_to_cached_items(refs, default_scope=case.scope)
        compile_started = time.perf_counter()
        packet = compile_cached_memory_prompt_packet(
            case.query,
            cached_items,
            env={PROMPT_PACKET_PREVIEW_FLAG: "true"},
            user_id=user_id,
            scope=case.scope,
            max_items=max_items,
            max_chars=max_chars,
        )
        compile_latency_ms = _elapsed_ms(compile_started)
        line_count = len((packet.get("packet") or {}).get("lines") or [])
        evidence_count = len((packet.get("packet") or {}).get("evidence_refs") or [])
        results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "read_latency_ms": read_latency_ms,
                "compile_latency_ms": compile_latency_ms,
                "total_latency_ms": read_latency_ms + compile_latency_ms,
                "loaded_count": len(refs),
                "candidate_count": packet.get("candidate_count", len(cached_items)),
                "accepted_count": packet.get("accepted_count", 0),
                "rejected_count": len(packet.get("rejected") or []),
                "line_count": line_count,
                "evidence_count": evidence_count,
                "can_inject_prompt": packet.get("can_inject_prompt"),
                "can_write_memory": packet.get("can_write_memory"),
            }
        )
    return summarize_prompt_packet_measurements(results)


def summarize_prompt_packet_measurements(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    compile_latencies = [float(item.get("compile_latency_ms") or 0.0) for item in results]
    read_latencies = [float(item.get("read_latency_ms") or 0.0) for item in results]
    total_latencies = [float(item.get("total_latency_ms") or 0.0) for item in results]
    accepted_counts = [int(item.get("accepted_count") or 0) for item in results]
    return {
        "case_count": len(results),
        "all_packet_permissions_disabled": all(
            item.get("can_inject_prompt") is False and item.get("can_write_memory") is False
            for item in results
        ),
        "all_packets_cited": all(int(item.get("evidence_count") or 0) > 0 for item in results),
        "all_packets_safe": all(
            item.get("can_inject_prompt") is False
            and item.get("can_write_memory") is False
            and int(item.get("accepted_count") or 0) > 0
            and int(item.get("evidence_count") or 0) > 0
            and int(item.get("rejected_count") or 0) == 0
            for item in results
        ),
        "min_accepted_count": min(accepted_counts) if accepted_counts else 0,
        "p50_read_latency_ms": median(read_latencies) if read_latencies else 0.0,
        "p95_read_latency_ms": percentile(read_latencies, 0.95),
        "p50_compile_latency_ms": median(compile_latencies) if compile_latencies else 0.0,
        "p95_compile_latency_ms": percentile(compile_latencies, 0.95),
        "p50_total_latency_ms": median(total_latencies) if total_latencies else 0.0,
        "p95_total_latency_ms": percentile(total_latencies, 0.95),
        "cached_packet_budget_ms": 50.0,
        "cached_packet_compile_within_budget": percentile(compile_latencies, 0.95) <= 50.0 if compile_latencies else False,
        "results": [dict(item) for item in results],
    }


def synthetic_measure_user_id_allowed(user_id: str) -> bool:
    return user_id == DEFAULT_MEASURE_USER_ID or user_id.startswith(f"{SYNTHETIC_MEASURE_USER_PREFIX}-")


def require_synthetic_measure_user_id(user_id: str) -> str:
    normalized = user_id.strip()
    if not normalized:
        raise ValueError("user_id must not be blank")
    if not synthetic_measure_user_id_allowed(normalized):
        raise ValueError(
            "--seed-synthetic may only use the synthetic prompt-packet measure user "
            f"{DEFAULT_MEASURE_USER_ID!r} or an id prefixed with {SYNTHETIC_MEASURE_USER_PREFIX!r}"
        )
    return normalized


__all__ = [
    "DEFAULT_MEASURE_USER_ID",
    "MEASURE_CASES",
    "SYNTHETIC_MEASURE_USER_PREFIX",
    "PromptPacketMeasureCase",
    "memory_ref_to_cached_item",
    "memory_refs_to_cached_items",
    "measure_cached_prompt_packets",
    "require_synthetic_measure_user_id",
    "seed_prompt_packet_measure_memories",
    "synthetic_measure_user_id_allowed",
    "summarize_prompt_packet_measurements",
]
