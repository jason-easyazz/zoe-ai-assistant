"""Tests for the harness-side focused-test runner (deterministic verify)."""

import pytest
import pipeline_focused_tests as pft
from pipeline_focused_tests import FocusedTestResult, focused_test_evidence_item

pytestmark = pytest.mark.ci_safe


def test_run_focused_pr_tests_no_pr_url_does_not_run():
    result = pft.run_focused_pr_tests("")
    assert result.ran is False
    assert result.passed is False


def test_run_focused_pr_tests_no_changed_test_files_does_not_run(monkeypatch):
    # PR resolves but changes no test files -> fall back to agent flow (ran=False).
    monkeypatch.setattr(
        pft, "_changed_test_files", lambda pr_url, *, root: ([], "feature", "deadbeef")
    )
    result = pft.run_focused_pr_tests("https://github.com/o/r/pull/1", repo_root="/tmp")
    assert result.ran is False
    assert "no changed test files" in result.summary


def test_changed_test_files_filters_to_pytest_modules(monkeypatch):
    payload = {
        "headRefName": "wt/t_x",
        "headRefOid": "abc123",
        "files": [
            {"path": "services/zoe-data/tests/test_calendar_utils.py"},
            {"path": "services/zoe-data/calendar_utils.py"},  # source, not a test
            {"path": "docs/notes.md"},
            {"path": "services/zoe-data/tests/helpers_test.py"},
            {"path": "services/zoe-data/tests/conftest.py"},  # not test_/_test
        ],
    }

    import json as _json

    monkeypatch.setattr(pft, "_run", lambda *a, **k: (0, _json.dumps(payload)))
    tests, head_ref, head_oid = pft._changed_test_files("pr", root="/x")
    assert tests == [
        "services/zoe-data/tests/test_calendar_utils.py",
        "services/zoe-data/tests/helpers_test.py",
    ]
    assert head_ref == "wt/t_x" and head_oid == "abc123"


def test_focused_test_evidence_item_shape():
    result = FocusedTestResult(
        ran=True,
        passed=True,
        summary="focused pytest (1 file(s)): exit 0\n1 passed",
        content_hash="h",
        test_paths=("services/zoe-data/tests/test_calendar_utils.py",),
    )
    item = focused_test_evidence_item(result, phase="verify")
    assert item.kind == "test"
    assert item.passed is True
    assert item.metadata["phase"] == "verify"
    assert item.metadata["source"] == "harness"
    assert item.metadata["test_paths"] == ["services/zoe-data/tests/test_calendar_utils.py"]
