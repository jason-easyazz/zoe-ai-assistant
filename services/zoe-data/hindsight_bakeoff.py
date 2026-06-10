"""Synthetic Hindsight bake-off fixtures and scoring helpers."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from zoe_memory_contract import (
    MemoryEvent,
    MemoryEventType,
    MemoryRelationship,
    MemoryScope,
    MemorySource,
    RelationshipType,
)


@dataclass(frozen=True)
class HindsightEvalQuery:
    name: str
    query: str
    expected_terms: tuple[str, ...]
    scope: str = MemoryScope.PROJECT.value
    user_id: str = "jason"
    budget: str = "mid"


SYNTHETIC_EVENTS: tuple[MemoryEvent, ...] = (
    MemoryEvent(
        event_id="mem_evt_weather_failure_001",
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FAILURE.value,
        content="The weather card failed on the mobile dashboard because the voice queue emitted duplicate weather responses.",
        entities=("weather_card", "mobile_dashboard", "voice_queue"),
        relationships=(
            MemoryRelationship(RelationshipType.FAILED_ON.value, source="weather_card", target="mobile_dashboard_render"),
            MemoryRelationship(RelationshipType.CAUSED_BY.value, source="weather_card_failure", target="duplicate_voice_queue_emit"),
        ),
        evidence_refs=("trace:weather-queue:001", "pytest:test_voice_weather_queue"),
        confidence=0.86,
    ),
    MemoryEvent(
        event_id="mem_evt_weather_fix_001",
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TEST.value,
        event_type=MemoryEventType.FIX.value,
        content="The voice queue guard fixed duplicate weather responses by allowing only one active weather card dispatch per request.",
        entities=("voice_queue_guard", "weather_card"),
        relationships=(
            MemoryRelationship(RelationshipType.FIXED_BY.value, source="duplicate_weather_response", target="voice_queue_guard"),
            MemoryRelationship(RelationshipType.MEASURED_BY.value, source="voice_queue_guard", target="pytest:test_voice_transcribe"),
        ),
        evidence_refs=("pytest:test_voice_transcribe", "commit:voice-queue-guard"),
        confidence=0.9,
    ),
    MemoryEvent(
        event_id="mem_evt_hindsight_preference_001",
        user_id="jason",
        scope=MemoryScope.PERSONAL.value,
        source=MemorySource.CHAT.value,
        event_type=MemoryEventType.PREFERENCE.value,
        content="Jason prefers Hindsight as the first Zoe memory bake-off because it fits Postgres and self-evolving experience memory.",
        entities=("hindsight", "postgres", "zoe_memory"),
        relationships=(),
        evidence_refs=("chat:2026-06-08:hindsight-first",),
        confidence=0.8,
    ),
    MemoryEvent(
        event_id="mem_evt_capability_approval_001",
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.PROPOSAL.value,
        event_type=MemoryEventType.APPROVAL.value,
        content="Jason approved using Multica evidence gates before Zoe can persist self-evolution memories as trusted truth.",
        entities=("multica", "evidence_gates", "self_evolution_memory"),
        relationships=(
            MemoryRelationship(RelationshipType.APPROVED_BY.value, source="evidence_gated_memory", target="jason"),
            MemoryRelationship(RelationshipType.TRUSTED_FOR.value, source="multica", target="self_evolution_governance"),
        ),
        evidence_refs=("chat:2026-06-08:zoe-evolution-plan", "doc:ADR-zoe-memory-layer"),
        confidence=0.88,
    ),
)

EVAL_QUERIES: tuple[HindsightEvalQuery, ...] = (
    HindsightEvalQuery(
        name="recall_weather_failure",
        query="What failed last time Zoe used the weather card?",
        expected_terms=("weather", "duplicate", "voice"),
    ),
    HindsightEvalQuery(
        name="recall_weather_fix",
        query="What fix worked for the recurring weather response failure?",
        expected_terms=("voice", "queue", "guard"),
    ),
    HindsightEvalQuery(
        name="recall_user_preference",
        query="What does Jason currently prefer for the first memory bake-off?",
        expected_terms=("hindsight", "postgres"),
        scope=MemoryScope.PERSONAL.value,
    ),
    HindsightEvalQuery(
        name="recall_governance",
        query="Which governance layer should gate Zoe self-evolution memory?",
        expected_terms=("multica", "evidence"),
    ),
)


def synthetic_retain_payloads() -> list[dict[str, Any]]:
    return [event.to_dict() for event in SYNTHETIC_EVENTS]


def score_recall_text(text: str, expected_terms: Iterable[str]) -> dict[str, Any]:
    lowered = text.lower()
    expected = tuple(term.lower() for term in expected_terms)
    matched = tuple(term for term in expected if term in lowered)
    return {
        "matched": list(matched),
        "missing": [term for term in expected if term not in matched],
        "score": 1.0 if not expected else len(matched) / len(expected),
    }


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not 0.0 <= percentile_value <= 1.0:
        raise ValueError("percentile_value must be between 0.0 and 1.0")
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_bakeoff_scores(scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    score_values = [float(item.get("score") or 0.0) for item in scores]
    latencies = [float(item.get("latency_ms") or 0.0) for item in scores if "latency_ms" in item]
    return {
        "case_count": len(scores),
        "avg_score": sum(score_values) / len(score_values) if score_values else 0.0,
        "min_score": min(score_values) if score_values else 0.0,
        "p50_latency_ms": median(latencies) if latencies else 0.0,
        "p95_latency_ms": percentile(latencies, 0.95),
    }


def summarize_recall_latency(
    scores: Sequence[Mapping[str, Any]],
    *,
    budget_ms: float = 600.0,
) -> dict[str, Any]:
    """Build the machine-readable recall latency acceptance block."""

    if budget_ms <= 0:
        raise ValueError("budget_ms must be positive")
    latencies = [
        float(item.get("latency_ms") or 0.0)
        for item in scores
        if bool(item.get("enabled")) and "latency_ms" in item
    ]
    enabled_case_count = len(latencies)
    p50_latency_ms = median(latencies) if latencies else 0.0
    p95_latency_ms = percentile(latencies, 0.95)
    measured = bool(latencies) and enabled_case_count > 0
    p95_within_budget = measured and p95_latency_ms <= budget_ms
    return {
        "measured": measured,
        "case_count": len(scores),
        "enabled_case_count": enabled_case_count,
        "budget_ms": budget_ms,
        "p50_latency_ms": p50_latency_ms,
        "p95_latency_ms": p95_latency_ms,
        "p95_within_budget": p95_within_budget,
        "hot_path_status": "eligible" if p95_within_budget else ("async_or_cached_only" if measured else "not_measured"),
    }


def recall_response_text(response: Mapping[str, Any]) -> str:
    results = response.get("results") or []
    texts = []
    for item in results:
        if isinstance(item, Mapping):
            texts.append(str(item.get("text") or item.get("content") or ""))
        else:
            texts.append(str(item))
    if response.get("text"):
        texts.append(str(response["text"]))
    return "\n".join(part for part in texts if part)


def score_recall_response(response: Mapping[str, Any], query: HindsightEvalQuery) -> dict[str, Any]:
    text = recall_response_text(response)
    score = score_recall_text(text, query.expected_terms)
    return {"name": query.name, "query": query.query, "text": text, **score}


__all__ = [
    "EVAL_QUERIES",
    "SYNTHETIC_EVENTS",
    "HindsightEvalQuery",
    "percentile",
    "recall_response_text",
    "score_recall_response",
    "score_recall_text",
    "summarize_bakeoff_scores",
    "summarize_recall_latency",
    "synthetic_retain_payloads",
]
