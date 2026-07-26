"""Tests for scripts/maintenance/zoe_latency_probe.py.

Covers the bucketing fix (distinct endpoints must not collapse) and the
regression-compare logic. The probe lives under scripts/maintenance, so it's
loaded by path.
"""
import pytest
import importlib.util
import sys
from pathlib import Path

pytestmark = pytest.mark.ci_safe

_PROBE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "zoe_latency_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("zoe_latency_probe", _PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the dataclass's __module__ resolves (from __future__
    # import annotations makes dataclasses look the module up in sys.modules).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


probe = _load_probe()
Sample = probe.Sample  # dataclass: (name, ok, elapsed_ms, status, detail)


class TestSummarizeBuckets:
    def test_distinct_voice_endpoints_not_collapsed(self):
        # The bug: both grouped under "voice", mixing two unrelated latencies.
        samples = [
            Sample("voice.livekit_health", True, 12.0, 200, "ok"),
            Sample("voice.command", True, 800.0, 200, "ok"),
        ]
        s = probe.summarize(samples)
        assert set(s) == {"voice.livekit_health", "voice.command"}
        assert s["voice.livekit_health"]["median_ms"] == 12.0
        assert s["voice.command"]["median_ms"] == 800.0

    def test_repeated_chat_samples_collapse(self):
        # chat.1 / chat.2 are repeats of one metric → one bucket.
        s = probe.summarize([
            Sample("chat.1", True, 100.0, 200, "ok"),
            Sample("chat.2", True, 200.0, 200, "ok"),
        ])
        assert set(s) == {"chat"}
        assert s["chat"]["count"] == 2
        assert s["chat"]["median_ms"] == 150.0

    def test_dotted_endpoint_name_preserved(self):
        s = probe.summarize([Sample("system.status", True, 5.0, 200, "ok")])
        assert set(s) == {"system.status"}

    def test_failed_sample_marks_bucket_not_ok(self):
        s = probe.summarize([Sample("health", False, 0.0, 500, "err")])
        assert s["health"]["ok"] is False
        assert s["health"]["median_ms"] is None


class TestCompare:
    def test_flags_regression_past_both_thresholds(self):
        summary = {"chat": {"median_ms": 1000.0}}
        baseline = {"summary": {"chat": {"median_ms": 400.0}}}
        warns = probe.compare(summary, baseline, warn_ratio=1.5, warn_ms=500.0)
        assert warns and "chat" in warns[0]

    def test_no_warning_within_threshold(self):
        summary = {"chat": {"median_ms": 420.0}}
        baseline = {"summary": {"chat": {"median_ms": 400.0}}}
        assert probe.compare(summary, baseline, warn_ratio=1.5, warn_ms=500.0) == []

    def test_no_warning_when_no_baseline(self):
        summary = {"chat": {"median_ms": 9999.0}}
        assert probe.compare(summary, {}, warn_ratio=1.5, warn_ms=500.0) == []

    def test_flags_fast_endpoint_proportional_regression(self):
        # 10ms → 100ms is 10x / +90ms. The old AND-with-500ms gate missed it;
        # the OR-with-floor must now flag it.
        summary = {"voice.livekit_health": {"median_ms": 100.0}}
        baseline = {"summary": {"voice.livekit_health": {"median_ms": 10.0}}}
        warns = probe.compare(summary, baseline, warn_ratio=1.5, warn_ms=500.0)
        assert warns and "voice.livekit_health" in warns[0]

    def test_no_warning_for_trivial_fast_change(self):
        # 1ms → 3ms is 3x but only +2ms — below RATIO_FLOOR_MS, so it's noise.
        summary = {"health": {"median_ms": 3.0}}
        baseline = {"summary": {"health": {"median_ms": 1.0}}}
        assert probe.compare(summary, baseline, warn_ratio=1.5, warn_ms=500.0) == []

    def test_flags_large_absolute_regression_regardless_of_ratio(self):
        # 2000ms → 2600ms is only 1.3x (< warn_ratio) but +600ms absolute.
        summary = {"chat": {"median_ms": 2600.0}}
        baseline = {"summary": {"chat": {"median_ms": 2000.0}}}
        warns = probe.compare(summary, baseline, warn_ratio=1.5, warn_ms=500.0)
        assert warns and "chat" in warns[0]
