"""Prometheus metrics for the Zoe voice pipeline.

Per-stage latency histograms for `/api/voice/turn` and `/api/voice/command`
so Pass 2 speed work can be gated on measured improvements (see
`docs/performance/VOICE_BASELINE_2026-04.md`).

All metrics share the same ``REGISTRY`` as ``memory_metrics`` so the existing
``/metrics`` scrape endpoint picks them up with no additional wiring.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from memory_metrics import REGISTRY


# Buckets span 10 ms - 20 s so we can watch the ~800 ms target without losing
# the ~8 s baseline during Pass 2.
_STAGE_BUCKETS = (0.010, 0.025, 0.050, 0.075, 0.100, 0.150, 0.200, 0.300,
                  0.500, 0.750, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0)

voice_stage_seconds = Histogram(
    "zoe_voice_stage_seconds",
    "Per-stage latency (seconds) for /api/voice/turn and /api/voice/command.",
    ["stage"],
    buckets=_STAGE_BUCKETS,
    registry=REGISTRY,
)

voice_turn_count = Counter(
    "zoe_voice_turn_count",
    "Voice turn outcomes.",
    ["outcome", "path"],
    registry=REGISTRY,
)

voice_intent_hit_count = Counter(
    "zoe_voice_intent_hit_count",
    "Intent-router hits observed on the voice path, labelled by intent name.",
    ["intent"],
    registry=REGISTRY,
)

voice_identity_source_count = Counter(
    "zoe_voice_identity_source_count",
    "Where the effective user_id came from on a voice turn.",
    ["source"],
    registry=REGISTRY,
)

voice_failure_reason_count = Counter(
    "zoe_voice_failure_reason_count",
    "Voice failure/degraded outcomes by path and reason.",
    ["path", "reason"],
    registry=REGISTRY,
)


__all__ = [
    "voice_stage_seconds",
    "voice_turn_count",
    "voice_intent_hit_count",
    "voice_identity_source_count",
    "voice_failure_reason_count",
]
