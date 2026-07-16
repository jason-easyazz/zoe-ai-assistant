"""Tests for the harness-side objective review-readiness gate (deterministic review).

Review approves on PR OPEN + all CI checks green. Greptile threads/confidence are
deliberately NOT gated here (owned by the closeout greploop merge gate), so a
fresh PR with open Greptile threads can still be review-approved.
"""

import pytest
import json

import pipeline_review as pr
from pipeline_review import _checks_all_green

pytestmark = pytest.mark.ci_safe


def _patch_pr_view(monkeypatch, payload: dict):
    monkeypatch.setattr(
        pr, "_run", lambda cmd, *, cwd, timeout=60: (0, json.dumps(payload))
    )


def test_checks_all_green_requires_at_least_one_and_all_green():
    assert _checks_all_green([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]) is True
    assert _checks_all_green([]) is False  # empty rollup is not trusted as green
    assert _checks_all_green([{"status": "IN_PROGRESS"}]) is False
    assert _checks_all_green([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) is False


def test_assess_no_pr_url():
    assert pr.assess_pr_review_ready("").ready is False


def test_assess_ready_when_open_and_green(monkeypatch):
    _patch_pr_view(
        monkeypatch,
        {"state": "OPEN", "mergeStateStatus": "CLEAN", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
    )
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is True


def test_assess_ready_does_not_query_or_gate_on_threads(monkeypatch):
    # A fresh PR normally has an open Greptile thread at review time; review must
    # still approve on green CI (the thread is the closeout gate's job). The module
    # no longer has any thread-counting path.
    _patch_pr_view(
        monkeypatch, {"state": "OPEN", "statusCheckRollup": [{"conclusion": "SUCCESS"}]}
    )
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is True
    assert not hasattr(pr, "_unresolved_thread_count")


def test_assess_not_ready_when_ci_pending(monkeypatch):
    _patch_pr_view(monkeypatch, {"state": "OPEN", "statusCheckRollup": [{"status": "IN_PROGRESS"}]})
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is False


def test_assess_not_ready_when_pr_not_open(monkeypatch):
    _patch_pr_view(monkeypatch, {"state": "MERGED", "statusCheckRollup": [{"conclusion": "SUCCESS"}]})
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is False


def test_assess_fails_open_on_gh_error(monkeypatch):
    monkeypatch.setattr(pr, "_run", lambda *a, **k: (1, "gh boom"))
    assert pr.assess_pr_review_ready("https://github.com/o/r/pull/9", repo_root="/tmp").ready is False
