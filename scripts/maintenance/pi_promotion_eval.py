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
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from zoe_pi_promotion import (  # noqa: E402
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    PiPromotionPolicy,
    PiRouteSample,
    eval_cases_to_dict,
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


async def _run_zoe_baseline(case: PiIntentEvalCase) -> dict[str, Any]:
    with _temporary_env({"ZOE_PI_INTENT_ENABLED": "false"}):
        from intent_router import detect_and_extract_intent

        start = time.perf_counter()
        intent = await detect_and_extract_intent(case.text)
        latency_ms = (time.perf_counter() - start) * 1000
    return {
        "intent": intent.name if intent else None,
        "confidence": getattr(intent, "confidence", None) if intent else None,
        "latency_ms": latency_ms,
        "correct": (intent.name if intent else None) == case.expected_intent,
    }


async def _run_pi(case: PiIntentEvalCase, *, transport: str, enable_execution: bool, local_model_configured: bool) -> dict[str, Any]:
    updates = {
        "ZOE_PI_INTENT_ENABLED": "true",
        "ZOE_PI_INTENT_TRANSPORT": transport,
        "ZOE_PI_ALLOW_EXECUTION": "true" if enable_execution else "false",
        "ZOE_PI_LOCAL_MODEL_CONFIGURED": "true" if local_model_configured else "false",
    }
    with _temporary_env(updates):
        from pi_intent_classifier import classify_with_pi_intent_governor

        start = time.perf_counter()
        result = await classify_with_pi_intent_governor(case.text)
        latency_ms = (time.perf_counter() - start) * 1000
    return {
        "intent": result.intent if result else None,
        "confidence": result.confidence if result else 0.0,
        "latency_ms": latency_ms,
        "correct": (result.intent if result else None) == case.expected_intent,
        "timed_out": result is None,
    }


async def _run_cases(*, transport: str, enable_execution: bool, local_model_configured: bool) -> tuple[list[dict[str, Any]], list[PiRouteSample]]:
    comparisons: list[dict[str, Any]] = []
    samples: list[PiRouteSample] = []
    for case in DEFAULT_PI_INTENT_EVAL_CASES:
        case.validate()
        zoe = await _run_zoe_baseline(case)
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
                )
            )
    return comparisons, samples


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Pi intent promotion gates")
    parser.add_argument("--demo", action="store_true", help="Include deterministic demo samples for policy smoke testing")
    parser.add_argument("--run-pi", action="store_true", help="Run configured local Pi against built-in eval cases")
    parser.add_argument("--transport", choices=["print", "rpc"], default="rpc")
    parser.add_argument("--allow-execution", action="store_true", help="Temporarily set ZOE_PI_ALLOW_EXECUTION=true")
    parser.add_argument("--local-model-configured", action="store_true", help="Temporarily set ZOE_PI_LOCAL_MODEL_CONFIGURED=true")
    parser.add_argument("--min-samples", type=int, default=30)
    args = parser.parse_args()

    policy = PiPromotionPolicy(min_samples=args.min_samples)
    comparisons: list[dict[str, Any]] = []
    samples: list[PiRouteSample] = []
    if args.demo:
        samples.extend(_demo_samples())
    if args.run_pi:
        comparisons, measured_samples = asyncio.run(
            _run_cases(
                transport=args.transport,
                enable_execution=args.allow_execution,
                local_model_configured=args.local_model_configured,
            )
        )
        samples.extend(measured_samples)
    payload = {
        "eval_cases": eval_cases_to_dict(DEFAULT_PI_INTENT_EVAL_CASES),
        "comparisons": comparisons,
        "promotion_report": summarize_pi_promotion(samples, policy=policy),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
