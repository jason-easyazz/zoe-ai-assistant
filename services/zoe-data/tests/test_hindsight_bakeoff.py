import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hindsight_bakeoff import (
    EVAL_QUERIES,
    SYNTHETIC_EVENTS,
    eval_queries_for_budgets,
    eval_queries_for_user,
    normalize_recall_budgets,
    percentile,
    score_recall_response,
    synthetic_events_for_user,
    summarize_bakeoff_scores,
    summarize_recall_latency,
    summarize_recall_latency_by_budget,
    synthetic_retain_payloads,
)


def test_synthetic_events_validate_and_include_required_cases():
    payloads = synthetic_retain_payloads()

    assert len(payloads) == len(SYNTHETIC_EVENTS) >= 4
    assert {item["event_type"] for item in payloads} >= {"failure", "fix", "preference", "approval"}
    assert all(item["user_id"] for item in payloads)
    assert all(item["evidence_refs"] for item in payloads)


def test_synthetic_events_can_be_parameterized_for_multi_user_runs():
    events = synthetic_events_for_user("casey")
    payloads = synthetic_retain_payloads("casey")

    assert {event.user_id for event in events} == {"casey"}
    assert {item["user_id"] for item in payloads} == {"casey"}
    assert tuple(event.event_id for event in events) == tuple(event.event_id for event in SYNTHETIC_EVENTS)
    assert {event.user_id for event in SYNTHETIC_EVENTS} == {"jason"}


def test_eval_queries_can_be_parameterized_for_multi_user_runs():
    queries = eval_queries_for_user("casey")

    assert {query.user_id for query in queries} == {"casey"}
    assert tuple(query.name for query in queries) == tuple(query.name for query in EVAL_QUERIES)
    assert {query.user_id for query in EVAL_QUERIES} == {"jason"}


def test_recall_budgets_normalize_and_dedupe():
    assert normalize_recall_budgets([" low, mid ", "low", "HIGH"]) == ("low", "mid", "high")


def test_recall_budget_normalization_rejects_blank_values():
    try:
        normalize_recall_budgets(["low,,mid"])
    except ValueError as exc:
        assert "budget must not be blank" in str(exc)
    else:
        raise AssertionError("normalize_recall_budgets accepted a blank budget")


def test_eval_queries_expand_for_recall_budgets_without_mutating_defaults():
    queries = eval_queries_for_budgets(EVAL_QUERIES[:2], ["low,mid"])

    assert len(queries) == 4
    assert [query.budget for query in queries] == ["low", "low", "mid", "mid"]
    assert [query.name for query in queries] == [
        "recall_weather_failure",
        "recall_weather_fix",
        "recall_weather_failure",
        "recall_weather_fix",
    ]
    assert {query.budget for query in EVAL_QUERIES} == {"mid"}


def test_eval_queries_keep_default_budgets_when_no_override_is_given():
    assert eval_queries_for_budgets(EVAL_QUERIES, None) == EVAL_QUERIES
    assert eval_queries_for_budgets(EVAL_QUERIES, []) == EVAL_QUERIES


def test_bakeoff_user_override_rejects_blank_user_id():
    for helper in (synthetic_events_for_user, eval_queries_for_user, synthetic_retain_payloads):
        try:
            helper("  ")
        except ValueError as exc:
            assert "user_id must not be blank" in str(exc)
        else:
            raise AssertionError(f"{helper.__name__} accepted a blank user_id")


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


def test_summarize_recall_latency_by_budget_reports_each_recall_budget():
    latency = summarize_recall_latency_by_budget(
        [
            {"budget": "low", "latency_ms": 100.0, "enabled": True},
            {"budget": "low", "latency_ms": 200.0, "enabled": True},
            {"budget": "mid", "latency_ms": 700.0, "enabled": True},
            {"budget": "mid", "latency_ms": 0.1, "enabled": False},
        ],
        budget_ms=600.0,
    )

    assert set(latency) == {"low", "mid"}
    assert latency["low"]["hot_path_status"] == "eligible"
    assert latency["low"]["enabled_case_count"] == 2
    assert latency["mid"]["hot_path_status"] == "async_or_cached_only"
    assert latency["mid"]["enabled_case_count"] == 1


