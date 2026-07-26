import pytest

from zoe_evolution_proposal import EvolutionSignalType
from zoe_observation_trace import (
    ObservationOutcome,
    ObservationTrace,
    ObservationTraceType,
    failed_outcome_trace_to_signal,
    summarize_observation_traces,
)

pytestmark = pytest.mark.ci_safe


def test_recall_trace_allows_lightweight_no_evidence_path():
    trace = ObservationTrace(
        trace_id="trace_recall_1",
        trace_type=ObservationTraceType.RECALL.value,
        surface="mempalace",
        scope="personal",
        user_id="jason",
        outcome=ObservationOutcome.SUCCESS.value,
        summary="MemPalace returned compact recall packet.",
        evidence_refs=(),
        latency_ms=82.5,
        helpfulness=0.9,
    )

    payload = trace.to_dict()

    assert payload["trace_type"] == "recall"
    assert payload["latency_ms"] == 82.5


def test_memory_route_trace_allows_safe_metadata_without_evidence():
    trace = ObservationTrace(
        trace_id="trace_memory_route_1",
        trace_type=ObservationTraceType.MEMORY_ROUTE.value,
        surface="memory",
        scope="system",
        outcome=ObservationOutcome.SUCCESS.value,
        summary="Memory router selected an observe-only route.",
        evidence_refs=(),
        latency_ms=1.25,
        metadata={
            "purpose": "chat",
            "query_length": 42,
            "primary": "hindsight",
            "can_inject_prompt": False,
            "can_write_memory": False,
        },
    )

    payload = trace.to_dict()

    assert payload["trace_type"] == "memory_route"
    assert payload["metadata"]["primary"] == "hindsight"


def test_retain_candidate_trace_requires_evidence():
    trace = ObservationTrace(
        trace_id="trace_retain_missing_evidence",
        trace_type=ObservationTraceType.RETAIN_CANDIDATE.value,
        surface="hindsight",
        scope="project",
        outcome=ObservationOutcome.PARTIAL.value,
        summary="Hindsight proposed a candidate.",
        evidence_refs=(),
    )

    with pytest.raises(ValueError, match="evidence_refs are required"):
        trace.validate()


def test_personal_trace_requires_user_id():
    trace = ObservationTrace(
        trace_id="trace_personal_no_user",
        trace_type=ObservationTraceType.RECALL.value,
        surface="mempalace",
        scope="personal",
        outcome=ObservationOutcome.SUCCESS.value,
        summary="Personal recall without a user is invalid.",
        evidence_refs=(),
    )

    with pytest.raises(ValueError, match="user_id is required"):
        trace.validate()


def test_metadata_rejects_secret_keys():
    trace = ObservationTrace(
        trace_id="trace_secret",
        trace_type=ObservationTraceType.VERIFICATION.value,
        surface="multica",
        scope="system",
        outcome=ObservationOutcome.FAILED.value,
        summary="Verification metadata accidentally included a token.",
        evidence_refs=("pytest:test",),
        metadata={"access_token": "abc"},
    )

    with pytest.raises(ValueError, match="secret fields"):
        trace.validate()


def test_metadata_rejects_nested_secret_keys():
    trace = ObservationTrace(
        trace_id="trace_nested_secret",
        trace_type=ObservationTraceType.VERIFICATION.value,
        surface="multica",
        scope="system",
        outcome=ObservationOutcome.FAILED.value,
        summary="Nested metadata accidentally included an API key.",
        evidence_refs=("pytest:test",),
        metadata={"settings": {"api_key": "abc"}},
    )

    with pytest.raises(ValueError, match="settings.api_key"):
        trace.validate()


def test_metadata_rejects_secret_keys_inside_lists():
    trace = ObservationTrace(
        trace_id="trace_list_secret",
        trace_type=ObservationTraceType.VERIFICATION.value,
        surface="multica",
        scope="system",
        outcome=ObservationOutcome.FAILED.value,
        summary="List metadata accidentally included an API key.",
        evidence_refs=("pytest:test",),
        metadata={"configs": [{"api_key": "abc"}]},
    )

    with pytest.raises(ValueError, match=r"configs\[0\]\.api_key"):
        trace.validate()


def test_metadata_allows_author_attribution_key():
    trace = ObservationTrace(
        trace_id="trace_author_metadata",
        trace_type=ObservationTraceType.VERIFICATION.value,
        surface="multica",
        scope="system",
        outcome=ObservationOutcome.SUCCESS.value,
        summary="Verification metadata includes ordinary attribution.",
        evidence_refs=("pytest:test",),
        metadata={"author": "zoe"},
    )

    assert trace.to_dict()["metadata"] == {"author": "zoe"}


