#!/usr/bin/env python3
"""Probe Zoe perceived voice latency beside optional Pi intent evidence.

The important user experience question is not only how long Pi/Gemma takes to
finish. It is whether Zoe can acknowledge the user immediately, then let the
slower candidate path finish without dead air. This script measures that cheap
presence layer and can attach the existing Pi fleet benchmark in one JSON
report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))
sys.path.insert(0, str(SCRIPT_DIR))

from pi_intent_fleet_benchmark import build_report as build_pi_fleet_report  # noqa: E402
from pi_intent_fleet_benchmark import run_benchmark as run_pi_fleet_benchmark  # noqa: E402
from zoe_pi_promotion import (  # noqa: E402
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    load_pi_intent_eval_cases,
    merge_pi_intent_eval_cases,
)
from voice_presence import (  # noqa: E402
    processing_ack_event,
    wake_ack_audio_payload,
    wake_ack_variant,
    wake_presence_events,
)

DEFAULT_PERCEIVED_BUDGET_MS = 150.0
DEFAULT_REPEAT = 25


def build_conversation_flow_report(
    *,
    env: Mapping[str, str] | None = None,
    repeat: int = DEFAULT_REPEAT,
    perceived_budget_ms: float = DEFAULT_PERCEIVED_BUDGET_MS,
    pi_benchmark: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact report for Zoe's perceived conversation latency."""
    values = env if env is not None else os.environ
    active_repeat = max(1, repeat)
    wake = _measure_path("wake_ack", active_repeat, lambda index: _wake_payload(values, index))
    processing = _measure_path(
        "processing_ack",
        active_repeat,
        lambda index: _processing_payload(values, index),
    )
    gate = _perceived_gate(wake, processing, budget_ms=perceived_budget_ms)
    return {
        "report_kind": "zoe_conversation_flow_probe",
        "note": (
            "Perceived latency measures the first cheap acknowledgement event. "
            "It does not prove Pi/Gemma final-answer latency or promotion eligibility."
        ),
        "repeat": active_repeat,
        "perceived_budget_ms": perceived_budget_ms,
        "presence": {
            "wake_ack": wake,
            "processing_ack": processing,
            "gate": gate,
        },
        "pi_benchmark": dict(pi_benchmark) if pi_benchmark else None,
        "decision": _decision(gate, pi_benchmark),
    }


def _wake_payload(env: Mapping[str, str], index: int) -> dict[str, Any]:
    variant = wake_ack_variant(env, index=index)
    audio = wake_ack_audio_payload(env, audio_path=str(variant.get("audio_path") or ""))
    events = wake_presence_events(ack_phrase=str(variant.get("phrase") or ""), ack_audio=audio)
    return {
        "configured": _events_have_visible_ack(events),
        "events": [_safe_event(event) for event in events],
    }


def _processing_payload(env: Mapping[str, str], index: int) -> dict[str, Any]:
    event = processing_ack_event(env, index=index)
    safe = _safe_event(event) if event else None
    return {"configured": bool(safe and _event_has_visible_ack(safe)), "event": safe}


def _measure_path(name: str, repeat: int, payload_factory: Callable[[int], Mapping[str, Any]]) -> dict[str, Any]:
    latencies: list[float] = []
    payloads: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for index in range(repeat):
        start = time.perf_counter()
        try:
            payload = payload_factory(index)
        except Exception as exc:  # pragma: no cover - defensive probe guard
            payload = {"configured": False, "error": type(exc).__name__}
            errors.append(f"{type(exc).__name__}: {exc}")
        latencies.append((time.perf_counter() - start) * 1000)
        payloads.append(payload)
    configured_count = sum(1 for payload in payloads if payload.get("configured"))
    first_payload = payloads[0] if payloads else {}
    return {
        "name": name,
        "configured": configured_count > 0,
        "configured_count": configured_count,
        "sample_count": len(payloads),
        "latency_ms": _latency_stats(latencies),
        "first_payload": dict(first_payload),
        "errors": errors[:5],
    }


