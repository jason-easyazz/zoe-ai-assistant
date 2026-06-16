#!/usr/bin/env python3
"""Probe the admin Pi intent lab endpoint over HTTP.

This measures the production-like FastAPI path for cue -> Pi -> optional safe
fulfillment. It is observation evidence only; it does not promote routes.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


DEFAULT_CASES = (
    {
        "case_id": "weather_jacket",
        "text": "need a jacket tonight",
        "expected_intent": "weather",
        "intent_group": "weather",
        "source": "synthetic",
    },
    {
        "case_id": "weather_rain",
        "text": "rain later",
        "expected_intent": "weather",
        "intent_group": "weather",
        "source": "synthetic",
    },
)


def run_probe(
    cases: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    repeat: int,
    run_pi: bool,
    include_safe_fulfillment: bool,
    allow_pi_execution: bool,
    local_model_configured: bool,
    timeout_seconds: float,
    post_json: Callable[[str, Mapping[str, Any], float], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    active_repeat = max(1, repeat)
    endpoint = _endpoint_url(base_url)
    sender = post_json or _post_json
    for repeat_index in range(1, active_repeat + 1):
        for raw_case in cases:
            case = _normalise_case(raw_case)
            payload = {
                "text": case["text"],
                "run_pi": run_pi,
                "allow_pi_execution": allow_pi_execution,
                "local_model_configured": local_model_configured,
                "include_hybrid_status": False,
                "include_safe_fulfillment": include_safe_fulfillment,
            }
            started = time.perf_counter()
            error = None
            result: Mapping[str, Any] | None = None
            try:
                result = sender(endpoint, payload, timeout_seconds)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            http_latency_ms = (time.perf_counter() - started) * 1000
            observations.append(
                _observation(
                    case,
                    repeat_index=repeat_index,
                    result=result,
                    http_latency_ms=http_latency_ms,
                    error=error,
                )
            )
    return observations


def build_report(
    cases: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    repeat: int,
    run_pi: bool,
    include_safe_fulfillment: bool,
    include_observations: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "report_kind": "pi_intent_lab_http_probe",
        "note": (
            "HTTP probe of the admin Pi intent lab endpoint. Measures production-like FastAPI startup path; "
            "results are observation evidence, not promotion samples."
        ),
        "base_url": base_url.rstrip("/"),
        "repeat": max(1, repeat),
        "unique_case_count": len({_normalise_case(case)["case_id"] for case in cases}),
        "observation_count": len(observations),
        "pi_ran": run_pi,
        "safe_fulfillment_enabled": include_safe_fulfillment,
        "safe_fulfillment_side_effects": "read_only_external_only" if include_safe_fulfillment else "none",
        "summary": {
            "overall": _stats(observations),
            "by_intent_group": _breakdown(observations, lambda item: str(item.get("intent_group") or "unknown")),
            "by_source": _breakdown(observations, lambda item: str(item.get("source") or "unknown")),
        },
    }
    if include_observations:
        payload["observations"] = list(observations)
    return payload


def _post_json(url: str, payload: Mapping[str, Any], timeout_seconds: float) -> Mapping[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
    return json.loads(raw)


def _observation(
    case: Mapping[str, str],
    *,
    repeat_index: int,
    result: Mapping[str, Any] | None,
    http_latency_ms: float,
    error: str | None,
) -> dict[str, Any]:
    if result is None:
        return {
            "case_id": case["case_id"],
            "repeat_index": repeat_index,
            "intent_group": case["intent_group"],
            "source": case["source"],
            "expected_intent": case["expected_intent"],
            "http_latency_ms": http_latency_ms,
            "request_error": error,
            "natural_flow_candidate": False,
        }
    pi = result.get("pi") or {}
    safe = result.get("safe_fulfillment") or {}
    flow = result.get("simulated_hybrid_flow") or {}
    expected = case["expected_intent"]
    safe_success = bool(
        safe.get("attempted")
        and safe.get("allowed")
        and not safe.get("timed_out")
        and not safe.get("error")
        and int(safe.get("response_chars") or 0) > 0
    )
    return {
        "case_id": case["case_id"],
        "repeat_index": repeat_index,
        "intent_group": case["intent_group"],
        "source": case["source"],
        "expected_intent": expected,
        "http_latency_ms": http_latency_ms,
        "request_error": error,
        "contract_side_effects": (result.get("contract") or {}).get("side_effects"),
        "zoe_router_intent": (result.get("zoe_router") or {}).get("intent"),
        "pi_intent": pi.get("intent"),
        "pi_correct": pi.get("intent") == expected,
        "pi_confidence": _float_or_none(pi.get("confidence")),
        "pi_latency_ms": _float_or_none(pi.get("latency_ms")),
        "pi_timed_out": bool(pi.get("timed_out")),
        "pi_error": pi.get("error"),
        "cue_latency_ms": _float_or_none(flow.get("cue_latency_ms")),
        "safe_fulfillment_attempted": bool(safe.get("attempted")),
        "safe_fulfillment_allowed": bool(safe.get("allowed")),
        "safe_fulfillment_success": safe_success,
        "safe_fulfillment_latency_ms": _float_or_none(safe.get("latency_ms")),
        "safe_fulfillment_error": safe.get("error"),
        "safe_fulfillment_timed_out": bool(safe.get("timed_out")),
        "final_completion_latency_ms": _float_or_none(flow.get("final_completion_latency_ms")),
        "natural_flow_candidate": bool(flow.get("natural_flow_candidate")),
        "response_preview": safe.get("response_preview") or "",
    }


def _breakdown(
    observations: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in observations:
        grouped.setdefault(key_fn(item), []).append(item)
    return {key: _stats(values) for key, values in sorted(grouped.items())}


def _stats(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {
            "observation_count": 0,
            "unique_case_count": 0,
            "request_error_rate": None,
            "pi_accuracy": None,
            "pi_timeout_rate": None,
            "natural_flow_rate": None,
            "safe_fulfillment_success_rate": None,
            "http_latency_ms": _latency_stats([]),
            "cue_latency_ms": _latency_stats([]),
            "pi_latency_ms": _latency_stats([]),
            "safe_fulfillment_latency_ms": _latency_stats([]),
            "final_completion_latency_ms": _latency_stats([]),
        }
    return {
        "observation_count": len(observations),
        "unique_case_count": len({str(item.get("case_id")) for item in observations}),
        "request_error_rate": _rate(bool(item.get("request_error")) for item in observations),
        "pi_accuracy": _rate(bool(item.get("pi_correct")) for item in observations if item.get("pi_intent") is not None),
        "pi_timeout_rate": _rate(bool(item.get("pi_timed_out")) for item in observations if item.get("pi_intent") is not None),
        "natural_flow_rate": _rate(bool(item.get("natural_flow_candidate")) for item in observations),
        "safe_fulfillment_success_rate": _rate(bool(item.get("safe_fulfillment_success")) for item in observations),
        "http_latency_ms": _latency_stats(_floats(item.get("http_latency_ms") for item in observations)),
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


def _rate(values: Iterable[bool]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(1 for item in items if item) / len(items)


def _floats(values: Iterable[Any]) -> list[float]:
    floats: list[float] = []
    for value in values:
        if isinstance(value, (int, float)):
            floats.append(float(value))
    return floats


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _endpoint_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/pi-intent-lab/compare"


def _normalise_case(raw: Mapping[str, Any]) -> dict[str, str]:
    return {
        "case_id": str(raw.get("case_id") or raw.get("id") or raw.get("text") or "case"),
        "text": str(raw.get("text") or ""),
        "expected_intent": str(raw.get("expected_intent") or ""),
        "intent_group": str(raw.get("intent_group") or raw.get("expected_intent") or "unknown"),
        "source": str(raw.get("source") or "synthetic"),
    }


def _load_cases(paths: Sequence[str], *, no_default_cases: bool) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = [] if no_default_cases else [_normalise_case(item) for item in DEFAULT_CASES]
    for path in paths:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else [raw]
        cases.extend(_normalise_case(item) for item in rows)
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe /api/pi-intent-lab/compare over HTTP")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases-file", action="append", default=[])
    parser.add_argument("--no-default-cases", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--run-pi", action="store_true")
    parser.add_argument("--allow-pi-execution", action="store_true")
    parser.add_argument("--local-model-configured", action="store_true")
    parser.add_argument("--include-safe-fulfillment", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    parser.add_argument("--include-observations", action="store_true")
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases_file, no_default_cases=args.no_default_cases)
    observations = run_probe(
        cases,
        base_url=args.base_url,
        repeat=args.repeat,
        run_pi=args.run_pi,
        include_safe_fulfillment=args.include_safe_fulfillment,
        allow_pi_execution=args.allow_pi_execution,
        local_model_configured=args.local_model_configured,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            build_report(
                cases,
                observations,
                base_url=args.base_url,
                repeat=args.repeat,
                run_pi=args.run_pi,
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
