"""Zoe Graphiti bake-off fixtures and scoring helpers.

These fixtures are backend-neutral. A later runner can feed the episodes into
Graphiti backed by FalkorDB or Neo4j, then score returned answers against the
same relationship questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class GraphitiEvalEpisode:
    episode_id: str
    name: str
    body: str
    source_description: str
    evidence_refs: tuple[str, ...]
    reference_time: str


@dataclass(frozen=True)
class GraphitiEvalQuery:
    name: str
    question: str
    expected_terms: tuple[str, ...]
    expected_relationship_path: tuple[str, ...]


GRAPHITI_EPISODES: tuple[GraphitiEvalEpisode, ...] = (
    GraphitiEvalEpisode(
        episode_id="graphiti_weather_failure_fix",
        name="Weather failure fixed by voice queue guard",
        body=(
            "Zoe weather_card FAILED_ON mobile_dashboard_render because duplicate_voice_queue_emit CAUSED_BY "
            "voice_queue. The fix duplicate_weather_response FIXED_BY voice_queue_guard was MEASURED_BY "
            "pytest:test_voice_transcribe. Evidence trace:weather-queue:001 and commit:voice-queue-guard."
        ),
        source_description="Zoe synthetic failure/fix trace",
        evidence_refs=("trace:weather-queue:001", "commit:voice-queue-guard", "pytest:test_voice_transcribe"),
        reference_time="2026-06-09T00:00:00Z",
    ),
    GraphitiEvalEpisode(
        episode_id="graphiti_memory_preference_old",
        name="Old Hindsight-first memory preference",
        body=(
            "Jason preferred Hindsight as Zoe's first memory bake-off because it fits Postgres and "
            "self-evolving experience memory. Evidence chat:2026-06-08:hindsight-first."
        ),
        source_description="Zoe synthetic old preference episode",
        evidence_refs=("chat:2026-06-08:hindsight-first",),
        reference_time="2026-06-08T00:00:00Z",
    ),
    GraphitiEvalEpisode(
        episode_id="graphiti_memory_preference_current",
        name="Current MemPalace-baseline memory preference",
        body=(
            "Jason corrected the plan: MemPalace remains Zoe's offline baseline memory until another offline "
            "system beats it on Zoe-specific latency, recall, and governance tests. The MemPalace baseline "
            "preference SUPERSEDES chat:2026-06-08:hindsight-first and is EVIDENCED_BY "
            "chat:2026-06-09:mempalace-baseline."
        ),
        source_description="Zoe synthetic current preference episode",
        evidence_refs=("chat:2026-06-09:mempalace-baseline",),
        reference_time="2026-06-09T00:00:00Z",
    ),
    GraphitiEvalEpisode(
        episode_id="graphiti_governance_approval",
        name="Self-evolution memory governance",
        body=(
            "Jason APPROVED_BY Multica evidence gates for Zoe self_evolution_memory. Multica is TRUSTED_FOR "
            "self_evolution_governance only when proposal evidence, tests, and PR evidence exist. "
            "Evidence doc:ADR-zoe-memory-layer and pr:231."
        ),
        source_description="Zoe synthetic governance approval",
        evidence_refs=("doc:ADR-zoe-memory-layer", "pr:231"),
        reference_time="2026-06-09T00:00:00Z",
    ),
    GraphitiEvalEpisode(
        episode_id="graphiti_capability_lane",
        name="Hermes trusted capability lane",
        body=(
            "Hermes USES Greptile review loops and Grep loop repair packets. Hermes is TRUSTED_FOR planning, "
            "architecture analysis, implementation repair, and review-loop execution. OpenClaw remains manual "
            "fallback, not the default planning lane. Evidence docs:zoe-tool-capability-inventory."
        ),
        source_description="Zoe synthetic capability lane",
        evidence_refs=("docs:zoe-tool-capability-inventory",),
        reference_time="2026-06-09T00:00:00Z",
    ),
    GraphitiEvalEpisode(
        episode_id="graphiti_recurring_task",
        name="Graphify recurring refresh task",
        body=(
            "Graphify BELONGS_TO_SCOPE system understanding. Graphify graph report RECURS_AS refresh_after_substantial_code_change. "
            "Stale graph reports should CAUSE Notice records before cleanup or architecture decisions. "
            "Evidence docs:zoe-harness-current-inventory and graphify-out:GRAPH_REPORT."
        ),
        source_description="Zoe synthetic recurring task",
        evidence_refs=("docs:zoe-harness-current-inventory", "graphify-out:GRAPH_REPORT"),
        reference_time="2026-06-09T00:00:00Z",
    ),
)


GRAPHITI_EVAL_QUERIES: tuple[GraphitiEvalQuery, ...] = (
    GraphitiEvalQuery(
        name="multi_hop_failure_fix",
        question="What fixed the weather card failure and what measured it?",
        expected_terms=("weather", "voice_queue_guard", "pytest"),
        expected_relationship_path=("weather_card", "FAILED_ON", "mobile_dashboard_render", "FIXED_BY", "voice_queue_guard", "MEASURED_BY"),
    ),
    GraphitiEvalQuery(
        name="superseded_memory_preference",
        question="What is Jason's current memory baseline preference and what old fact did it supersede?",
        expected_terms=("mempalace", "offline", "supersedes", "hindsight"),
        expected_relationship_path=("mempalace", "SUPERSEDES", "hindsight-first"),
    ),
    GraphitiEvalQuery(
        name="governance_trust",
        question="Which governance layer is trusted for Zoe self-evolution memory and under what evidence condition?",
        expected_terms=("multica", "trusted", "proposal", "tests"),
        expected_relationship_path=("multica", "TRUSTED_FOR", "self_evolution_governance", "EVIDENCED_BY"),
    ),
    GraphitiEvalQuery(
        name="capability_lane",
        question="Which lane should Zoe use for planning and Greptile review loops?",
        expected_terms=("hermes", "greptile", "planning"),
        expected_relationship_path=("hermes", "USES", "greptile", "TRUSTED_FOR", "planning"),
    ),
    GraphitiEvalQuery(
        name="recurring_graphify_refresh",
        question="When should Graphify refresh recur and what should stale reports cause?",
        expected_terms=("refresh", "substantial", "notice", "cleanup"),
        expected_relationship_path=("graphify", "RECURS_AS", "refresh_after_substantial_code_change", "CAUSE", "Notice"),
    ),
)


def graphiti_episode_payloads() -> list[dict[str, Any]]:
    return [
        {
            "episode_id": episode.episode_id,
            "name": episode.name,
            "episode_body": episode.body,
            "source_description": episode.source_description,
            "reference_time": episode.reference_time,
            "evidence_refs": list(episode.evidence_refs),
        }
        for episode in GRAPHITI_EPISODES
    ]


def score_answer_text(text: str, expected_terms: Iterable[str]) -> dict[str, Any]:
    lowered = text.lower()
    expected = tuple(term.lower() for term in expected_terms)
    matched = tuple(term for term in expected if term in lowered)
    return {
        "matched": list(matched),
        "missing": [term for term in expected if term not in matched],
        "score": 1.0 if not expected else len(matched) / len(expected),
    }


def score_graphiti_answer(answer: Mapping[str, Any] | str, query: GraphitiEvalQuery) -> dict[str, Any]:
    if isinstance(answer, Mapping):
        raw = answer.get("text") if answer.get("text") is not None else answer.get("answer")
        text = str(raw) if raw is not None else str(answer)
    else:
        text = str(answer)
    score = score_answer_text(text, query.expected_terms)
    return {
        "name": query.name,
        "question": query.question,
        "expected_relationship_path": list(query.expected_relationship_path),
        "text": text,
        **score,
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


def summarize_graphiti_scores(scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    score_values = [float(item.get("score") or 0.0) for item in scores]
    latencies = [float(item.get("latency_ms") or 0.0) for item in scores if "latency_ms" in item]
    return {
        "case_count": len(scores),
        "avg_score": sum(score_values) / len(score_values) if score_values else 0.0,
        "min_score": min(score_values) if score_values else 0.0,
        "p50_latency_ms": median(latencies) if latencies else 0.0,
        "p95_latency_ms": percentile(latencies, 0.95),
    }


__all__ = [
    "GRAPHITI_EPISODES",
    "GRAPHITI_EVAL_QUERIES",
    "GraphitiEvalEpisode",
    "GraphitiEvalQuery",
    "graphiti_episode_payloads",
    "percentile",
    "score_answer_text",
    "score_graphiti_answer",
    "summarize_graphiti_scores",
]