def test_summarize_recall_latency_by_budget_uses_unknown_for_missing_budget():
    latency = summarize_recall_latency_by_budget([{"latency_ms": 20.0, "enabled": True}], budget_ms=30.0)

    assert set(latency) == {"unknown"}
    assert latency["unknown"]["hot_path_status"] == "eligible"


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
    assert latency["p50_latency_ms"] == 800.0
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

    assert len(runner.eval_queries_for_user()) == len(EVAL_QUERIES)
    assert {event.user_id for event in runner.synthetic_events_for_user("casey")} == {"casey"}
    assert runner._user_id_arg(" casey ") == "casey"
    try:
        runner._user_id_arg("  ")
    except argparse.ArgumentTypeError as exc:
        assert "user_id must not be blank" in str(exc)
    else:
        raise AssertionError("_user_id_arg accepted a blank user_id")
    assert runner.summarize_bakeoff_scores([{"score": 1, "latency_ms": 1}])["case_count"] == 1
    assert runner.summarize_recall_latency([{"latency_ms": 1, "enabled": True}], budget_ms=600)["p95_within_budget"] is True
    expanded = runner.eval_queries_for_budgets(runner.eval_queries_for_user(), ["low,mid"])
    assert len(expanded) == len(EVAL_QUERIES) * 2
    assert runner.summarize_recall_latency_by_budget(
        [{"budget": "low", "latency_ms": 1, "enabled": True}],
        budget_ms=600,
    )["low"]["p95_within_budget"] is True



@pytest.fixture(scope="module")
def bakeoff_runner():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "hindsight_bakeoff.py"
    spec = importlib.util.spec_from_file_location("hindsight_bakeoff_runner_dynamic", script_path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    original_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(runner)
    finally:
        sys.path[:] = original_sys_path
    return runner


class FakeHindsightClient:
    def __init__(self, config):
        self.config = type("Config", (), {"enabled": True})()

    def enabled_status(self):
        return {"enabled": True, "offline_only": True}

    async def recall(self, **kwargs):
        query = kwargs["query"].lower()
        if "weather card" in query:
            text = "weather duplicate voice"
        elif "fix worked" in query:
            text = "voice queue guard"
        elif "jason" in query:
            text = "hindsight postgres"
        else:
            text = "multica evidence"
        return {"enabled": True, "bank_id": "zoe-test", "results": [{"text": text}]}


class FakeHindsightConfig:
    @classmethod
    def from_env(cls):
        return cls()


@pytest.mark.asyncio
async def test_runner_json_omits_budget_breakdown_without_budget_arg(bakeoff_runner, monkeypatch, capsys):
    runner = bakeoff_runner
    monkeypatch.setattr(runner, "HindsightConfig", FakeHindsightConfig)
    monkeypatch.setattr(runner, "HindsightMemoryClient", FakeHindsightClient)
    monkeypatch.setattr(runner.sys, "argv", ["hindsight_bakeoff.py", "--json"])

    assert await runner.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert "latency" in payload
    assert "latency_by_recall_budget" not in payload


@pytest.mark.asyncio
async def test_runner_text_output_prints_budget_breakdown_when_requested(bakeoff_runner, monkeypatch, capsys):
    runner = bakeoff_runner
    monkeypatch.setattr(runner, "HindsightConfig", FakeHindsightConfig)
    monkeypatch.setattr(runner, "HindsightMemoryClient", FakeHindsightClient)
    monkeypatch.setattr(runner.sys, "argv", ["hindsight_bakeoff.py", "--recall-budget", "low,mid"])

    assert await runner.main() == 0
    output = capsys.readouterr().out

    assert '"low"' in output
    assert '"mid"' in output
    assert "recall_weather_failure: score=1.00" in output
