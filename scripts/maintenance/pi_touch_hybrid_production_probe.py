#!/usr/bin/env python3
"""Probe Zoe's production Pi hybrid path through the touch-panel voice loop.

This script exercises the real non-stream `/api/voice/command` production path
while a panel websocket is connected. It measures whether the touch UI can see
Zoe's instant intent-buffer cue before the final Pi-backed answer completes.
It is observation-only: no promotion, no label writes, and no route changes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover - CLI reports missing dependency cleanly.
    websocket = None

DEFAULT_CASES = (
    {
        "case_id": "weather_rain_later",
        "text": "will it rain later",
        "expected_intent": "weather",
        "intent_group": "weather",
        "source": "production_smoke",
    },
    {
        "case_id": "daily_briefing",
        "text": "give me my daily briefing",
        "expected_intent": "daily_briefing",
        "intent_group": "daily_briefing",
        "source": "production_smoke",
    },
)
DEFAULT_CUE_MAX_MS = 250.0
DEFAULT_FINAL_MAX_MS = 8000.0

VoiceSender = Callable[[str, Mapping[str, Any], float, Mapping[str, str]], Mapping[str, Any]]
PanelObserver = Callable[
    [str, str, str, float, Callable[[], Mapping[str, Any]]],
    tuple[Mapping[str, Any] | None, Sequence[Mapping[str, Any]], str | None],
]


def run_probe(
    cases: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    panel_id: str,
    device_token: str,
    repeat: int,
    timeout_seconds: float,
    websocket_timeout_seconds: float,
    cue_max_ms: float = DEFAULT_CUE_MAX_MS,
    final_max_ms: float = DEFAULT_FINAL_MAX_MS,
    sender: VoiceSender | None = None,
    panel_observer: PanelObserver | None = None,
) -> list[dict[str, Any]]:
    if not device_token:
        raise ValueError("device_token is required for the production touch probe")
    if timeout_seconds <= 0 or websocket_timeout_seconds <= 0:
        raise ValueError("timeouts must be positive")
    observations: list[dict[str, Any]] = []
    endpoint = base_url.rstrip("/") + "/api/voice/command"
    ws_url = _ws_push_url(base_url, panel_id=panel_id)
    active_sender = sender or _post_voice_command
    active_observer = panel_observer or _observe_panel_events
    headers = {"X-Device-Token": device_token}
    active_repeat = max(1, repeat)

    for repeat_index in range(1, active_repeat + 1):
        for raw_case in cases:
            case = _normalise_case(raw_case)
            payload = {
                "text": case["text"],
                "panel_id": panel_id,
                "session_id": f"pi-touch-prod-{case['case_id']}",
            }

            def action() -> Mapping[str, Any]:
                return active_sender(endpoint, payload, timeout_seconds, headers)

            started = time.perf_counter()
            response: Mapping[str, Any] | None = None
            panel_events: Sequence[Mapping[str, Any]] = []
            request_error = None
            observer_error = None
            try:
                response, panel_events, observer_error = active_observer(
                    ws_url,
                    panel_id,
                    device_token,
                    websocket_timeout_seconds,
                    action,
                )
            except Exception as exc:
                request_error = f"{type(exc).__name__}: {exc}"
            http_latency_ms = (time.perf_counter() - started) * 1000
            observations.append(
                _observation(
                    case,
                    repeat_index=repeat_index,
                    response=response,
                    panel_events=panel_events,
                    http_latency_ms=http_latency_ms,
                    request_error=request_error,
                    observer_error=observer_error,
                    cue_max_ms=cue_max_ms,
                    final_max_ms=final_max_ms,
                )
            )
    return observations


def build_report(
    cases: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    *,
    base_url: str,
    panel_id: str,
    repeat: int,
    include_observations: bool = False,
    cue_max_ms: float = DEFAULT_CUE_MAX_MS,
    final_max_ms: float = DEFAULT_FINAL_MAX_MS,
) -> dict[str, Any]:
    summary = {
        "overall": _stats(observations),
        "by_intent_group": _breakdown(observations, lambda item: str(item.get("intent_group") or "unknown")),
        "by_source": _breakdown(observations, lambda item: str(item.get("source") or "unknown")),
    }
    report: dict[str, Any] = {
        "report_kind": "pi_touch_hybrid_production_probe",
        "note": (
            "Live production probe for Zoe's non-stream /api/voice/command Pi hybrid route. "
            "Measures panel websocket cue timing plus final HTTP/TTS completion."
        ),
        "base_url": base_url.rstrip("/"),
        "endpoint": base_url.rstrip("/") + "/api/voice/command",
        "panel_id": panel_id,
        "repeat": max(1, repeat),
        "unique_case_count": len({_normalise_case(case)["case_id"] for case in cases}),
        "observation_count": len(observations),
        "contract": {
            "production_route_change": True,
            "route": "pi_hybrid_production_non_stream_voice_command",
            "panel_transport": "/ws/push?panel_id=<panel>",
            "cue_source": "voice:responding processing_ack=true source=pi_hybrid_production",
            "final_source": "voice:responding pi_hybrid=true plus /api/voice/command JSON",
            "promotion_enabled": False,
            "memory_writes": "same_as_live_voice_command_for_effective_user",
            "cue_max_ms": cue_max_ms,
            "final_max_ms": final_max_ms,
        },
        "summary": summary,
        "readiness": _readiness(summary["overall"]),
    }
    if include_observations:
        report["observations"] = list(observations)
    return report


def _observe_panel_events(
    ws_url: str,
    panel_id: str,
    device_token: str,
    timeout_seconds: float,
    action: Callable[[], Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, Sequence[Mapping[str, Any]], str | None]:
    if websocket is None:
        raise RuntimeError("websocket-client is not installed")
    ws = websocket.create_connection(
        ws_url,
        timeout=timeout_seconds,
        header=[f"X-Device-Token: {device_token}"],
    )
    events: list[dict[str, Any]] = []
    errors: queue.Queue[str] = queue.Queue()
    stop = threading.Event()
    start_ref = {"value": time.perf_counter()}

    def recv_loop() -> None:
        while not stop.is_set():
            try:
                raw = ws.recv()
            except Exception as exc:
                if not stop.is_set():
                    errors.put(f"{type(exc).__name__}: {exc}")
                return
            now_ms = (time.perf_counter() - start_ref["value"]) * 1000
            try:
                message = json.loads(raw)
            except Exception:
                message = {"raw": str(raw)}
            if _event_matches_panel(message, panel_id):
                events.append({"offset_ms": now_ms, "message": message})
            if _is_done_event(message, panel_id):
                stop.set()
                return

    start_ref["value"] = time.perf_counter()
    thread = threading.Thread(target=recv_loop, daemon=True)
    thread.start()
    response = None
    try:
        response = action()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not stop.is_set():
            if _has_done(events):
                break
            time.sleep(0.02)
    finally:
        stop.set()
        try:
            ws.close()
        except Exception:
            pass
        thread.join(timeout=0.5)
    observer_error = None if errors.empty() else errors.get_nowait()
    return response, events, observer_error


def _post_voice_command(
    url: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
    return json.loads(body)


def _observation(
    case: Mapping[str, str],
    *,
    repeat_index: int,
    response: Mapping[str, Any] | None,
    panel_events: Sequence[Mapping[str, Any]],
    http_latency_ms: float,
    request_error: str | None,
    observer_error: str | None,
    cue_max_ms: float,
    final_max_ms: float,
) -> dict[str, Any]:
    response = response or {}
    cue = _first_panel_message(panel_events, lambda msg: _message_type(msg) == "voice:responding" and bool(_message_data(msg).get("processing_ack")))
    final = _first_panel_message(panel_events, lambda msg: _message_type(msg) == "voice:responding" and bool(_message_data(msg).get("pi_hybrid")))
    done = _first_panel_message(panel_events, lambda msg: _message_type(msg) == "voice:done")
    pi_hybrid = response.get("pi_hybrid") if isinstance(response.get("pi_hybrid"), Mapping) else {}
    accepted = bool(pi_hybrid.get("accepted"))
    reply = str(response.get("reply") or "")
    cue_latency = _float_or_none(cue.get("offset_ms") if cue else None)
    final_ws_latency = _float_or_none(final.get("offset_ms") if final else None)
    final_latency = http_latency_ms if reply else None
    cue_text = str(_message_data(cue.get("message") if cue else {}).get("text") or "")
    final_text = str(_message_data(final.get("message") if final else {}).get("text") or "")
    natural_cue = cue_latency is not None and cue_latency <= cue_max_ms
    natural_final = final_latency is not None and final_latency <= final_max_ms
    return {
        "case_id": case["case_id"],
        "repeat_index": repeat_index,
        "text": case["text"],
        "intent_group": case["intent_group"],
        "expected_intent": case["expected_intent"],
        "source": case["source"],
        "request_error": request_error,
        "observer_error": observer_error,
        "http_latency_ms": http_latency_ms,
        "panel_event_count": len(panel_events),
        "panel_event_types": [_message_type(item.get("message") or {}) for item in panel_events],
        "processing_ack_available": cue is not None,
        "processing_ack_text": cue_text,
        "processing_ack_latency_ms": cue_latency,
        "processing_ack_within_budget": natural_cue,
        "final_panel_response_available": final is not None,
        "final_panel_response_text": _preview(final_text, limit=200),
        "final_panel_response_latency_ms": final_ws_latency,
        "done_available": done is not None,
        "final_http_response_available": bool(reply),
        "final_http_response_preview": _preview(reply, limit=260),
        "final_http_latency_ms": final_latency,
        "final_http_within_budget": natural_final,
        "response_ok": bool(response.get("ok")),
        "response_intent": response.get("intent"),
        "expected_intent_matched": response.get("intent") == case["expected_intent"],
        "pi_hybrid_accepted": accepted,
        "pi_hybrid_reason": pi_hybrid.get("reason"),
        "pi_hybrid_intent_group": pi_hybrid.get("intent_group"),
        "pi_hybrid_agreement_kind": pi_hybrid.get("agreement_kind"),
        "audio_returned": bool(response.get("audio_base64")),
        "natural_flow_pass": bool(natural_cue and natural_final and accepted and reply and not request_error),
    }


def _stats(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {
            "observation_count": 0,
            "unique_case_count": 0,
            "natural_flow_pass_rate": None,
            "pi_hybrid_accept_rate": None,
            "processing_ack_rate": None,
            "final_panel_response_rate": None,
            "final_http_response_rate": None,
            "audio_return_rate": None,
            "request_error_rate": None,
            "observer_error_rate": None,
            "processing_ack_latency_ms": _latency_stats([]),
            "final_panel_response_latency_ms": _latency_stats([]),
            "final_http_latency_ms": _latency_stats([]),
        }
    return {
        "observation_count": len(observations),
        "unique_case_count": len({str(item.get("case_id")) for item in observations}),
        "natural_flow_pass_rate": _rate(bool(item.get("natural_flow_pass")) for item in observations),
        "pi_hybrid_accept_rate": _rate(bool(item.get("pi_hybrid_accepted")) for item in observations),
        "processing_ack_rate": _rate(bool(item.get("processing_ack_available")) for item in observations),
        "final_panel_response_rate": _rate(bool(item.get("final_panel_response_available")) for item in observations),
        "final_http_response_rate": _rate(bool(item.get("final_http_response_available")) for item in observations),
        "audio_return_rate": _rate(bool(item.get("audio_returned")) for item in observations),
        "request_error_rate": _rate(bool(item.get("request_error")) for item in observations),
        "observer_error_rate": _rate(bool(item.get("observer_error")) for item in observations),
        "processing_ack_latency_ms": _latency_stats(_floats(item.get("processing_ack_latency_ms") for item in observations)),
        "final_panel_response_latency_ms": _latency_stats(_floats(item.get("final_panel_response_latency_ms") for item in observations)),
        "final_http_latency_ms": _latency_stats(_floats(item.get("final_http_latency_ms") for item in observations)),
    }


def _readiness(overall: Mapping[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if overall.get("natural_flow_pass_rate") != 1.0:
        blockers.append("natural_flow_not_passing_all_cases")
    if overall.get("processing_ack_rate") != 1.0:
        blockers.append("panel_processing_ack_missing")
    if overall.get("pi_hybrid_accept_rate") != 1.0:
        blockers.append("pi_hybrid_not_accepting_all_cases")
    if overall.get("audio_return_rate") != 1.0:
        blockers.append("voice_audio_missing")
    if (overall.get("request_error_rate") or 0) > 0:
        blockers.append("voice_command_request_errors")
    if (overall.get("observer_error_rate") or 0) > 0:
        blockers.append("panel_websocket_observer_errors")
    return {"ready_for_touch_panel_smoke": not blockers, "blockers": blockers}


def _event_matches_panel(message: Mapping[str, Any], panel_id: str) -> bool:
    msg_type = _message_type(message)
    if msg_type == "connected":
        return str(message.get("panel_id") or "") in {"", panel_id}
    data = _message_data(message)
    return str(data.get("panel_id") or "") == panel_id


def _is_done_event(message: Mapping[str, Any], panel_id: str) -> bool:
    return _event_matches_panel(message, panel_id) and _message_type(message) == "voice:done"


def _has_done(events: Sequence[Mapping[str, Any]]) -> bool:
    return any(_message_type(item.get("message") or {}) == "voice:done" for item in events)


def _first_panel_message(events: Sequence[Mapping[str, Any]], predicate: Callable[[Mapping[str, Any]], bool]) -> Mapping[str, Any] | None:
    for event in events:
        message = event.get("message") if isinstance(event.get("message"), Mapping) else {}
        if predicate(message):
            return event
    return None


def _message_type(message: Mapping[str, Any]) -> str:
    return str(message.get("type") or "")


def _message_data(message: Mapping[str, Any]) -> Mapping[str, Any]:
    data = message.get("data")
    return data if isinstance(data, Mapping) else {}


def _ws_push_url(base_url: str, *, panel_id: str) -> str:
    parsed = urllib.parse.urlparse(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urllib.parse.urlencode({"panel_id": panel_id})
    return urllib.parse.urlunparse((scheme, parsed.netloc, "/ws/push", "", query, ""))


def _breakdown(observations: Sequence[Mapping[str, Any]], key_fn: Callable[[Mapping[str, Any]], str]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in observations:
        grouped.setdefault(key_fn(item), []).append(item)
    return {key: _stats(values) for key, values in sorted(grouped.items())}


def _load_cases(paths: Sequence[str], *, no_default_cases: bool) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = [] if no_default_cases else [_normalise_case(item) for item in DEFAULT_CASES]
    for path in paths:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else [raw]
        cases.extend(_normalise_case(item) for item in rows)
    return cases


def _normalise_case(raw: Mapping[str, Any]) -> dict[str, str]:
    return {
        "case_id": str(raw.get("case_id") or raw.get("id") or raw.get("text") or "case"),
        "text": str(raw.get("text") or ""),
        "expected_intent": str(raw.get("expected_intent") or ""),
        "intent_group": str(raw.get("intent_group") or raw.get("expected_intent") or "unknown"),
        "source": str(raw.get("source") or "synthetic"),
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
    return [float(value) for value in values if isinstance(value, (int, float))]


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _preview(text: str, *, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe production Pi hybrid touch-panel voice flow")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--panel-id", default=os.environ.get("ZOE_TOUCH_PROBE_PANEL_ID", "zoe-touch-pi"))
    parser.add_argument("--device-token", default=os.environ.get("ZOE_TOUCH_PROBE_DEVICE_TOKEN", ""))
    parser.add_argument("--cases-file", action="append", default=[])
    parser.add_argument("--no-default-cases", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=18.0)
    parser.add_argument("--websocket-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--cue-max-ms", type=float, default=DEFAULT_CUE_MAX_MS)
    parser.add_argument("--final-max-ms", type=float, default=DEFAULT_FINAL_MAX_MS)
    parser.add_argument("--include-observations", action="store_true")
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases_file, no_default_cases=args.no_default_cases)
    observations = run_probe(
        cases,
        base_url=args.base_url,
        panel_id=args.panel_id,
        device_token=args.device_token,
        repeat=args.repeat,
        timeout_seconds=args.timeout_seconds,
        websocket_timeout_seconds=args.websocket_timeout_seconds,
        cue_max_ms=args.cue_max_ms,
        final_max_ms=args.final_max_ms,
    )
    print(json.dumps(build_report(cases, observations, base_url=args.base_url, panel_id=args.panel_id, repeat=args.repeat, include_observations=args.include_observations, cue_max_ms=args.cue_max_ms, final_max_ms=args.final_max_ms), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
