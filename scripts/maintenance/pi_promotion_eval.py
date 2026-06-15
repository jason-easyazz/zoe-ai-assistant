#!/usr/bin/env python3
"""Run or report Pi-vs-Zoe intent promotion evidence.

Default mode is side-effect free and prints the built-in eval fixture plus an
empty policy report. Use --demo for deterministic promotion-gate smoke data, or
--run-pi to compare Zoe's current router with the configured local Pi runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from zoe_pi_promotion import (  # noqa: E402
    DEFAULT_PI_INTENT_EVAL_CASES,
    LOW_RISK_PI_INTENT_GROUPS,
    PiIntentEvalCase,
    PiPromotionPolicy,
    PiRouteSample,
    eval_cases_to_dict,
    load_pi_intent_eval_cases,
    merge_pi_intent_eval_cases,
    summarize_eval_case_sources,
    summarize_pi_promotion,
)


@contextmanager
def _temporary_env(updates: dict[str, str | None]):
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _demo_samples() -> list[PiRouteSample]:
    samples: list[PiRouteSample] = []
    for index in range(30):
        samples.append(
            PiRouteSample(
                case_id=f"demo_weather_{index}",
                intent_group="weather",
                expected_intent="weather",
                zoe_intent="reminder_list" if index < 20 else "weather",
                pi_intent="weather",
                zoe_latency_ms=850,
                pi_latency_ms=320,
                pi_confidence=0.9,
                pi_transport="rpc",
            )
        )
    return samples


async def _run_zoe_baseline(
    case: PiIntentEvalCase,
    *,
    fallback_baseline_latency_ms: float | None = None,
    extraction_failed_baseline_latency_ms: float | None = None,
    measure_zoe_agent_baseline: bool = False,
    zoe_agent_baseline_timeout_seconds: float = 30.0,
    zoe_agent_baseline_max_tokens: int = 256,
) -> dict[str, Any]:
    with _temporary_env({"ZOE_PI_INTENT_ENABLED": "false"}):
        from intent_router import detect_and_extract_intent

        start = time.perf_counter()
        intent = await detect_and_extract_intent(case.text)
        router_latency_ms = (time.perf_counter() - start) * 1000

    latency_ms = router_latency_ms
    baseline_kind = "router"
    baseline_comparable = True
    baseline_timed_out = False
    baseline_response_chars: int | None = None
    baseline_error: str | None = None
    agent_baseline: dict[str, Any] | None = None
    if intent is not None:
        # A current router hit is Zoe's comparable baseline, even when the
        # fixture came from an older fallback/extraction-failure trace.
        pass
    elif case.route_class == "fallback":
        if fallback_baseline_latency_ms is not None:
            latency_ms = fallback_baseline_latency_ms
            baseline_kind = "operator_fallback_override"
        elif measure_zoe_agent_baseline:
            agent_baseline = await _run_zoe_agent_baseline(
                case,
                timeout_seconds=zoe_agent_baseline_timeout_seconds,
                max_tokens=zoe_agent_baseline_max_tokens,
            )
        else:
            baseline_kind = "router_only_not_comparable"
            baseline_comparable = False
    elif case.route_class == "extraction_failed":
        if extraction_failed_baseline_latency_ms is not None:
            latency_ms = extraction_failed_baseline_latency_ms
            baseline_kind = "operator_extraction_failed_override"
        elif measure_zoe_agent_baseline:
            agent_baseline = await _run_zoe_agent_baseline(
                case,
                timeout_seconds=zoe_agent_baseline_timeout_seconds,
                max_tokens=zoe_agent_baseline_max_tokens,
            )
        else:
            baseline_kind = "router_only_not_comparable"
            baseline_comparable = False

    if agent_baseline is not None:
        latency_ms = agent_baseline["latency_ms"]
        baseline_kind = agent_baseline["baseline_kind"]
        baseline_comparable = agent_baseline["baseline_comparable"]
        baseline_timed_out = agent_baseline["timed_out"]
        baseline_response_chars = agent_baseline["response_chars"]
        baseline_error = agent_baseline.get("error")

    return {
        "intent": intent.name if intent else None,
        "confidence": getattr(intent, "confidence", None) if intent else None,
        "latency_ms": latency_ms,
        "router_latency_ms": router_latency_ms,
        "baseline_kind": baseline_kind,
        "baseline_comparable": baseline_comparable,
        "baseline_timed_out": baseline_timed_out,
        "baseline_response_chars": baseline_response_chars,
        "baseline_error": baseline_error,
        "correct": (intent.name if intent else None) == case.expected_intent,
    }


async def _run_zoe_agent_baseline(
    case: PiIntentEvalCase,
    *,
    timeout_seconds: float,
    max_tokens: int,
) -> dict[str, Any]:
    from zoe_agent import run_zoe_agent

    session_id = f"pi-eval-baseline-{case.case_id}-{uuid.uuid4().hex[:8]}"
    start = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            run_zoe_agent(
                case.text,
                session_id,
                "pi-eval",
                history=[],
                db_memory_context="",
                portrait="",
                max_tokens_override=max_tokens,
            ),
            timeout=timeout_seconds,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "latency_ms": latency_ms,
            "baseline_kind": f"zoe_agent_{case.route_class}_baseline",
            "baseline_comparable": True,
            "timed_out": False,
            "response_chars": len(response or ""),
            "error": None,
        }
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "latency_ms": latency_ms,
            "baseline_kind": f"zoe_agent_{case.route_class}_timeout",
            "baseline_comparable": False,
            "timed_out": True,
            "response_chars": 0,
            "error": "TimeoutError",
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "latency_ms": latency_ms,
            "baseline_kind": f"zoe_agent_{case.route_class}_error",
            "baseline_comparable": False,
            "timed_out": False,
            "response_chars": 0,
            "error": exc.__class__.__name__,
        }

async def _run_pi(case: PiIntentEvalCase, *, transport: str, enable_execution: bool, local_model_configured: bool) -> dict[str, Any]:
    prefilter_enabled = os.environ.get("ZOE_PI_INTENT_PREFILTER_ENABLED", "true")
    updates = {
        "ZOE_PI_INTENT_ENABLED": "true",
        "ZOE_PI_INTENT_TRANSPORT": transport,
        "ZOE_PI_ALLOW_EXECUTION": "true" if enable_execution else "false",
        "ZOE_PI_LOCAL_MODEL_CONFIGURED": "true" if local_model_configured else "false",
        "ZOE_PI_INTENT_PREFILTER_ENABLED": prefilter_enabled,
    }
    with _temporary_env(updates):
        from pi_intent_classifier import classify_with_pi_intent_governor

        timeout_seconds = float(os.environ.get("ZOE_PI_INTENT_TIMEOUT_SECONDS") or 4.0)
        start = time.perf_counter()
        result = await classify_with_pi_intent_governor(case.text)
        latency_ms = (time.perf_counter() - start) * 1000
    timed_out = result is None and latency_ms >= (timeout_seconds * 1000 * 0.95)
    return {
        "intent": result.intent if result else None,
        "confidence": result.confidence if result else 0.0,
        "latency_ms": latency_ms,
        "correct": (result.intent if result else None) == case.expected_intent,
        "prefilter_enabled": prefilter_enabled,
        "timed_out": timed_out,
    }


async def _run_cases(
    cases: list[PiIntentEvalCase],
    *,
    transport: str,
    enable_execution: bool,
    local_model_configured: bool,
    fallback_baseline_latency_ms: float | None = None,
    extraction_failed_baseline_latency_ms: float | None = None,
    measure_zoe_agent_baseline: bool = False,
    zoe_agent_baseline_timeout_seconds: float = 30.0,
    zoe_agent_baseline_max_tokens: int = 256,
) -> tuple[list[dict[str, Any]], list[PiRouteSample]]:
    comparisons: list[dict[str, Any]] = []
    samples: list[PiRouteSample] = []
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
        pi = await _run_pi(
            case,
            transport=transport,
            enable_execution=enable_execution,
            local_model_configured=local_model_configured,
        )
        comparisons.append({"case": case.to_dict(), "zoe": zoe, "pi": pi})
        if not case.negative and case.intent_group != "chat":
            samples.append(
                PiRouteSample(
                    case_id=case.case_id,
                    intent_group=case.intent_group,
                    expected_intent=case.expected_intent,
                    zoe_intent=zoe["intent"],
                    pi_intent=pi["intent"],
                    zoe_latency_ms=float(zoe["latency_ms"]),
                    pi_latency_ms=float(pi["latency_ms"]),
                    pi_confidence=float(pi["confidence"] or 0),
                    pi_transport=transport,
                    route_class=case.route_class,
                    timed_out=bool(pi["timed_out"]),
                    metadata={
                        "baseline_kind": zoe["baseline_kind"],
                        "baseline_comparable": zoe["baseline_comparable"],
                        "baseline_timed_out": zoe["baseline_timed_out"],
                        "baseline_response_chars": zoe["baseline_response_chars"],
                        "baseline_error": zoe["baseline_error"],
                        "router_latency_ms": zoe["router_latency_ms"],
                    },
                )
            )
    return comparisons, samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report Pi intent promotion gates")
    parser.add_argument("--demo", action="store_true", help="Include deterministic demo samples for policy smoke testing")
    parser.add_argument("--run-pi", action="store_true", help="Run configured local Pi against built-in eval cases")
    parser.add_argument("--transport", choices=["print", "rpc"], default="rpc")
    parser.add_argument("--allow-execution", action="store_true", help="Temporarily set ZOE_PI_ALLOW_EXECUTION=true")
    parser.add_argument("--local-model-configured", action="store_true", help="Temporarily set ZOE_PI_LOCAL_MODEL_CONFIGURED=true")
    parser.add_argument(
        "--fallback-baseline-latency-ms",
        type=float,
        default=None,
        help="Measured Zoe fallback/agent latency to use for fallback route-class speed comparisons",
    )
    parser.add_argument(
        "--extraction-failed-baseline-latency-ms",
        type=float,
        default=None,
        help="Measured Zoe fallback latency after deterministic slot extraction fails",
    )
    parser.add_argument(
        "--measure-zoe-agent-baseline",
        action="store_true",
        help="Measure comparable Zoe Agent fallback latency for fallback/extraction_failed route classes",
    )
    parser.add_argument(
        "--zoe-agent-baseline-timeout-seconds",
        type=float,
        default=30.0,
        help="Timeout for each measured Zoe Agent fallback baseline case",
    )
    parser.add_argument(
        "--zoe-agent-baseline-max-tokens",
        type=int,
        default=256,
        help="max_tokens_override passed to Zoe Agent when measuring comparable fallback baselines",
    )
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument(
        "--cases-file",
        action="append",
        default=[],
        help="JSON or JSONL eval cases file. Repeat to combine datasets.",
    )
    parser.add_argument("--no-default-cases", action="store_true", help="Use only --cases-file datasets")
    parser.add_argument(
        "--promoted-group",
        action="append",
        choices=sorted(LOW_RISK_PI_INTENT_GROUPS),
        default=[],
        help="Intent group currently promoted through Pi; repeat for multiple groups",
    )
    args = parser.parse_args(argv)

    policy = PiPromotionPolicy(min_samples=args.min_samples)
    loaded_case_groups = [load_pi_intent_eval_cases(path) for path in args.cases_file]
    base_cases = [] if args.no_default_cases else list(DEFAULT_PI_INTENT_EVAL_CASES)
    eval_cases = merge_pi_intent_eval_cases(base_cases, *loaded_case_groups)
    comparisons: list[dict[str, Any]] = []
    samples: list[PiRouteSample] = []
    if args.demo:
        samples.extend(_demo_samples())
    if args.run_pi:
        comparisons, measured_samples = asyncio.run(
            _run_cases(
                eval_cases,
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
        samples.extend(measured_samples)
    payload = {
        "eval_case_files": args.cases_file,
        "eval_cases": eval_cases_to_dict(eval_cases),
        "eval_case_source_counts": summarize_eval_case_sources(eval_cases),
        "comparisons": comparisons,
        "promotion_report": summarize_pi_promotion(samples, policy=policy, promoted_groups=args.promoted_group),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
