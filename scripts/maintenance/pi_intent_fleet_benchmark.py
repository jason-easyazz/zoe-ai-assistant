#!/usr/bin/env python3
"""Run repeated Pi-vs-Zoe intent fleet benchmarks.

This is speed/accuracy evidence, not promotion evidence: repeated observations
of the same eval case must not be counted as distinct promotion samples.
"""

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
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))
sys.path.insert(0, str(SCRIPT_DIR))

from pi_promotion_eval import _run_pi, _run_zoe_baseline  # noqa: E402
from zoe_pi_promotion import (  # noqa: E402
    DEFAULT_PI_INTENT_EVAL_CASES,
    LOW_RISK_PI_INTENT_GROUPS,
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
    enable_execution: bool = False,
    local_model_configured: bool = False,
    fallback_baseline_latency_ms: float | None = None,
    extraction_failed_baseline_latency_ms: float | None = None,
    measure_zoe_agent_baseline: bool = False,
    zoe_agent_baseline_timeout_seconds: float = 30.0,
    zoe_agent_baseline_max_tokens: int = 256,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    active_repeat = max(1, repeat)
    for repeat_index in range(1, active_repeat + 1):
        for case in cases:
            case.validate()
            zoe = await _run_zoe_baseline(
                case,
                fallback_baseline_latency_ms=fallback_baseline_latency_ms,
                extraction_failed_baseline_latency_ms=extraction_failed_baseline_latency_ms,
                measure_zoe_agent_baseline=measure_zoe_agent_baseline,
                zoe_agent_baseline_timeout_seconds=zoe_agent_baseline_timeout_seconds,
                zoe_agent_baseline_max_tokens=zoe_agent_baseline_max_tokens,
            )
            pi = None
            if run_pi:
                pi = await _run_pi(
                    case,
                    transport=transport,
                    enable_execution=enable_execution,
                    local_model_configured=local_model_configured,
                )
            observations.append(_observation(case, repeat_index=repeat_index, zoe=zoe, pi=pi))
    return observations


def build_report(
    cases: Sequence[PiIntentEvalCase],
    observations: Sequence[Mapping[str, Any]],
    *,
    repeat: int,
    run_pi: bool,
    transport: str,
    include_observations: bool = False,
) -> dict[str, Any]:
    unique_case_ids = sorted({case.case_id for case in cases})
    payload: dict[str, Any] = {
        "benchmark_kind": "speed_accuracy_observations_not_promotion_samples",
        "note": "Repeated observations measure latency stability; promotion sample counts must use unique labeled evidence.",
        "repeat": max(1, repeat),
        "unique_case_count": len(unique_case_ids),
        "observation_count": len(observations),
        "pi_ran": run_pi,
        "transport": transport if run_pi else None,
        "low_risk_intent_groups": sorted(LOW_RISK_PI_INTENT_GROUPS),
        "eval_case_source_counts": summarize_eval_case_sources(cases),
        "eval_cases": eval_cases_to_dict(cases),
        "summary": {
            "overall": _stats(observations),
            "by_route_class": _breakdown(observations, lambda item: str(item.get("route_class") or "unknown")),
            "by_intent_group": _breakdown(observations, lambda item: str(item.get("intent_group") or "unknown")),
            "by_source": _breakdown(observations, lambda item: str(item.get("source") or "unknown")),
        },
    }
    if include_observations:
        payload["observations"] = list(observations)
    return payload


def _observation(
    case: PiIntentEvalCase,
    *,
    repeat_index: int,
    zoe: Mapping[str, Any],
    pi: Mapping[str, Any] | None,
) -> dict[str, Any]:
    expected = case.expected_intent
    pi_intent = pi.get("intent") if pi else None
    return {
        "case_id": case.case_id,
        "repeat_index": repeat_index,
        "intent_group": case.intent_group,
        "route_class": case.route_class,
        "source": case.source,
        "negative": case.negative,
        "expected_intent": expected,
        "zoe_intent": zoe.get("intent"),
        "zoe_correct": zoe.get("intent") == expected,
        "zoe_latency_ms": float(zoe.get("latency_ms") or 0.0),
        "zoe_router_latency_ms": float(zoe.get("router_latency_ms") or 0.0),
        "zoe_baseline_kind": zoe.get("baseline_kind"),
        "zoe_baseline_comparable": bool(zoe.get("baseline_comparable")),
        "zoe_baseline_timed_out": bool(zoe.get("baseline_timed_out")),
        "pi_intent": pi_intent,
        "pi_correct": (pi_intent == expected) if pi else None,
        "pi_latency_ms": float(pi.get("latency_ms")) if pi else None,
        "pi_confidence": float(pi.get("confidence") or 0.0) if pi else None,
        "pi_timed_out": bool(pi.get("timed_out")) if pi else None,
    }


def _breakdown(
    observations: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in observations:
        grouped[key_fn(item)].append(item)
    return {key: _stats(values) for key, values in sorted(grouped.items())}


def _stats(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {
            "observation_count": 0,
            "unique_case_count": 0,
            "zoe_accuracy": 0.0,
            "pi_accuracy": None,
            "accuracy_delta": None,
            "zoe_latency_ms": _latency_stats([]),
            "pi_latency_ms": _latency_stats([]),
            "pi_timeout_rate": None,
        }
    zoe_correct = [bool(item.get("zoe_correct")) for item in observations]
    pi_correct_values = [item.get("pi_correct") for item in observations if item.get("pi_correct") is not None]
    zoe_accuracy = _rate(zoe_correct)
    pi_accuracy = _rate(bool(value) for value in pi_correct_values) if pi_correct_values else None
    pi_timeouts = [bool(item.get("pi_timed_out")) for item in observations if item.get("pi_timed_out") is not None]
    return {
        "observation_count": len(observations),
        "unique_case_count": len({str(item.get("case_id")) for item in observations}),
        "zoe_accuracy": zoe_accuracy,
        "pi_accuracy": pi_accuracy,
        "accuracy_delta": None if pi_accuracy is None else pi_accuracy - zoe_accuracy,
        "zoe_latency_ms": _latency_stats(_floats(item.get("zoe_latency_ms") for item in observations)),
        "pi_latency_ms": _latency_stats(_floats(item.get("pi_latency_ms") for item in observations)),
        "pi_timeout_rate": _rate(pi_timeouts) if pi_timeouts else None,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repeated Zoe/Pi intent speed and accuracy benchmark observations")
    parser.add_argument("--run-pi", action="store_true", help="Run configured local Pi beside Zoe")
    parser.add_argument("--transport", choices=["print", "rpc"], default="rpc")
    parser.add_argument("--allow-execution", action="store_true", help="Temporarily set ZOE_PI_ALLOW_EXECUTION=true")
    parser.add_argument("--local-model-configured", action="store_true", help="Temporarily set ZOE_PI_LOCAL_MODEL_CONFIGURED=true")
    parser.add_argument("--repeat", type=int, default=3, help="Repeat each case this many times for latency stability")
    parser.add_argument("--cases-file", action="append", default=[], help="JSON or JSONL eval cases file. Repeat to combine datasets.")
    parser.add_argument("--no-default-cases", action="store_true", help="Use only --cases-file datasets")
    parser.add_argument("--include-observations", action="store_true", help="Include every observation row in JSON output")
    parser.add_argument("--fallback-baseline-latency-ms", type=float, default=None)
    parser.add_argument("--extraction-failed-baseline-latency-ms", type=float, default=None)
    parser.add_argument("--measure-zoe-agent-baseline", action="store_true")
    parser.add_argument("--zoe-agent-baseline-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--zoe-agent-baseline-max-tokens", type=int, default=256)
    args = parser.parse_args(argv)

    loaded_case_groups = [load_pi_intent_eval_cases(path) for path in args.cases_file]
    base_cases = [] if args.no_default_cases else list(DEFAULT_PI_INTENT_EVAL_CASES)
    cases = merge_pi_intent_eval_cases(base_cases, *loaded_case_groups)
    observations = asyncio.run(
        run_benchmark(
            cases,
            repeat=args.repeat,
            run_pi=args.run_pi,
            transport=args.transport,
            enable_execution=args.allow_execution,
            local_model_configured=args.local_model_configured,
            fallback_baseline_latency_ms=args.fallback_baseline_latency_ms,
            extraction_failed_baseline_latency_ms=args.extraction_failed_baseline_latency_ms,
            measure_zoe_agent_baseline=args.measure_zoe_agent_baseline,
            zoe_agent_baseline_timeout_seconds=args.zoe_agent_baseline_timeout_seconds,
            zoe_agent_baseline_max_tokens=args.zoe_agent_baseline_max_tokens,
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
                include_observations=args.include_observations,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
