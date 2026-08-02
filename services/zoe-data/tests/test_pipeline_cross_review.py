"""Tests for the flag-dark Multica cross-review evidence producer."""

import pytest

from pipeline_cross_review import produce_cross_review_evidence
from pipeline_evidence import PipelineState, valid_cross_review_signoff

pytestmark = pytest.mark.ci_safe

_HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


@pytest.fixture(autouse=True)
def _enable_cross_review(monkeypatch):
    monkeypatch.setenv("ZOE_MULTICA_CROSS_REVIEW", "true")


def _result(**overrides):
    result = {
        "reviewer_vendor": "anthropic",
        "verdict": "approve",
        "reviewed_sha": _HEAD_SHA,
        "blocking_issues": [],
    }
    result.update(overrides)
    return result


def _produce(result, **overrides):
    captured = []

    def fake_dispatch(request):
        captured.append(request)
        return result

    kwargs = {
        "pr_number": 1597,
        "acceptance_contract": "Add only a flag-dark producer and focused tests.",
        "implementer_vendor": "openai",
        "head_sha": _HEAD_SHA,
        "dispatch": fake_dispatch,
    }
    kwargs.update(overrides)
    return produce_cross_review_evidence(**kwargs), captured


def test_approve_matching_sha_different_vendor_produces_valid_evidence():
    evidence, calls = _produce(_result())

    assert len(calls) == 1
    assert calls[0].pr_number == 1597
    assert evidence is not None
    state = PipelineState(
        task_ref="multica:T1",
        phase="review",
        pr_head_sha=_HEAD_SHA,
        implementer_platform="openai",
        allow_cross_review_signoff=True,
        evidence=[evidence],
    )
    assert valid_cross_review_signoff(state) is True


def test_blocking_verdict_produces_no_signoff():
    evidence, _ = _produce(_result(verdict="blocking", blocking_issues=[{"id": "B1"}]))
    assert evidence is None


def test_same_vendor_reviewer_is_refused():
    evidence, _ = _produce(_result(reviewer_vendor="openai"))
    assert evidence is None


def test_same_vendor_alias_is_refused():
    evidence, _ = _produce(_result(reviewer_vendor="openai"), implementer_vendor="codex")
    assert evidence is None


def test_sha_mismatch_is_refused():
    evidence, _ = _produce(_result(reviewed_sha="b" * 40))
    assert evidence is None


@pytest.mark.parametrize(
    "result",
    [
        "review complete: CLEAN",
        "{not-json",
        {"reviewer_vendor": "anthropic", "verdict": "approve"},
        _result(confidence=0.2),
    ],
)
def test_unparseable_or_low_confidence_result_fails_closed(result):
    evidence, _ = _produce(result)
    assert evidence is None


def test_feature_flag_defaults_off_and_skips_dispatch(monkeypatch):
    monkeypatch.delenv("ZOE_MULTICA_CROSS_REVIEW", raising=False)
    evidence, calls = _produce(_result())
    assert evidence is None
    assert calls == []


def test_diff_ref_context_is_forwarded_to_injected_dispatch():
    evidence, calls = _produce(_result(), pr_number=None, diff_ref="origin/main...HEAD")
    assert evidence is not None
    assert calls[0].diff_ref == "origin/main...HEAD"