def _perceived_gate(wake: Mapping[str, Any], processing: Mapping[str, Any], *, budget_ms: float) -> dict[str, Any]:
    wake_p95 = _path_p95(wake)
    processing_p95 = _path_p95(processing)
    wake_ready = bool(wake.get("configured")) and wake_p95 is not None and wake_p95 <= budget_ms
    processing_ready = bool(processing.get("configured")) and processing_p95 is not None and processing_p95 <= budget_ms
    return {
        "budget_ms": budget_ms,
        "wake_ack_ready": wake_ready,
        "processing_ack_ready": processing_ready,
        "natural_flow_buffer_ready": processing_ready,
        "full_wake_to_processing_ready": wake_ready and processing_ready,
        "blockers": _gate_blockers(wake_ready, processing_ready, wake, processing, budget_ms),
    }


def _gate_blockers(
    wake_ready: bool,
    processing_ready: bool,
    wake: Mapping[str, Any],
    processing: Mapping[str, Any],
    budget_ms: float,
) -> list[str]:
    blockers: list[str] = []
    if not wake.get("configured"):
        blockers.append("wake_ack_not_configured")
    elif not wake_ready:
        blockers.append(f"wake_ack_p95_exceeds_{budget_ms:g}ms")
    if not processing.get("configured"):
        blockers.append("processing_ack_not_configured")
    elif not processing_ready:
        blockers.append(f"processing_ack_p95_exceeds_{budget_ms:g}ms")
    return blockers


def _decision(gate: Mapping[str, Any], pi_benchmark: Mapping[str, Any] | None) -> dict[str, Any]:
    pi_state = _pi_state(pi_benchmark)
    recommendations: list[str] = []
    if gate.get("natural_flow_buffer_ready"):
        recommendations.append("Use the intent buffer for slow voice turns; first acknowledgement is inside budget.")
    else:
        recommendations.append("Configure or repair processing acknowledgements before judging natural conversation flow.")
    if not gate.get("wake_ack_ready"):
        recommendations.append("Configure cached wake acknowledgement phrases/audio for the instant 'hey Zoe' response.")
    if pi_state["state"] == "candidate_speed_accuracy_win":
        recommendations.append("Pi shows a benchmark win here, but promotion still requires unique labeled evidence gates.")
    elif pi_state["state"] == "not_measured":
        recommendations.append("Run with --run-pi to attach Pi speed/accuracy evidence.")
    else:
        recommendations.append("Keep Pi in shadow/evidence mode until it beats Zoe on comparable speed and accuracy.")
    return {
        "state": "buffer_ready_pi_shadow" if gate.get("natural_flow_buffer_ready") else "buffer_not_ready",
        "pi_candidate": pi_state,
        "recommendations": recommendations,
    }


def _pi_state(pi_benchmark: Mapping[str, Any] | None) -> dict[str, Any]:
    if not pi_benchmark:
        return {"state": "not_measured"}
    winners: list[str] = []
    groups = (((pi_benchmark.get("summary") or {}).get("by_intent_group")) or {})
    for group, stats in groups.items():
        pi_accuracy = stats.get("pi_accuracy")
        accuracy_delta = stats.get("accuracy_delta")
        zoe_p95 = ((stats.get("zoe_latency_ms") or {}).get("p95"))
        pi_p95 = ((stats.get("pi_latency_ms") or {}).get("p95"))
        if (
            isinstance(pi_accuracy, (int, float))
            and isinstance(accuracy_delta, (int, float))
            and isinstance(zoe_p95, (int, float))
            and isinstance(pi_p95, (int, float))
            and pi_accuracy >= 0.95
            and accuracy_delta >= 0.05
            and pi_p95 < zoe_p95
        ):
            winners.append(str(group))
    return {
        "state": "candidate_speed_accuracy_win" if winners else "keep_shadow",
        "winning_groups": sorted(winners),
        "benchmark_kind": pi_benchmark.get("benchmark_kind"),
        "observation_count": pi_benchmark.get("observation_count"),
        "unique_case_count": pi_benchmark.get("unique_case_count"),
    }


def _path_p95(path_report: Mapping[str, Any]) -> float | None:
    value = ((path_report.get("latency_ms") or {}).get("p95"))
    return float(value) if isinstance(value, (int, float)) else None


def _safe_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    safe = dict(event)
    audio_b64 = safe.pop("audio_base64", None)
    if audio_b64:
        safe["audio_base64_chars"] = len(str(audio_b64))
    return safe


def _events_have_visible_ack(events: Sequence[Mapping[str, Any]]) -> bool:
    return any(_event_has_visible_ack(event) for event in events)


