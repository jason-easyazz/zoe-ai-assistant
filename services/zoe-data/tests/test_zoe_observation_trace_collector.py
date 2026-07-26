import pytest

from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType
from zoe_observation_trace_collector import ObservationTraceCollectorPolicy, collect_observation_traces

pytestmark = pytest.mark.ci_safe


def _trace(
    trace_id: str,
    *,
    trace_type: str = ObservationTraceType.MEMORY_ROUTE.value,
    surface: str = "memory",
    scope: str = "system",
    user_id: str | None = None,
) -> ObservationTrace:
    return ObservationTrace(
        trace_id=trace_id,
        trace_type=trace_type,
        surface=surface,
        scope=scope,
        user_id=user_id,
        outcome=ObservationOutcome.SUCCESS.value,
        summary="Trace accepted by collector.",
        evidence_refs=("pytest:evidence",) if trace_type == ObservationTraceType.VERIFICATION.value else (),
        latency_ms=10.0,
    )


def test_collect_observation_traces_accepts_valid_non_persistent_batch():
    result = collect_observation_traces((_trace("trace_1"), _trace("trace_2")))
    payload = result.to_dict()

    assert result.ok is True
    assert payload["accepted_count"] == 2
    assert payload["persisted"] is False
    assert payload["summary"]["trace_count"] == 2
    assert payload["summary"]["types"] == {"memory_route": 2}


def test_collect_observation_traces_rejects_empty_batch():
    result = collect_observation_traces(())

    assert result.ok is False
    assert result.accepted == ()
    assert result.rejected == ({"trace_id": "*batch*", "reason": "batch is empty"},)


def test_collector_policy_rejects_persistence_until_enabled_elsewhere():
    with pytest.raises(ValueError, match="persistence is not enabled"):
        ObservationTraceCollectorPolicy(allow_persistence=True).validate()


def test_collector_policy_requires_positive_batch_size():
    with pytest.raises(ValueError, match="max_batch_size must be positive"):
        ObservationTraceCollectorPolicy(max_batch_size=0).validate()


def test_collect_observation_traces_rejects_oversized_batch():
    result = collect_observation_traces(
        (_trace("trace_1"), _trace("trace_2")),
        policy=ObservationTraceCollectorPolicy(max_batch_size=1),
    )

    assert result.ok is False
    assert result.accepted == ()
    assert "exceeds max_batch_size" in result.rejected[0]["reason"]


def test_collect_observation_traces_rejects_invalid_trace_shape():
    result = collect_observation_traces(
        (
            ObservationTrace(
                trace_id="trace_bad",
                trace_type="unknown",
                surface="memory",
                scope="system",
                outcome=ObservationOutcome.SUCCESS.value,
                summary="Invalid trace type.",
                evidence_refs=(),
            ),
        )
    )

    assert result.ok is False
    assert result.accepted == ()
    assert "unknown trace_type" in result.rejected[0]["reason"]


def test_collect_observation_traces_discards_entire_batch_on_rejection():
    result = collect_observation_traces(
        (
            _trace("trace_ok"),
            ObservationTrace(
                trace_id="trace_bad",
                trace_type="unknown",
                surface="memory",
                scope="system",
                outcome=ObservationOutcome.SUCCESS.value,
                summary="Invalid trace type.",
                evidence_refs=(),
            ),
        )
    )

    assert result.ok is False
    assert result.accepted == ()
    assert result.to_dict()["accepted_count"] == 0
    assert result.rejected[0]["trace_id"] == "trace_bad"


def test_collect_observation_traces_reports_batch_rejections_from_original_input():
    result = collect_observation_traces(
        (
            ObservationTrace(
                trace_id="trace_bad_user_1",
                trace_type="unknown",
                surface="memory",
                scope="personal",
                user_id="user_1",
                outcome=ObservationOutcome.SUCCESS.value,
                summary="Invalid trace type.",
                evidence_refs=(),
            ),
            _trace("trace_user_2", scope="personal", user_id="user_2"),
        )
    )

    assert result.ok is False
    assert result.accepted == ()
    assert [rejection["reason"] for rejection in result.rejected] == [
        "trace_bad_user_1: unknown trace_type 'unknown'",
        "batch contains multiple user_id values",
    ]


def test_collect_observation_traces_rejects_disallowed_surface():
    result = collect_observation_traces(
        (_trace("trace_graphiti", surface="graphiti"),),
        policy=ObservationTraceCollectorPolicy(allowed_surfaces=("memory",)),
    )

    assert result.ok is False
    assert "surface 'graphiti' is not allowed" in result.rejected[0]["reason"]


def test_collect_observation_traces_rejects_disallowed_trace_type():
    result = collect_observation_traces(
        (_trace("trace_recall", trace_type=ObservationTraceType.RECALL.value),),
        policy=ObservationTraceCollectorPolicy(allowed_trace_types=(ObservationTraceType.MEMORY_ROUTE.value,)),
    )

    assert result.ok is False
    assert "trace_type 'recall' is not allowed" in result.rejected[0]["reason"]


def test_collect_observation_traces_rejects_mixed_user_batch():
    traces = (
        _trace("trace_user_1", scope="personal", user_id="user_1"),
        _trace("trace_user_2", scope="personal", user_id="user_2"),
    )

    result = collect_observation_traces(traces)

    assert result.ok is False
    assert result.accepted == ()
    assert result.rejected[0]["reason"] == "batch contains multiple user_id values"


def test_collect_observation_traces_can_require_single_scope_batch():
    traces = (_trace("trace_system", scope="system"), _trace("trace_project", scope="project"))

    result = collect_observation_traces(
        traces,
        policy=ObservationTraceCollectorPolicy(require_single_scope_batch=True),
    )

    assert result.ok is False
    assert result.rejected[0]["reason"] == "batch contains multiple scopes"