def test_summary_counts_outcomes_types_and_latency_percentiles():
    traces = (
        ObservationTrace(
            trace_id="trace_1",
            trace_type=ObservationTraceType.RECALL.value,
            surface="mempalace",
            scope="system",
            outcome=ObservationOutcome.SUCCESS.value,
            summary="Recall succeeded.",
            evidence_refs=(),
            latency_ms=100,
        ),
        ObservationTrace(
            trace_id="trace_2",
            trace_type=ObservationTraceType.FALLBACK.value,
            surface="hindsight",
            scope="system",
            outcome=ObservationOutcome.SKIPPED.value,
            summary="Hindsight unavailable, fallback used.",
            evidence_refs=(),
            latency_ms=300,
        ),
        ObservationTrace(
            trace_id="trace_3",
            trace_type=ObservationTraceType.OUTCOME_EVAL.value,
            surface="chat",
            scope="project",
            outcome=ObservationOutcome.FAILED.value,
            summary="Correction handling failed.",
            evidence_refs=("eval:correction:001",),
            latency_ms=500,
        ),
    )

    summary = summarize_observation_traces(traces)

    assert summary["trace_count"] == 3
    assert summary["outcomes"] == {"success": 1, "skipped": 1, "failed": 1}
    assert summary["types"]["recall"] == 1
    assert summary["p50_latency_ms"] == 300
    assert summary["p95_latency_ms"] == pytest.approx(480)


def test_failed_outcome_trace_becomes_notice_signal_when_repeated():
    trace = ObservationTrace(
        trace_id="trace_eval_failed",
        trace_type=ObservationTraceType.OUTCOME_EVAL.value,
        surface="chat",
        scope="project",
        user_id="jason",
        outcome=ObservationOutcome.FAILED.value,
        summary="Zoe reintroduced a superseded preference.",
        evidence_refs=("eval:correction:002",),
        subject_id="correction_handling",
    )

    signal = failed_outcome_trace_to_signal(trace, repeat_count=3)

    assert signal is not None
    assert signal.signal_type == EvolutionSignalType.OUTCOME_EVAL_FAILURE.value
    assert signal.evidence_refs == ("eval:correction:002",)
    assert signal.metadata["repeat_count"] == 3


def test_failed_outcome_trace_threshold_is_repeat_count_two():
    trace = ObservationTrace(
        trace_id="trace_eval_threshold",
        trace_type=ObservationTraceType.OUTCOME_EVAL.value,
        surface="chat",
        scope="system",
        outcome=ObservationOutcome.FAILED.value,
        summary="Zoe failed a task completion eval.",
        evidence_refs=("eval:task:threshold",),
    )

    assert failed_outcome_trace_to_signal(trace, repeat_count=1) is None
    signal = failed_outcome_trace_to_signal(trace, repeat_count=2)
    assert signal is not None
    assert signal.signal_id == "signal_trace_eval_threshold_2"


def test_failed_outcome_trace_respects_existing_signal_ids():
    trace = ObservationTrace(
        trace_id="trace_eval_duplicate",
        trace_type=ObservationTraceType.OUTCOME_EVAL.value,
        surface="chat",
        scope="system",
        outcome=ObservationOutcome.BLOCKED.value,
        summary="Zoe was blocked on a cleanup eval.",
        evidence_refs=("eval:cleanup:001",),
    )

    signal = failed_outcome_trace_to_signal(trace, repeat_count=2, idempotency_key="cleanup_eval")
    assert signal is not None
    assert signal.signal_id == "signal_cleanup_eval_2"
    assert (
        failed_outcome_trace_to_signal(
            trace,
            repeat_count=2,
            idempotency_key="cleanup_eval",
            existing_signal_ids={signal.signal_id},
        )
        is None
    )


def test_successful_outcome_trace_does_not_create_notice_signal():
    trace = ObservationTrace(
        trace_id="trace_eval_success",
        trace_type=ObservationTraceType.OUTCOME_EVAL.value,
        surface="chat",
        scope="system",
        outcome=ObservationOutcome.SUCCESS.value,
        summary="Task completion passed.",
        evidence_refs=("eval:task:001",),
    )

    assert failed_outcome_trace_to_signal(trace, repeat_count=5) is None
