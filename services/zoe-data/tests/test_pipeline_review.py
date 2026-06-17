"""Tests for the harness-side objective review-readiness gate (deterministic review)."""

import json

import pipeline_review as pr
from pipeline_review import ReviewReadiness, _checks_all_green, _pr_number


def test_pr_number_parses_trailing_slash_and_plain():
    assert _pr_number("https://github.com/o/r/pull/642") == "642"
    assert _pr_number("https://github.com/o/r/pull/642/") == "642"
    assert _pr_number("https://github.com/o/r/tree/main") == ""


def test_checks_all_green_requires_at_least_one_and_all_green():
    assert _checks_all_green([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]) is True
    assert _checks_all_green([]) is False  # empty rollup is not trusted as green
    assert _checks_all_green([{"status": "IN_PROGRESS"}]) is False
    assert _checks_all_green([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) is False


def test_assess_no_pr_url():
    assert pr.assess_pr_review_ready("").ready is False


def test_assess_ready_when_open_green_and_no_unresolved(monkeypatch):
    def fake_run(cmd, *, cwd, timeout=60):
        if cmd[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(
                {"state": "OPEN", "mergeStateStatus": "CLEAN", "statusCheckRollup": [{"conclusion": "SUCCESS"}]}
            )
        return 0, ""

    monkeypatch.setattr(pr, "_run", fake_run)
    monkeypatch.setattr(pr, "_unresolved_thread_count", lambda n, *, cwd: 0)
    out = pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp")
    assert out.ready is True
    assert out.unresolved_threads == 0


def test_assess_not_ready_when_unresolved_threads(monkeypatch):
    def fake_run(cmd, *, cwd, timeout=60):
        return 0, json.dumps(
            {"state": "OPEN", "statusCheckRollup": [{"conclusion": "SUCCESS"}]}
        )

    monkeypatch.setattr(pr, "_run", fake_run)
    monkeypatch.setattr(pr, "_unresolved_thread_count", lambda n, *, cwd: 2)
    out = pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp")
    assert out.ready is False
    assert out.unresolved_threads == 2


def test_assess_not_ready_when_ci_pending(monkeypatch):
    def fake_run(cmd, *, cwd, timeout=60):
        return 0, json.dumps(
            {"state": "OPEN", "statusCheckRollup": [{"status": "IN_PROGRESS"}]}
        )

    monkeypatch.setattr(pr, "_run", fake_run)
    monkeypatch.setattr(pr, "_unresolved_thread_count", lambda n, *, cwd: 0)
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is False


def test_assess_fails_open_on_gh_error(monkeypatch):
    monkeypatch.setattr(pr, "_run", lambda *a, **k: (1, "gh boom"))
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is False
