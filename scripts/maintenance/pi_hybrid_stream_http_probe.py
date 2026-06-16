#!/usr/bin/env python3
"""Probe the Pi hybrid-stream lab endpoint over HTTP.

This measures the real cue-first NDJSON transport: client-visible cue arrival,
client-visible final/error arrival, Pi accuracy, and optional read-only safe
fulfillment. It is observation evidence only; it does not promote routes.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_intent_lab import SAFE_FULFILLMENT_INTENTS  # noqa: E402
from zoe_pi_promotion import (  # noqa: E402
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    eval_cases_to_dict,
    load_pi_intent_eval_cases,
    merge_pi_intent_eval_cases,
    summarize_eval_case_sources,
)

DEFAULT_NATURAL_CUE_MAX_MS = 250.0
DEFAULT_NATURAL_FINAL_MAX_MS = 4500.0


def run_probe(
    cases: Sequence[PiIntentEvalCase],
    *,
    base_url: str,
    repeat: int,
    run_pi: bool,
    include_safe_fulfillment: bool,
    allow_pi_execution: bool,
    local_model_configured: bool,
    timeout_seconds: float,
    request_timeout_seconds: float | None = None,
    session_id: str | None = None,
    device_token: str | None = None,
    natural_cue_max_ms: float = DEFAULT_NATURAL_CUE_MAX_MS,
    natural_final_max_ms: float = DEFAULT_NATURAL_FINAL_MAX_MS,
    stream_post: Callable[[str, Mapping[str, Any], float], Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    endpoint = _endpoint_url(base_url)
    active_repeat = max(1, repeat)
    auth_headers = _auth_headers(session_id=session_id, device_token=device_token)
    if stream_post is not None and auth_headers:
        raise ValueError("session_id/device_token require the default HTTP sender")
    if request_timeout_seconds is not None and request_timeout_seconds >= timeout_seconds:
        raise ValueError("request_timeout_seconds must be less than timeout_seconds")

    def send(url: str, payload: Mapping[str, Any], timeout: float) -> Sequence[Mapping[str, Any]]:
        if stream_post is not None:
            return stream_post(url, payload, timeout)
        return _post_stream_json(url, payload, timeout, headers=auth_headers)

    for repeat_index in range(1, active_repeat + 1):
        for case in cases:
            case.validate()
            payload = {
                "text": case.text,
                "run_pi": run_pi,
                "pi_transport": "rpc",
                "allow_pi_execution": allow_pi_execution,
                "local_model_configured": local_model_configured,
                "include_hybrid_status": False,
                "include_safe_fulfillment": include_safe_fulfillment,
            }
            if request_timeout_seconds is not None:
                payload["request_timeout_seconds"] = request_timeout_seconds
            started = time.perf_counter()
            error = None
            events: Sequence[Mapping[str, Any]] = []
            try:
                events = send(endpoint, payload, timeout_seconds)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            http_latency_ms = (time.perf_counter() - started) * 1000
            observations.append(
                _observation(
                    case,
                    repeat_index=repeat_index,
                    events=events,
                    http_latency_ms=http_latency_ms,
                    error=error,
                    natural_cue_max_ms=natural_cue_max_ms,
                    natural_final_max_ms=natural_final_max_ms,
                )
            )
    return observations


def build_report(
    cases: Sequence[PiIntentEvalCase],
    observations: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    repeat: int,
    run_pi: bool,
    include_safe_fulfillment: bool,
    include_observations: bool = False,
    natural_cue_max_ms: float = DEFAULT_NATURAL_CUE_MAX_MS,
    natural_final_max_ms: float = DEFAULT_NATURAL_FINAL_MAX_MS,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "report_kind": "pi_hybrid_stream_http_probe",
        "note": (
            "HTTP probe of the admin Pi hybrid-stream lab endpoint. Measures real NDJSON cue-first transport; "
            "results are observation evidence, not promotion samples."
        ),
        "base_url": base_url.rstrip("/"),
        "endpoint": _endpoint_url(base_url),
        "repeat": max(1, repeat),
        "unique_case_count": len({case.case_id for case in cases}),
        "observation_count": len(observations),
        "pi_ran": run_pi,
        "safe_fulfillment_enabled": include_safe_fulfillment,
        "safe_fulfillment_side_effects": "read_only_external_only" if include_safe_fulfillment else "none",
        "conversation_contract": {
            "strategy": "processing_cue_packet_then_final_or_error_packet",
            "transport": "application/x-ndjson",
            "natural_cue_max_ms": natural_cue_max_ms,
            "natural_final_max_ms": natural_final_max_ms,
            "production_route_change": False,
            "memory_writes_enabled": False,
            "shadow_writes_enabled": False,
            "promotion_enabled": False,
        },
        "eval_case_source_counts": summarize_eval_case_sources(cases),
        "eval_cases": eval_cases_to_dict(cases),
        "summary": {
            "overall": _stats(observations, pi_ran=run_pi),
            "by_intent_group": _breakdown(
                observations,
                lambda item: str(item.get("intent_group") or "unknown"),
                pi_ran=run_pi,
            ),
            "by_source": _breakdown(observations, lambda item: str(item.get("source") or "unknown"), pi_ran=run_pi),
        },
    }
    if include_observations:
        payload["observations"] = list(observations)
    return payload


def _post_stream_json(
    url: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
    *,
    headers: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            for raw in response:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                event = json.loads(line)
                event["client_received_ms"] = (time.perf_counter() - started) * 1000
                events.append(event)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
    return events


def _observation(
    case: PiIntentEvalCase,
    *,
    repeat_index: int,
    events: Sequence[Mapping[str, Any]],
    http_latency_ms: float,
    error: str | None,
    natural_cue_max_ms: float,
    natural_final_max_ms: float,
) -> dict[str, Any]:
    cue_event = _first_event(events, "processing_cue")
    final_event = _first_event(events, "final")
    error_event = _first_event(events, "error")
    result = final_event.get("result") if isinstance(final_event.get("result"), Mapping) else {}
    pi = result.get("pi") if isinstance(result.get("pi"), Mapping) else {}
    safe = result.get("safe_fulfillment") if isinstance(result.get("safe_fulfillment"), Mapping) else {}
    flow = result.get("simulated_hybrid_flow") if isinstance(result.get("simulated_hybrid_flow"), Mapping) else {}
    cue = cue_event.get("cue") if isinstance(cue_event.get("cue"), Mapping) else {}
    pi_intent = pi.get("intent")
    expected = case.expected_intent
    response_preview = str(safe.get("response_preview") or "")
    safe_success = bool(
        safe.get("attempted")
        and safe.get("allowed")
        and not safe.get("timed_out")
        and not safe.get("error")
        and int(safe.get("response_chars") or 0) > 0
    )
    cue_client_ms = _float_or_none(cue_event.get("client_received_ms"))
    final_client_ms = _float_or_none(final_event.get("client_received_ms"))
    error_client_ms = _float_or_none(error_event.get("client_received_ms"))
    terminal_client_ms = final_client_ms if final_client_ms is not None else error_client_ms
    stream_error = error or _stream_error_text(error_event)
    cue_within_budget = cue_client_ms is not None and cue_client_ms <= natural_cue_max_ms
    final_packet_within_budget = final_client_ms is not None and final_client_ms <= natural_final_max_ms
    conversation_final_within_budget = bool(response_preview) and final_packet_within_budget
    event_names = [str(event.get("event") or "") for event in events]
    return {
        "case_id": case.case_id,
        "repeat_index": repeat_index,
        "intent_group": case.intent_group,
        "route_class": case.route_class,
        "source": case.source,
        "negative": case.negative,
        "expected_intent": expected,
        "http_latency_ms": http_latency_ms,
        "event_names": event_names,
        "request_error": stream_error,
        "cue_packet_available": bool(cue_event),
        "final_packet_available": bool(final_event),
        "error_packet_available": bool(error_event),
        "cue_text": str(cue.get("text") or ""),
        "cue_client_latency_ms": cue_client_ms,
        "cue_server_elapsed_ms": _float_or_none(cue_event.get("elapsed_ms")),
        "final_client_latency_ms": final_client_ms,
        "final_server_elapsed_ms": _float_or_none(final_event.get("elapsed_ms")),
        "terminal_client_latency_ms": terminal_client_ms,
        "pi_intent": pi_intent,
        "pi_correct": pi_intent == expected,
        "pi_confidence": _float_or_none(pi.get("confidence")),
        "pi_latency_ms": _float_or_none(pi.get("latency_ms")),
        "pi_timed_out": bool(pi.get("timed_out")),
        "pi_error": pi.get("error"),
        "safe_fulfillment_requested": bool(safe.get("requested")),
        "safe_fulfillment_attempted": bool(safe.get("attempted")),
        "safe_fulfillment_allowed": bool(safe.get("allowed")),
        "safe_fulfillment_success": safe_success,
        "safe_fulfillment_latency_ms": _float_or_none(safe.get("latency_ms")),
        "safe_fulfillment_error": safe.get("error"),
        "safe_fulfillment_timed_out": bool(safe.get("timed_out")),
        "simulated_final_completion_latency_ms": _float_or_none(flow.get("final_completion_latency_ms")),
        "response_preview": response_preview,
        "cue_within_budget": cue_within_budget,
        "final_packet_within_budget": final_packet_within_budget,
        "conversation_final_within_budget": conversation_final_within_budget,
        "stream_natural_candidate": bool(not stream_error and cue_within_budget and final_packet_within_budget),
        "conversation_natural_candidate": bool(
            not stream_error and cue_within_budget and conversation_final_within_budget and response_preview
        ),
        "production_route_change": False,
    }


def _stats(observations: Sequence[Mapping[str, Any]], *, pi_ran: bool) -> dict[str, Any]:
    if not observations:
        return {
            "observation_count": 0,
            "unique_case_count": 0,
            "request_error_rate": None,
            "cue_packet_rate": None,
            "final_packet_rate": None,
            "error_packet_rate": None,
            "pi_accuracy": None,
            "negative_false_positive_rate": None,
            "pi_timeout_rate": None,
            "stream_natural_rate": None,
            "conversation_natural_rate": None,
            "safe_fulfillment_success_rate": None,
            "cue_client_latency_ms": _latency_stats([]),
            "final_client_latency_ms": _latency_stats([]),
            "pi_latency_ms": _latency_stats([]),
            "safe_fulfillment_latency_ms": _latency_stats([]),
        }
    negative_items = [item for item in observations if item.get("negative")]
    safe_requested = [item for item in observations if item.get("safe_fulfillment_requested")]
    return {
        "observation_count": len(observations),
        "unique_case_count": len({str(item.get("case_id")) for item in observations}),
        "request_error_rate": _rate(bool(item.get("request_error")) for item in observations),
        "cue_packet_rate": _rate(bool(item.get("cue_packet_available")) for item in observations),
        "final_packet_rate": _rate(bool(item.get("final_packet_available")) for item in observations),
        "error_packet_rate": _rate(bool(item.get("error_packet_available")) for item in observations),
        "pi_accuracy": _rate(bool(item.get("pi_correct")) for item in observations) if pi_ran else None,
        "negative_false_positive_rate": (
            _rate(bool(item.get("pi_intent")) for item in negative_items) if negative_items else None
        ),
        "pi_timeout_rate": _rate(bool(item.get("pi_timed_out")) for item in observations) if pi_ran else None,
        "stream_natural_rate": _rate(bool(item.get("stream_natural_candidate")) for item in observations),
        "conversation_natural_rate": _rate(bool(item.get("conversation_natural_candidate")) for item in observations),
        "safe_fulfillment_success_rate": (
            _rate(bool(item.get("safe_fulfillment_success")) for item in safe_requested) if safe_requested else None
        ),
        "cue_client_latency_ms": _latency_stats(_floats(item.get("cue_client_latency_ms") for item in observations)),
        "final_client_latency_ms": _latency_stats(_floats(item.get("final_client_latency_ms") for item in observations)),
        "pi_latency_ms": _latency_stats(_floats(item.get("pi_latency_ms") for item in observations)),
        "safe_fulfillment_latency_ms": _latency_stats(
            _floats(item.get("safe_fulfillment_latency_ms") for item in observations)
        ),
    }


def _breakdown(
    observations: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
    *,
    pi_ran: bool,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in observations:
        grouped.setdefault(key_fn(item), []).append(item)
    return {key: _stats(values, pi_ran=pi_ran) for key, values in sorted(grouped.items())}


def _first_event(events: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    return next((event for event in events if event.get("event") == name), {})


def _stream_error_text(error_event: Mapping[str, Any]) -> str | None:
    if not error_event:
        return None
    parts = [str(error_event.get("error_type") or "stream_error")]
    if error_event.get("error"):
        parts.append(str(error_event.get("error")))
    return ": ".join(parts)


def _auth_headers(*, session_id: str | None, device_token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if session_id:
        headers["X-Session-ID"] = session_id
    if device_token:
        headers["X-Device-Token"] = device_token
    return headers


def _endpoint_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/pi-intent-lab/hybrid-stream"


def _load_cases(paths: Sequence[str], *, no_default_cases: bool) -> list[PiIntentEvalCase]:
    loaded_case_groups = [load_pi_intent_eval_cases(path) for path in paths]
    base_cases = [] if no_default_cases else list(DEFAULT_PI_INTENT_EVAL_CASES)
    return merge_pi_intent_eval_cases(base_cases, *loaded_case_groups)


def _select_cases(
    cases: Sequence[PiIntentEvalCase],
    *,
    case_ids: Sequence[str] = (),
    intent_groups: Sequence[str] = (),
    safe_fulfillment_eligible_only: bool = False,
) -> list[PiIntentEvalCase]:
    wanted_ids = {item for item in case_ids if item}
    wanted_groups = {item for item in intent_groups if item}
    selected: list[PiIntentEvalCase] = []
    for case in cases:
        case.validate()
        if wanted_ids and case.case_id not in wanted_ids:
            continue
        if wanted_groups and case.intent_group not in wanted_groups:
            continue
        if safe_fulfillment_eligible_only and case.expected_intent not in SAFE_FULFILLMENT_INTENTS:
            continue
        selected.append(case)
    missing_ids = sorted(wanted_ids - {case.case_id for case in selected})
    if missing_ids:
        raise ValueError(f"unknown or filtered case_id values: {', '.join(missing_ids)}")
    return selected


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe /api/pi-intent-lab/hybrid-stream over HTTP")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases-file", action="append", default=[])
    parser.add_argument("--no-default-cases", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case-id", action="append", default=[], help="Limit the run to one or more case IDs.")
    parser.add_argument("--intent-group", action="append", default=[], help="Limit the run to one or more intent groups.")
    parser.add_argument(
        "--safe-fulfillment-eligible-only",
        action="store_true",
        help="Keep only cases whose expected intent is in the lab read-only safe-fulfillment allowlist.",
    )
    parser.add_argument("--run-pi", action="store_true")
    parser.add_argument("--allow-pi-execution", action="store_true")
    parser.add_argument("--local-model-configured", action="store_true")
    parser.add_argument("--include-safe-fulfillment", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=18.0,
        help="Server-side lab timeout; must be lower than --timeout-seconds so stream errors are observable.",
    )
    parser.add_argument("--session-id")
    parser.add_argument("--device-token")
    parser.add_argument("--include-observations", action="store_true")
    parser.add_argument("--natural-cue-max-ms", type=float, default=DEFAULT_NATURAL_CUE_MAX_MS)
    parser.add_argument("--natural-final-max-ms", type=float, default=DEFAULT_NATURAL_FINAL_MAX_MS)
    args = parser.parse_args(argv)

    cases = _select_cases(
        _load_cases(args.cases_file, no_default_cases=args.no_default_cases),
        case_ids=args.case_id,
        intent_groups=args.intent_group,
        safe_fulfillment_eligible_only=args.safe_fulfillment_eligible_only,
    )
    observations = run_probe(
        cases,
        base_url=args.base_url,
        repeat=args.repeat,
        run_pi=args.run_pi,
        include_safe_fulfillment=args.include_safe_fulfillment,
        allow_pi_execution=args.allow_pi_execution,
        local_model_configured=args.local_model_configured,
        timeout_seconds=args.timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        session_id=args.session_id,
        device_token=args.device_token,
        natural_cue_max_ms=args.natural_cue_max_ms,
        natural_final_max_ms=args.natural_final_max_ms,
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
                natural_cue_max_ms=args.natural_cue_max_ms,
                natural_final_max_ms=args.natural_final_max_ms,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
