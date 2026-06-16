#!/usr/bin/env python3
"""Benchmark Zoe's cue -> Pi -> optional safe-fulfillment hybrid flow."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_intent_lab import compare_pi_intent_lab  # noqa: E402
from zoe_pi_promotion import (  # noqa: E402
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    eval_cases_to_dict,
    load_pi_intent_eval_cases,
    merge_pi_intent_eval_cases,
    summarize_eval_case_sources,
)


async def run_benchmark(
    cases: Sequence[PiIntentEvalCase],
    *,
    repeat: int,
    run_pi: bool,
    transport: str = "rpc",
    allow_execution: bool = False,
    local_model_configured: bool = False,
    include_safe_fulfillment: bool = False,
    safe_fulfillment_timeout_seconds: float = 8.0,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    active_repeat = max(1, repeat)
    for repeat_index in range(1, active_repeat + 1):
        for case in cases:
            case.validate()
            result = await compare_pi_intent_lab(
                case.text,
                run_pi=run_pi,
                pi_transport=transport,
                allow_pi_execution=allow_execution,
                local_model_configured=local_model_configured,
                include_hybrid_status=False,
                include_safe_fulfillment=include_safe_fulfillment,
                safe_fulfillment_timeout_seconds=safe_fulfillment_timeout_seconds,
            )
            observations.append(_observation(case, repeat_index=repeat_index, result=result))
    return observations


def build_report(
    cases: Sequence[PiIntentEvalCase],
    observations: Sequence[Mapping[str, Any]],
    *,
    repeat: int,
    run_pi: bool,
    transport: str,
    include_safe_fulfillment: bool,
    include_observations: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "benchmark_kind": "pi_hybrid_flow_observations_not_promotion_samples",
        "note": _report_note(include_safe_fulfillment),
        "repeat": max(1, repeat),
        "unique_case_count": len({case.case_id for case in cases}),
        "observation_count": len(observations),
        "pi_ran": run_pi,
        "transport": transport if run_pi else None,
        "safe_fulfillment_enabled": include_safe_fulfillment,
        "safe_fulfillment_side_effects": "read_only_external_only" if include_safe_fulfillment else "none",
        "eval_case_source_counts": summarize_eval_case_sources(cases),
        "eval_cases": eval_cases_to_dict(cases),
        "summary": {
            "overall": _stats(observations, pi_ran=run_pi),
            "by_intent_group": _breakdown(
                observations,
                lambda item: str(item.get("intent_group") or "unknown"),
                pi_ran=run_pi,
            ),
            "by_source": _breakdown(
                observations,
                lambda item: str(item.get("source") or "unknown"),
                pi_ran=run_pi,
            ),
        },
    }
    if include_observations:
        payload["observations"] = list(observations)
    return payload


def _observation(case: PiIntentEvalCase, *, repeat_index: int, result: Mapping[str, Any]) -> dict[str, Any]:
    pi = result.get("pi") or {}
    safe = result.get("safe_fulfillment") or {}
    flow = result.get("simulated_hybrid_flow") or {}
    pi_intent = pi.get("intent")
    expected = case.expected_intent
    safe_success = bool(
        safe.get("attempted")
        and safe.get("allowed")
        and not safe.get("timed_out")
        and not safe.get("error")
        and int(safe.get("response_chars") or 0) > 0
    )
    return {
        "case_id": case.case_id,
        "repeat_index": repeat_index,
        "intent_group": case.intent_group,
        "source": case.source,
        "negative": case.negative,
        "expected_intent": expected,
        "zoe_router_intent": (result.get("zoe_router") or {}).get("intent"),
        "pi_intent": pi_intent,
        "pi_correct": pi_intent == expected,
        "pi_confidence": _float_or_none(pi.get("confidence")),
        "pi_timed_out": bool(pi.get("timed_out")),
        "pi_error": pi.get("error"),
        "cue_latency_ms": _float_or_none(flow.get("cue_latency_ms")),
        "pi_latency_ms": _float_or_none(pi.get("latency_ms")),
        "safe_fulfillment_requested": bool(safe.get("requested")),
        "safe_fulfillment_attempted": bool(safe.get("attempted")),
        "safe_fulfillment_allowed": bool(safe.get("allowed")),
        "safe_fulfillment_success": safe_success,
        "safe_fulfillment_timed_out": bool(safe.get("timed_out")),
        "safe_fulfillment_error": safe.get("error"),
        "safe_fulfillment_latency_ms": _float_or_none(safe.get("latency_ms")),
        "final_completion_latency_ms": _float_or_none(flow.get("final_completion_latency_ms")),
        "natural_flow_candidate": bool(flow.get("natural_flow_candidate")),
        "response_preview": safe.get("response_preview") or "",
    }


def _report_note(include_safe_fulfillment: bool) -> str:
    note = (
        "Measures perceived cue latency, Pi completion latency, and optional read-only fulfillment latency. "
        "Repeated observations are speed stability evidence, not unique promotion samples."
    )
    if include_safe_fulfillment:
        note += (
            " In-process safe fulfillment uses the same handlers as the lab endpoint, but DB-backed handlers "
            "may require FastAPI startup initialization for production-like answers."
        )
    return note


def _breakdown(
    observations: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
    *,
    pi_ran: bool,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in observations:
        grouped[key_fn(item)].append(item)
    return {key: _stats(values, pi_ran=pi_ran) for key, values in sorted(grouped.items())}


def _stats(observations: Sequence[Mapping[str, Any]], *, pi_ran: bool = True) -> dict[str, Any]:
    if not observations:
        return {
            "observation_count": 0,
            "unique_case_count": 0,
            "pi_accuracy": None,
            "pi_timeout_rate": None,
            "natural_flow_rate": None,
            "safe_fulfillment_success_rate": None,
            "cue_latency_ms": _latency_stats([]),
            "pi_latency_ms": _latency_stats([]),
            "safe_fulfillment_latency_ms": _latency_stats([]),
            "final_completion_latency_ms": _latency_stats([]),
        }
    pi_correct = [bool(item.get("pi_correct")) for item in observations]
    safe_requested = [item for item in observations if item.get("safe_fulfillment_requested")]
    return {
        "observation_count": len(observations),
        "unique_case_count": len({str(item.get("case_id")) for item in observations}),
        "pi_accuracy": _rate(pi_correct) if pi_ran else None,
        "pi_timeout_rate": _rate(bool(item.get("pi_timed_out")) for item in observations) if pi_ran else None,
        "natural_flow_rate": _rate(bool(item.get("natural_flow_candidate")) for item in observations) if pi_ran else None,
        "safe_fulfillment_success_rate": (
            _rate(bool(item.get("safe_fulfillment_success")) for item in safe_requested) if safe_requested else None
        ),
        "cue_latency_ms": _latency_stats(_floats(item.get("cue_latency_ms") for item in observations)),
        "pi_latency_ms": _latency_stats(_floats(item.get("pi_latency_ms") for item in observations)),
        "safe_fulfillment_latency_ms": _latency_stats(
            _floats(item.get("safe_fulfillment_latency_ms") for item in observations)
        ),
        "final_completion_latency_ms": _latency_stats(
            _floats(item.get("final_completion_latency_ms") for item in observations)
        ),
    }


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


def _rate(values: Iterable[bool]) -> float:
    items = list(values)
    return sum(1 for item in items if item) / len(items) if items else 0.0


def _floats(values: Iterable[Any]) -> list[float]:
    floats: list[float] = []
    for value in values:
        if isinstance(value, (int, float)):
            floats.append(float(value))
    return floats


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _load_cases(paths: Sequence[str], *, no_default_cases: bool) -> list[PiIntentEvalCase]:
    loaded_case_groups = [load_pi_intent_eval_cases(path) for path in paths]
    base_cases = [] if no_default_cases else list(DEFAULT_PI_INTENT_EVAL_CASES)
    return merge_pi_intent_eval_cases(base_cases, *loaded_case_groups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark cue -> Pi -> optional safe fulfillment flow")
    parser.add_argument("--run-pi", action="store_true")
    parser.add_argument("--transport", choices=["print", "rpc"], default="rpc")
    parser.add_argument("--allow-execution", action="store_true")
    parser.add_argument("--local-model-configured", action="store_true")
    parser.add_argument("--include-safe-fulfillment", action="store_true")
    parser.add_argument("--safe-fulfillment-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--cases-file", action="append", default=[])
    parser.add_argument("--no-default-cases", action="store_true")
    parser.add_argument("--include-observations", action="store_true")
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases_file, no_default_cases=args.no_default_cases)
    observations = asyncio.run(
        run_benchmark(
            cases,
            repeat=args.repeat,
            run_pi=args.run_pi,
            transport=args.transport,
            allow_execution=args.allow_execution,
            local_model_configured=args.local_model_configured,
            include_safe_fulfillment=args.include_safe_fulfillment,
            safe_fulfillment_timeout_seconds=args.safe_fulfillment_timeout_seconds,
        )
    )
    print(
        json.dumps(
            build_report(
                cases,
                observations,
                repeat=args.repeat,
                run_pi=args.run_pi,
                transport=args.transport,
                include_safe_fulfillment=args.include_safe_fulfillment,
                include_observations=args.include_observations,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
