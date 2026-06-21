#!/usr/bin/env python3
"""Compare Zoe's current voice command path with the Pi hybrid lab flow.

This is an operator evidence script. It does not promote Pi, alter routing,
or dispatch privileged actions. The current Zoe path is the live
/api/voice/command?stream=true endpoint and may perform the normal endpoint side effects. The Pi path reuses the existing admin-only Pi intent lab HTTP
probe.
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
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from pi_intent_lab_http_probe import (  # noqa: E402
    DEFAULT_CASES as DEFAULT_PI_HTTP_CASES,
    build_report as build_pi_http_report,
    run_probe as run_pi_http_probe,
)

DEFAULT_VOICE_CASES = (
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


def run_voice_command_probe(
    cases: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    repeat: int,
    timeout_seconds: float,
    session_id: str | None = None,
    device_token: str | None = None,
    panel_id: str = "pi-flow-comparison",
    sender: Callable[[str, Mapping[str, Any], float, Mapping[str, str]], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    endpoint = base_url.rstrip("/") + "/api/voice/command?stream=true"
    headers = _auth_headers(session_id=session_id, device_token=device_token)
    active_repeat = max(1, repeat)
    for repeat_index in range(1, active_repeat + 1):
        for raw_case in cases:
            case = _normalise_case(raw_case)
            payload = {
                "text": case["text"],
                "panel_id": panel_id,
                "session_id": f"voice-pi-flow-{case['case_id']}",
            }
            started = time.perf_counter()
            error = None
            result: Mapping[str, Any] | None = None
            try:
                if sender is not None:
                    result = sender(endpoint, payload, timeout_seconds, headers)
                else:
                    result = _post_voice_stream(endpoint, payload, timeout_seconds, headers=headers)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            http_latency_ms = (time.perf_counter() - started) * 1000
            observations.append(
                _voice_observation(
                    case,
                    repeat_index=repeat_index,
                    result=result,
                    http_latency_ms=http_latency_ms,
                    error=error,
                )
            )
    return observations


def build_report(
    voice_cases: Sequence[Mapping[str, Any]],
    voice_observations: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    repeat: int,
    pi_http_flow: Mapping[str, Any] | None = None,
    include_observations: bool = False,
) -> dict[str, Any]:
    voice_summary = {
        "overall": _voice_stats(voice_observations),
        "by_intent_group": _breakdown(voice_observations, lambda item: str(item.get("intent_group") or "unknown")),
        "by_source": _breakdown(voice_observations, lambda item: str(item.get("source") or "unknown")),
    }
    report: dict[str, Any] = {
        "report_kind": "voice_pi_flow_comparison",
        "note": (
            "Compares current Zoe voice command HTTP behavior with optional Pi hybrid lab evidence. "
            "Observation only: no promotion, no route changes. The current voice path is live endpoint behavior."
        ),
        "base_url": base_url.rstrip("/"),
        "repeat": max(1, repeat),
        "unique_case_count": len({_normalise_case(case)["case_id"] for case in voice_cases}),
        "observation_count": len(voice_observations),
        "contract": {
            "current_voice_path": "/api/voice/command?stream=true",
            "pi_path": "pi_intent_lab_http_probe" if pi_http_flow else None,
            "production_route_change": False,
            "promotion_enabled": False,
            "current_voice_side_effects": "live_voice_command_endpoint",
            "current_voice_memory_writes": "possible_when_endpoint_writes_for_effective_user",
            "pi_path_side_effects": "lab_probe_only" if pi_http_flow else None,
        },
        "voice_current": {"summary": voice_summary},
        "pi_http_flow": dict(pi_http_flow) if pi_http_flow else None,
        "comparison": _comparison(voice_summary["overall"], pi_http_flow),
    }
    if include_observations:
        report["voice_current"]["observations"] = list(voice_observations)
    return report


def _post_voice_stream(
    url: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
    *,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            raw_lines: list[dict[str, Any]] = []
            if "application/json" in content_type:
                body = response.read().decode("utf-8")
                raw_lines.append({"offset_ms": (time.perf_counter() - started) * 1000, "line": body})
            else:
                while True:
                    raw = response.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        raw_lines.append({"offset_ms": (time.perf_counter() - started) * 1000, "line": line})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
    return {"status": "ok", "content_type": content_type, "lines": raw_lines}


def _voice_observation(
    case: Mapping[str, str],
    *,
    repeat_index: int,
    result: Mapping[str, Any] | None,
    http_latency_ms: float,
    error: str | None,
) -> dict[str, Any]:
    parsed = _parse_voice_result(result)
    final_text = parsed.get("final_text") or ""
    processing_text = parsed.get("processing_ack_text") or ""
    final_completion_latency_ms = parsed.get("final_completion_latency_ms") if final_text else None
    return {
        "case_id": case["case_id"],
        "repeat_index": repeat_index,
        "intent_group": case["intent_group"],
        "source": case["source"],
        "expected_intent": case["expected_intent"],
        "request_error": error,
        "http_latency_ms": http_latency_ms,
        "transport": parsed.get("transport"),
        "processing_ack_available": bool(processing_text),
        "processing_ack_text": processing_text,
        "processing_ack_latency_ms": parsed.get("processing_ack_latency_ms"),
        "final_response_available": bool(final_text),
        "final_response_preview": _preview(final_text, limit=240),
        "final_completion_latency_ms": final_completion_latency_ms,
        "done_seen": bool(parsed.get("done_seen")),
        "route_intent": parsed.get("intent"),
        "raw_event_count": parsed.get("raw_event_count", 0),
    }


def _parse_voice_result(result: Mapping[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"transport": "none", "raw_event_count": 0}
    lines = list(result.get("lines") or [])
    parsed_events: list[dict[str, Any]] = []
    for item in lines:
        line = str((item or {}).get("line") or "").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, Mapping):
            parsed_events.append({"offset_ms": item.get("offset_ms"), "event": dict(event)})

    if len(parsed_events) == 1:
        event = parsed_events[0]["event"]
        if "reply" in event or "ok" in event:
            return {
                "transport": "json",
                "final_text": str(event.get("reply") or ""),
                "final_completion_latency_ms": parsed_events[0].get("offset_ms"),
                "intent": event.get("intent"),
                "done_seen": True,
                "raw_event_count": len(parsed_events),
            }

    processing_text = ""
    processing_latency = None
    final_text = ""
    final_latency = None
    done_seen = False
    intent = None
    for parsed in parsed_events:
        event = parsed["event"]
        offset = parsed.get("offset_ms")
        if event.get("processing_ack") and not processing_text:
            processing_text = str(event.get("text") or "")
            processing_latency = _float_or_none(offset)
        if event.get("done"):
            done_seen = True
            final_text = str(event.get("reply") or final_text)
            final_latency = _float_or_none(offset)
        elif event.get("text") and not event.get("processing_ack"):
            final_text = str(event.get("text") or final_text)
            final_latency = _float_or_none(offset)
        if event.get("intent"):
            intent = event.get("intent")
    return {
        "transport": "stream",
        "processing_ack_text": processing_text,
        "processing_ack_latency_ms": processing_latency,
        "final_text": final_text,
        "final_completion_latency_ms": final_latency,
        "intent": intent,
        "done_seen": done_seen,
        "raw_event_count": len(parsed_events),
    }


def _voice_stats(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {
            "observation_count": 0,
            "unique_case_count": 0,
            "request_error_rate": None,
            "processing_ack_rate": None,
            "final_response_rate": None,
            "http_latency_ms": _latency_stats([]),
            "processing_ack_latency_ms": _latency_stats([]),
            "final_completion_latency_ms": _latency_stats([]),
        }
    return {
        "observation_count": len(observations),
        "unique_case_count": len({str(item.get("case_id")) for item in observations}),
        "request_error_rate": _rate(bool(item.get("request_error")) for item in observations),
        "processing_ack_rate": _rate(bool(item.get("processing_ack_available")) for item in observations),
        "final_response_rate": _rate(bool(item.get("final_response_available")) for item in observations),
        "http_latency_ms": _latency_stats(_floats(item.get("http_latency_ms") for item in observations)),
        "processing_ack_latency_ms": _latency_stats(
            _floats(item.get("processing_ack_latency_ms") for item in observations)
        ),
        "final_completion_latency_ms": _latency_stats(
            _floats(item.get("final_completion_latency_ms") for item in observations)
        ),
    }


def _comparison(voice_overall: Mapping[str, Any], pi_http_flow: Mapping[str, Any] | None) -> dict[str, Any]:
    voice_final_p95 = ((voice_overall.get("final_completion_latency_ms") or {}).get("p95"))
    voice_ack_p95 = ((voice_overall.get("processing_ack_latency_ms") or {}).get("p95"))
    if not pi_http_flow:
        return {
            "state": "voice_only_measured",
            "voice_final_p95_ms": voice_final_p95,
            "voice_processing_ack_p95_ms": voice_ack_p95,
            "production_route_change": False,
        }
    pi_overall = ((pi_http_flow.get("summary") or {}).get("overall")) or {}
    pi_final_p95 = ((pi_overall.get("final_completion_latency_ms") or {}).get("p95"))
    pi_cue_p95 = ((pi_overall.get("cue_latency_ms") or {}).get("p95"))
    voice_final_ready = isinstance(voice_final_p95, (int, float))
    pi_final_ready = isinstance(pi_final_p95, (int, float))
    return {
        "state": (
            "comparison_observed"
            if voice_final_ready and pi_final_ready
            else "pi_observed_voice_unavailable"
            if pi_final_ready
            else "voice_observed_pi_unavailable"
            if voice_final_ready
            else "both_unavailable"
        ),
        "voice_final_p95_ms": voice_final_p95,
        "voice_processing_ack_p95_ms": voice_ack_p95,
        "pi_final_p95_ms": pi_final_p95,
        "pi_cue_p95_ms": pi_cue_p95,
        "pi_minus_voice_final_p95_ms": (pi_final_p95 - voice_final_p95) if voice_final_ready and pi_final_ready else None,
        "production_route_change": False,
    }


def _breakdown(
    observations: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in observations:
        grouped[key_fn(item)].append(item)
    return {key: _voice_stats(values) for key, values in sorted(grouped.items())}


def _auth_headers(*, session_id: str | None, device_token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if session_id:
        headers["X-Session-ID"] = session_id
    if device_token:
        headers["X-Device-Token"] = device_token
    return headers


def _normalise_case(raw: Mapping[str, Any]) -> dict[str, str]:
    return {
        "case_id": str(raw.get("case_id") or raw.get("id") or raw.get("text") or "case"),
        "text": str(raw.get("text") or ""),
        "expected_intent": str(raw.get("expected_intent") or ""),
        "intent_group": str(raw.get("intent_group") or raw.get("expected_intent") or "unknown"),
        "source": str(raw.get("source") or "synthetic"),
    }


def _load_cases(paths: Sequence[str], *, no_default_cases: bool) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = [] if no_default_cases else [_normalise_case(item) for item in DEFAULT_VOICE_CASES]
    for path in paths:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else [raw]
        cases.extend(_normalise_case(item) for item in rows)
    return cases


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
    return [float(value) for value in values if isinstance(value, (int, float))]


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _preview(text: str, *, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare current voice command flow with Pi hybrid lab flow")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases-file", action="append", default=[])
    parser.add_argument("--no-default-cases", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--session-id")
    parser.add_argument("--device-token")
    parser.add_argument("--panel-id", default="pi-flow-comparison")
    parser.add_argument("--include-pi-http-flow", action="store_true")
    parser.add_argument("--include-safe-fulfillment", action="store_true")
    parser.add_argument("--allow-pi-execution", action="store_true")
    parser.add_argument("--local-model-configured", action="store_true")
    parser.add_argument("--pi-http-timeout-seconds", type=float, default=12.0)
    parser.add_argument("--pi-http-request-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--natural-final-max-ms", type=float, default=4500.0)
    parser.add_argument("--include-observations", action="store_true")
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases_file, no_default_cases=args.no_default_cases)
    voice_observations = run_voice_command_probe(
        cases,
        base_url=args.base_url,
        repeat=args.repeat,
        timeout_seconds=args.timeout_seconds,
        session_id=args.session_id,
        device_token=args.device_token,
        panel_id=args.panel_id,
    )
    pi_http_flow = None
    if args.include_pi_http_flow:
        pi_observations = run_pi_http_probe(
            DEFAULT_PI_HTTP_CASES,
            base_url=args.base_url,
            repeat=args.repeat,
            run_pi=True,
            include_safe_fulfillment=args.include_safe_fulfillment,
            allow_pi_execution=args.allow_pi_execution,
            local_model_configured=args.local_model_configured,
            timeout_seconds=args.pi_http_timeout_seconds,
            request_timeout_seconds=args.pi_http_request_timeout_seconds,
            session_id=args.session_id,
            device_token=args.device_token,
            wake_ack_text="",
            natural_final_max_ms=args.natural_final_max_ms,
        )
        pi_http_flow = build_pi_http_report(
            DEFAULT_PI_HTTP_CASES,
            pi_observations,
            base_url=args.base_url,
            repeat=args.repeat,
            run_pi=True,
            include_safe_fulfillment=args.include_safe_fulfillment,
            natural_final_max_ms=args.natural_final_max_ms,
        )
    print(
        json.dumps(
            build_report(
                cases,
                voice_observations,
                base_url=args.base_url,
                repeat=args.repeat,
                pi_http_flow=pi_http_flow,
                include_observations=args.include_observations,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