def _event_has_visible_ack(event: Mapping[str, Any] | None) -> bool:
    if not event:
        return False
    return bool(str(event.get("text") or "").strip() or event.get("audio_base64_chars") or event.get("audio_base64"))


def _latency_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "p50": None, "p95": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "avg": sum(ordered) / len(ordered),
        "p50": statistics.median(ordered),
        "p95": _percentile(ordered, 95),
        "min": ordered[0],
        "max": ordered[-1],
    }


def _percentile(ordered_values: Sequence[float], percentile: int) -> float | None:
    if not ordered_values:
        return None
    rank = max(1, math.ceil((percentile / 100) * len(ordered_values)))
    return ordered_values[min(rank - 1, len(ordered_values) - 1)]


def _demo_env(base_env: Mapping[str, str]) -> dict[str, str]:
    values = dict(base_env)
    values.setdefault("ZOE_WAKE_ACK_PHRASES", "Yes Jason.|Hi Jason.|Good morning Jason.")
    values.setdefault("ZOE_PROCESSING_ACK_PHRASES", "Let me check.|One moment.|I will check that.")
    return values


def _load_cases(paths: Sequence[str], *, no_default_cases: bool) -> list[PiIntentEvalCase]:
    loaded_case_groups = [load_pi_intent_eval_cases(path) for path in paths]
    base_cases = [] if no_default_cases else list(DEFAULT_PI_INTENT_EVAL_CASES)
    return merge_pi_intent_eval_cases(base_cases, *loaded_case_groups)


async def _optional_pi_benchmark(args: argparse.Namespace) -> Mapping[str, Any] | None:
    if not args.run_pi:
        return None
    cases = _load_cases(args.cases_file, no_default_cases=args.no_default_cases)
    observations = await run_pi_fleet_benchmark(
        cases,
        repeat=args.benchmark_repeat,
        run_pi=True,
        transport=args.transport,
        enable_execution=args.allow_execution,
        local_model_configured=args.local_model_configured,
        fallback_baseline_latency_ms=args.fallback_baseline_latency_ms,
        extraction_failed_baseline_latency_ms=args.extraction_failed_baseline_latency_ms,
        measure_zoe_agent_baseline=args.measure_zoe_agent_baseline,
        zoe_agent_baseline_timeout_seconds=args.zoe_agent_baseline_timeout_seconds,
        zoe_agent_baseline_max_tokens=args.zoe_agent_baseline_max_tokens,
    )
    return build_pi_fleet_report(
        cases,
        observations,
        repeat=args.benchmark_repeat,
        run_pi=True,
        transport=args.transport,
        include_observations=args.include_observations,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Zoe perceived conversation latency and optional Pi evidence")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT, help="Presence-path samples to collect")
    parser.add_argument("--perceived-budget-ms", type=float, default=DEFAULT_PERCEIVED_BUDGET_MS)
    parser.add_argument("--demo-defaults", action="store_true", help="Use demo wake/processing phrases when env is unset")
    parser.add_argument("--run-pi", action="store_true", help="Attach Pi fleet benchmark evidence")
    parser.add_argument("--transport", choices=["print", "rpc"], default="rpc")
    parser.add_argument("--allow-execution", action="store_true")
    parser.add_argument("--local-model-configured", action="store_true")
    parser.add_argument("--benchmark-repeat", type=int, default=1)
    parser.add_argument("--cases-file", action="append", default=[])
    parser.add_argument("--no-default-cases", action="store_true")
    parser.add_argument("--include-observations", action="store_true")
    parser.add_argument("--fallback-baseline-latency-ms", type=float, default=None)
    parser.add_argument("--extraction-failed-baseline-latency-ms", type=float, default=None)
    parser.add_argument("--measure-zoe-agent-baseline", action="store_true")
    parser.add_argument("--zoe-agent-baseline-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--zoe-agent-baseline-max-tokens", type=int, default=256)
    args = parser.parse_args(argv)

    values = _demo_env(os.environ) if args.demo_defaults else os.environ
    pi_benchmark = asyncio.run(_optional_pi_benchmark(args))
    print(
        json.dumps(
            build_conversation_flow_report(
                env=values,
                repeat=args.repeat,
                perceived_budget_ms=args.perceived_budget_ms,
                pi_benchmark=pi_benchmark,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
