import importlib.util
import sys
from pathlib import Path

from hindsight_bakeoff import (
    EVAL_QUERIES,
    SYNTHETIC_EVENTS,
    percentile,
    score_recall_response,
    summarize_bakeoff_scores,
    summarize_recall_latency,
    synthetic_retain_payloads,
)


def test_synthetic_events_validate_and_include_required_cases():
    payloads = synthetic_retain_payloads()

    assert len(payloads) == len(SYNTHETIC_EVENTS) >= 4
    assert {item["event_type"] for item in payloads} >= {"failure", "fix", "preference", "approval"}
    assert all(item["user_id"] for item in payloads)
    assert all(item["evidence_refs"] for item in payloads)


def test_eval_queries_cover_plan_questions():
    names = {query.name for query in EVAL_QUERIES}

    assert names == {
        "recall_weather_failure",
        "recall_weather_fix",
        "recall_user_preference",
        "recall_governance",
    }


def test_score_recall_response_reports_missing_terms():
    query = EVAL_QUERIES[0]
    score = score_recall_response({"results": [{"text": "weather card had duplicate responses"}]}, query)

    assert score["score"] == 2 / 3
    assert score["missing"] == ["voice"]


def test_synthetic_evidence_refs_are_tuples_not_strings():
    for event in SYNTHETIC_EVENTS:
        assert isinstance(event.evidence_refs, tuple)
        assert all(isinstance(ref, str) for ref in event.evidence_refs)
        assert all(len(ref) > 4 for ref in event.evidence_refs)


def test_summarize_bakeoff_scores_reports_score_and_latency():
    summary = summarize_bakeoff_scores(
        [
            {"score": 1.0, "latency_ms": 20.0},
            {"score": 0.5, "latency_ms": 10.0},
            {"score": 0.0, "latency_ms": 30.0},
        ]
    )

    assert summary["case_count"] == 3
    assert summary["avg_score"] == 0.5
    assert summary["min_score"] == 0.0
    assert summary["p50_latency_ms"] == 20.0
    assert summary["p95_latency_ms"] == 29.0


def test_summarize_recall_latency_reports_budget_status():
    latency = summarize_recall_latency(
        [
            {"latency_ms": 20.0, "enabled": True},
            {"latency_ms": 10.0, "enabled": True},
            {"latency_ms": 30.0, "enabled": True},
        ],
        budget_ms=25.0,
    )

    assert latency == {
        "measured": True,
        "case_count": 3,
        "enabled_case_count": 3,
        "budget_ms": 25.0,
        "p50_latency_ms": 20.0,
        "p95_latency_ms": 29.0,
        "p95_within_budget": False,
        "hot_path_status": "async_or_cached_only",
    }


def test_summarize_recall_latency_reports_hot_path_eligible():
    latency = summarize_recall_latency(
        [
            {"latency_ms": 20.0, "enabled": True},
            {"latency_ms": 10.0, "enabled": True},
            {"latency_ms": 30.0, "enabled": True},
        ],
        budget_ms=30.0,
    )

    assert latency["p95_within_budget"] is True
    assert latency["hot_path_status"] == "eligible"


def test_summarize_recall_latency_marks_disabled_runs_not_measured():
    latency = summarize_recall_latency(
        [
            {"latency_ms": 0.01, "enabled": False},
            {"latency_ms": 0.02, "enabled": False},
        ],
        budget_ms=600.0,
    )

    assert latency["measured"] is False
    assert latency["case_count"] == 2
    assert latency["enabled_case_count"] == 0
    assert latency["p50_latency_ms"] is None
    assert latency["p95_latency_ms"] is None
    assert latency["p95_within_budget"] is False
    assert latency["hot_path_status"] == "not_measured"


def test_summarize_recall_latency_ignores_missing_enabled_latency():
    latency = summarize_recall_latency(
        [
            {"latency_ms": None, "enabled": True},
            {"latency_ms": 800.0, "enabled": True},
        ],
        budget_ms=600.0,
    )

    assert latency["measured"] is True
    assert latency["case_count"] == 2
    assert latency["enabled_case_count"] == 1
    assert latency["p95_latency_ms"] == 800.0
    assert latency["p95_within_budget"] is False
    assert latency["hot_path_status"] == "async_or_cached_only"


def test_summarize_recall_latency_ignores_disabled_latencies_in_mixed_run():
    latency = summarize_recall_latency(
        [
            {"latency_ms": 0.1, "enabled": False},
            {"latency_ms": 0.2, "enabled": False},
            {"latency_ms": 800.0, "enabled": True},
        ],
        budget_ms=600.0,
    )

    assert latency["measured"] is True
    assert latency["case_count"] == 3
    assert latency["enabled_case_count"] == 1
    assert latency["p95_latency_ms"] == 800.0
    assert latency["p95_within_budget"] is False
    assert latency["hot_path_status"] == "async_or_cached_only"


def test_summarize_recall_latency_requires_positive_budget():
    try:
        summarize_recall_latency([{"latency_ms": 1.0}], budget_ms=0.0)
    except ValueError as exc:
        assert "budget_ms must be positive" in str(exc)
    else:
        raise AssertionError("summarize_recall_latency accepted a non-positive budget")


def test_percentile_rejects_out_of_range_values():
    try:
        percentile([1, 2, 3], 95)
    except ValueError as exc:
        assert "between 0.0 and 1.0" in str(exc)
    else:
        raise AssertionError("percentile accepted an out-of-range value")


def test_maintenance_runner_imports_real_bakeoff_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "hindsight_bakeoff.py"
    spec = importlib.util.spec_from_file_location("hindsight_bakeoff_runner", script_path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    original_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(runner)
    finally:
        sys.path[:] = original_sys_path

    assert len(runner.EVAL_QUERIES) == len(EVAL_QUERIES)
    assert runner.summarize_bakeoff_scores([{"score": 1, "latency_ms": 1}])["case_count"] == 1
    assert runner.summarize_recall_latency([{"latency_ms": 1, "enabled": True}], budget_ms=600)["p95_within_budget"] is True
