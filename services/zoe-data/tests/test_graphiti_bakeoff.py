import pytest

from graphiti_bakeoff import (
    GRAPHITI_EPISODES,
    GRAPHITI_EVAL_QUERIES,
    graphiti_episode_payloads,
    percentile,
    score_graphiti_answer,
    summarize_graphiti_scores,
)


def test_graphiti_episodes_cover_zoe_relationship_topics():
    ids = {episode.episode_id for episode in GRAPHITI_EPISODES}

    assert ids == {
        "graphiti_weather_failure_fix",
        "graphiti_memory_preference_old",
        "graphiti_memory_preference_current",
        "graphiti_governance_approval",
        "graphiti_capability_lane",
        "graphiti_recurring_task",
    }
    assert all(episode.evidence_refs for episode in GRAPHITI_EPISODES)


def test_graphiti_queries_cover_multi_hop_and_supersession():
    names = {query.name for query in GRAPHITI_EVAL_QUERIES}

    assert names == {
        "multi_hop_failure_fix",
        "superseded_memory_preference",
        "governance_trust",
        "capability_lane",
        "recurring_graphify_refresh",
    }
    assert all(query.expected_relationship_path for query in GRAPHITI_EVAL_QUERIES)


def test_graphiti_episode_payloads_include_evidence():
    payloads = graphiti_episode_payloads()

    assert len(payloads) == len(GRAPHITI_EPISODES)
    assert all(payload["episode_id"] for payload in payloads)
    assert all(payload["episode_body"] for payload in payloads)
    assert all(payload["evidence_refs"] for payload in payloads)


def test_graphiti_supersession_payloads_have_temporal_order():
    payloads = {payload["episode_id"]: payload for payload in graphiti_episode_payloads()}

    assert payloads["graphiti_memory_preference_old"]["reference_time"] < payloads["graphiti_memory_preference_current"]["reference_time"]
    assert "SUPERSEDES" in payloads["graphiti_memory_preference_current"]["episode_body"]


def test_score_graphiti_answer_reports_missing_terms():
    query = GRAPHITI_EVAL_QUERIES[0]
    score = score_graphiti_answer("weather fixed by voice_queue_guard", query)

    assert score["score"] == 2 / 3
    assert score["missing"] == ["pytest"]
    assert score["expected_relationship_path"] == list(query.expected_relationship_path)


def test_score_graphiti_answer_respects_empty_text_field():
    query = GRAPHITI_EVAL_QUERIES[0]
    score = score_graphiti_answer({"text": ""}, query)

    assert score["text"] == ""
    assert score["score"] == 0.0


def test_summarize_graphiti_scores_reports_latency():
    summary = summarize_graphiti_scores(
        [
            {"score": 1.0, "latency_ms": 100.0},
            {"score": 0.5, "latency_ms": 200.0},
            {"score": 0.0, "latency_ms": 300.0},
        ]
    )

    assert summary["case_count"] == 3
    assert summary["avg_score"] == 0.5
    assert summary["min_score"] == 0.0
    assert summary["p50_latency_ms"] == 200.0
    assert summary["p95_latency_ms"] == 290.0


def test_percentile_rejects_out_of_range_values():
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        percentile([1, 2, 3], 95)
