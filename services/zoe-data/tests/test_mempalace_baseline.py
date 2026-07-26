from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path

import pytest

from mempalace_baseline import (
    BASELINE_CASES,
    percentile,
    recall_text_from_results,
    run_mempalace_baseline,
    score_recall_text,
)

pytestmark = pytest.mark.ci_safe


@dataclass
class FakeRef:
    text: str


class FakeMemoryService:
    def __init__(self):
        self.rows = []

    async def ingest(self, text: str, **kwargs):
        self.rows.append({"text": text, "kwargs": kwargs})
        return FakeRef(text=text)

    async def search(self, query: str, **kwargs):
        query_terms = {part.strip("?.,'").lower() for part in query.split()}
        ranked = []
        for row in self.rows:
            text = row["text"]
            overlap = sum(1 for term in query_terms if term and term in text.lower())
            ranked.append((overlap, text))
        ranked.sort(reverse=True)
        return [FakeRef(text=text) for overlap, text in ranked[: kwargs.get("limit", 5)] if overlap > 0]


def test_baseline_cases_cover_memory_plan_topics():
    ids = {case.case_id for case in BASELINE_CASES}

    assert ids == {
        "weather_failure_fix",
        "memory_preference",
        "governance_approval",
        "tool_capability",
        "tool_failure_fixed_by_relation",
        "graphify_backend_supersession",
        "recurring_task_governed_by_relation",
    }
    assert all(case.expected_terms for case in BASELINE_CASES)


def test_baseline_cases_include_relational_and_supersession_topics():
    cases = {case.case_id: case for case in BASELINE_CASES}

    relation_case = cases["tool_failure_fixed_by_relation"]
    assert {"openclaw", "hermes"}.issubset(set(relation_case.expected_terms))
    assert "fixed" in relation_case.query.lower()

    supersession_case = cases["graphify_backend_supersession"]
    assert "superseded" in supersession_case.text.lower()
    assert "local" in supersession_case.expected_terms

    recurring_case = cases["recurring_task_governed_by_relation"]
    assert recurring_case.memory_type == "recurring_task"
    assert "multica" in recurring_case.expected_terms


def test_score_recall_text_reports_missing_terms():
    score = score_recall_text("weather card duplicate response", ("weather", "duplicate", "voice"))

    assert score["score"] == 2 / 3
    assert score["missing"] == ["voice"]


def test_recall_text_from_results_supports_refs_dicts_and_strings():
    text = recall_text_from_results([FakeRef("alpha"), {"content": "beta"}, "gamma"])

    assert text.splitlines() == ["alpha", "beta", "gamma"]


def test_percentile_interpolates():
    assert percentile([10, 20, 30], 0.5) == 20
    assert percentile([10, 20, 30], 0.95) == pytest.approx(29)


@pytest.mark.asyncio
async def test_run_mempalace_baseline_scores_fake_service():
    summary = await run_mempalace_baseline(FakeMemoryService(), timeout_s=0.1)

    assert summary["case_count"] == len(BASELINE_CASES)
    assert summary["avg_score"] >= 0.75
    assert summary["p95_latency_ms"] >= summary["p50_latency_ms"]
    assert all(result["hit_count"] >= 1 for result in summary["results"])


@pytest.mark.asyncio
async def test_maintenance_runner_cleans_up_after_baseline_failure(monkeypatch, capsys):
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "mempalace_baseline.py"
    spec = importlib.util.spec_from_file_location("mempalace_baseline_runner", script_path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    class FailingService:
        def __init__(self):
            self.deleted = []

        async def delete_user(self, user_id: str, *, actor: str):
            self.deleted.append((user_id, actor))
            return 7

    service = FailingService()

    async def fail_baseline(*args, **kwargs):
        raise RuntimeError("baseline failed")

    monkeypatch.setattr(runner, "get_memory_service", lambda: service)
    monkeypatch.setattr(runner, "run_mempalace_baseline", fail_baseline)
    monkeypatch.setattr(runner.sys, "argv", ["mempalace_baseline.py"])

    with pytest.raises(RuntimeError, match="baseline failed"):
        await runner._main()

    assert service.deleted == [("zoe-mempalace-baseline", "mempalace_baseline")]
    assert '"cleanup_removed": 7' in capsys.readouterr().out
