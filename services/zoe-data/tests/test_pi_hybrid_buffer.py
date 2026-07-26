import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pi_hybrid_buffer import (
    _path_p95,
    _presence_blockers,
    _presence_gate,
    _presence_latency_warnings,
)

pytestmark = pytest.mark.ci_safe


def _path_report(*, configured: bool = True, p95: float | int | str | None = 40.0) -> dict[str, object]:
    report: dict[str, object] = {"configured": configured}
    if p95 is not None:
        report["latency_ms"] = {"p95": p95}
    return report


def test_path_p95_extracts_numeric_latency_and_ignores_missing_or_malformed_values():
    assert _path_p95(_path_report(p95=42.5)) == 42.5
    assert _path_p95(_path_report(p95=17)) == 17.0
    assert _path_p95(_path_report(p95=None)) is None
    assert _path_p95({"configured": True, "latency_ms": {}}) is None
    assert _path_p95(_path_report(p95="42.5")) is None


def test_presence_latency_warnings_only_emit_for_budget_overages():
    assert _presence_latency_warnings(90.0, 120.0, 150.0) == []
    assert _presence_latency_warnings(None, None, 150.0) == []
    assert _presence_latency_warnings(151.0, None, 150.0) == [
        "wake_ack_payload_construction_p95_exceeds_150ms"
    ]
    assert _presence_latency_warnings(None, 200.0, 150.0) == [
        "processing_ack_payload_construction_p95_exceeds_150ms"
    ]
    assert _presence_latency_warnings(175.0, 200.0, 150.0) == [
        "wake_ack_payload_construction_p95_exceeds_150ms",
        "processing_ack_payload_construction_p95_exceeds_150ms",
    ]


def test_presence_blockers_report_missing_or_incomplete_payloads():
    assert _presence_blockers({}, {}) == [
        "wake_ack_not_configured",
        "processing_ack_not_configured",
    ]
    assert _presence_blockers({"configured": True}, {"configured": False}) == [
        "processing_ack_not_configured"
    ]
    assert _presence_blockers(
        _path_report(configured=True, p95=30.0),
        _path_report(configured=True, p95=45.0),
    ) == []


def test_presence_gate_marks_ready_when_both_payloads_are_configured_within_budget():
    wake = _path_report(configured=True, p95=45.0)
    processing = _path_report(configured=True, p95=60.0)

    gate = _presence_gate(wake, processing, budget_ms=150.0)

    assert gate == {
        "payload_construction_budget_ms": 150.0,
        "latency_kind": "payload_construction_only",
        "wake_ack_ready": True,
        "processing_ack_ready": True,
        "natural_flow_buffer_ready": True,
        "full_wake_to_processing_ready": True,
        "blockers": [],
        "warnings": [],
    }


def test_presence_gate_surfaces_blockers_and_latency_warnings_when_not_ready():
    wake = _path_report(configured=False, p95=175.0)
    processing = _path_report(configured=True, p95=200.0)

    gate = _presence_gate(wake, processing, budget_ms=150.0)

    assert gate["wake_ack_ready"] is False
    assert gate["processing_ack_ready"] is True
    assert gate["natural_flow_buffer_ready"] is True
    assert gate["full_wake_to_processing_ready"] is False
    assert gate["blockers"] == ["wake_ack_not_configured"]
    assert gate["warnings"] == [
        "wake_ack_payload_construction_p95_exceeds_150ms",
        "processing_ack_payload_construction_p95_exceeds_150ms",
    ]