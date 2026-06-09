"""Repeatable MemPalace baseline fixtures and scoring helpers.

The baseline is intentionally backend-light: tests can run with a fake service,
while the maintenance runner can use Zoe's real `MemoryService` on the Jetson.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Protocol, Sequence


@dataclass(frozen=True)
class MemPalaceBaselineCase:
    case_id: str
    text: str
    query: str
    expected_terms: tuple[str, ...]
    memory_type: str
    user_id: str = "zoe-mempalace-baseline"


class MemPalaceBaselineService(Protocol):
    async def ingest(self, text: str, **kwargs: Any) -> Any: ...

    async def search(self, query: str, **kwargs: Any) -> Sequence[Any]: ...


BASELINE_CASES: tuple[MemPalaceBaselineCase, ...] = (
    MemPalaceBaselineCase(
        case_id="weather_failure_fix",
        text="The mobile weather card failed because duplicate voice queue dispatches emitted two weather responses; the voice queue guard fixed it.",
        query="What failed last time Zoe used the weather card and what fixed it?",
        expected_terms=("weather", "duplicate", "voice", "guard"),
        memory_type="experience",
    ),
    MemPalaceBaselineCase(
        case_id="memory_preference",
        text="Jason prefers MemPalace to remain Zoe's offline baseline memory until another offline system beats it on Zoe-specific latency and recall tests.",
        query="What does Jason prefer as Zoe's offline baseline memory?",
        expected_terms=("mempalace", "offline", "baseline"),
        memory_type="preference",
    ),
    MemPalaceBaselineCase(
        case_id="governance_approval",
        text="Zoe self-evolution memory must pass through Multica evidence gates before it can become trusted durable truth.",
        query="Which governance layer gates Zoe self-evolution memory?",
        expected_terms=("multica", "evidence", "trusted"),
        memory_type="approval",
    ),
    MemPalaceBaselineCase(
        case_id="tool_capability",
        text="Hermes is Zoe's preferred escalation lane for planning, architecture analysis, implementation repair, and Greptile review loops.",
        query="Which lane should Zoe prefer for planning and Greptile repair loops?",
        expected_terms=("hermes", "planning", "greptile"),
        memory_type="capability",
    ),
)


def recall_text_from_results(results: Sequence[Any]) -> str:
    parts: list[str] = []
    for item in results:
        if isinstance(item, str):
            parts.append(item)
            continue
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
            continue
        if isinstance(item, dict):
            parts.append(str(item.get("text") or item.get("content") or ""))
    return "\n".join(part for part in parts if part)


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


async def run_mempalace_baseline(
    service: MemPalaceBaselineService,
    *,
    cases: Sequence[MemPalaceBaselineCase] = BASELINE_CASES,
    user_id: str = "zoe-mempalace-baseline",
    search_limit: int = 5,
    timeout_s: float = 2.0,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        await service.ingest(
            case.text,
            user_id=user_id,
            source="mempalace_baseline",
            user_turn_id=f"baseline:{case.case_id}",
            memory_type=case.memory_type,
            confidence=0.95,
            status="approved",
            tags=["mempalace_baseline", case.case_id],
        )
        started = time.perf_counter()
        hits = await service.search(
            case.query,
            user_id=user_id,
            limit=search_limit,
            timeout_s=timeout_s,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        recalled = recall_text_from_results(hits)
        score = score_recall_text(recalled, case.expected_terms)
        results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "latency_ms": latency_ms,
                "hit_count": len(hits),
                **score,
            }
        )

    latencies = [item["latency_ms"] for item in results]
    scores = [item["score"] for item in results]
    return {
        "case_count": len(results),
        "avg_score": sum(scores) / len(scores) if scores else 0.0,
        "min_score": min(scores) if scores else 0.0,
        "p50_latency_ms": median(latencies) if latencies else 0.0,
        "p95_latency_ms": percentile(latencies, 0.95),
        "results": results,
    }


__all__ = [
    "BASELINE_CASES",
    "MemPalaceBaselineCase",
    "recall_text_from_results",
    "run_mempalace_baseline",
    "score_recall_text",
    "percentile",
]
