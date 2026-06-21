#!/usr/bin/env python3
"""Zoe latency smoke probe.

Measures a small set of user-visible endpoints and compares them with the last
saved baseline. The probe is intentionally light enough to run on demand or in CI:
health/status checks, chat non-stream latency, optional voice command latency,
and LiveKit health response latency.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASELINE = Path.home() / ".cache" / "zoe" / "latency_baseline.json"
DEFAULT_RESULTS = Path.home() / ".cache" / "zoe" / "latency_last.json"


@dataclass
class Sample:
    name: str
    ok: bool
    elapsed_ms: float
    status: int | None = None
    detail: str = ""


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[int | None, Any, float, str]:
    req_headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = _json_bytes(payload)
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed_ms = (time.perf_counter() - started) * 1000
            text = raw.decode("utf-8", errors="replace")
            try:
                body = json.loads(text) if text else {}
            except json.JSONDecodeError:
                body = text[:300]
            return resp.status, body, elapsed_ms, ""
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        body = exc.read().decode("utf-8", errors="replace")[:300]
        return exc.code, body, elapsed_ms, body
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return None, None, elapsed_ms, str(exc)


def sample_http_get(name: str, url: str, timeout: float) -> Sample:
    status, body, elapsed_ms, error = request_json("GET", url, timeout=timeout)
    ok = status is not None and 200 <= status < 300
    detail = error
    if ok and isinstance(body, dict):
        status_text = body.get("status") or body.get("ok") or "ok"
        detail = str(status_text)
    return Sample(name=name, ok=ok, elapsed_ms=elapsed_ms, status=status, detail=detail)


def sample_chat(base_url: str, prompt: str, timeout: float, index: int) -> Sample:
    session_id = f"post-merge-latency-{uuid.uuid4().hex[:8]}"
    status, body, elapsed_ms, error = request_json(
        "POST",
        f"{base_url}/api/chat/?stream=false",
        payload={"message": prompt, "session_id": session_id},
        timeout=timeout,
    )
    reply = ""
    if isinstance(body, dict):
        reply = str(body.get("reply") or body.get("response") or body.get("message") or "")
    ok = status == 200 and bool(reply.strip())
    detail = f"{len(reply)} chars" if ok else (error or str(body)[:160])
    return Sample(name=f"chat.{index}", ok=ok, elapsed_ms=elapsed_ms, status=status, detail=detail)


def sample_voice_command(base_url: str, token: str, panel_id: str, timeout: float) -> Sample:
    status, body, elapsed_ms, error = request_json(
        "POST",
        f"{base_url}/api/voice/command",
        payload={"text": "What time is it?", "panel_id": panel_id},
        headers={"X-Device-Token": token},
        timeout=timeout,
    )
    reply = ""
    if isinstance(body, dict):
        reply = str(body.get("reply") or "")
    ok = status == 200 and isinstance(body, dict) and bool(body.get("ok"))
    detail = f"{len(reply)} chars" if ok else (error or str(body)[:160])
    return Sample(name="voice.command", ok=ok, elapsed_ms=elapsed_ms, status=status, detail=detail)


def summarize(samples: list[Sample]) -> dict[str, Any]:
    groups: dict[str, list[Sample]] = {}
    for sample in samples:
        # Group repeated samples of the SAME metric (chat.1, chat.2 → "chat") but
        # keep distinct endpoints separate — only a trailing numeric suffix is
        # stripped, so "voice.livekit_health" and "voice.command" don't collapse
        # into one "voice" bucket (which mixed two unrelated endpoints' latencies).
        key = re.sub(r"\.\d+$", "", sample.name)
        groups.setdefault(key, []).append(sample)
    summary: dict[str, Any] = {}
    for key, items in groups.items():
        elapsed = [item.elapsed_ms for item in items if item.ok and item.elapsed_ms > 0]
        summary[key] = {
            "ok": all(item.ok for item in items),
            "count": len(items),
            "passed": sum(1 for item in items if item.ok),
            "median_ms": round(statistics.median(elapsed), 1) if elapsed else None,
            "max_ms": round(max(elapsed), 1) if elapsed else None,
        }
    return summary


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare(summary: dict[str, Any], baseline: dict[str, Any], warn_ratio: float, warn_ms: float) -> list[str]:
    warnings: list[str] = []
    base_summary = baseline.get("summary") if isinstance(baseline, dict) else None
    if not isinstance(base_summary, dict):
        return warnings
    for key, current in summary.items():
        current_ms = current.get("median_ms")
        base_entry = base_summary.get(key)
        base_ms = base_entry.get("median_ms") if isinstance(base_entry, dict) else None
        if not isinstance(current_ms, (int, float)) or not isinstance(base_ms, (int, float)) or base_ms <= 0:
            continue
        delta = current_ms - base_ms
        ratio = current_ms / base_ms
        if ratio >= warn_ratio and delta >= warn_ms:
            warnings.append(f"{key} median {current_ms:.1f}ms vs baseline {base_ms:.1f}ms ({ratio:.2f}x)")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure Zoe post-merge chat/voice latency.")
    parser.add_argument("--base-url", default=os.environ.get("ZOE_URL", "http://127.0.0.1:8000").rstrip("/"))
    parser.add_argument("--samples", type=int, default=int(os.environ.get("ZOE_LATENCY_SAMPLES", "2")))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("ZOE_LATENCY_TIMEOUT_S", "30")))
    parser.add_argument("--baseline", type=Path, default=Path(os.environ.get("ZOE_LATENCY_BASELINE", DEFAULT_BASELINE)))
    parser.add_argument("--results", type=Path, default=Path(os.environ.get("ZOE_LATENCY_RESULTS", DEFAULT_RESULTS)))
    parser.add_argument("--update-baseline", action="store_true", help="Save this run as the new comparison baseline.")
    parser.add_argument("--warn-ratio", type=float, default=float(os.environ.get("ZOE_LATENCY_WARN_RATIO", "1.5")))
    parser.add_argument("--warn-ms", type=float, default=float(os.environ.get("ZOE_LATENCY_WARN_MS", "500")))
    parser.add_argument("--panel-id", default=os.environ.get("ZOE_PANEL_ID", "post-merge-probe"))
    args = parser.parse_args()

    # Device token is read from the environment ONLY (never a CLI flag) so it
    # can't leak into `ps aux` / process listings on a shared host.
    voice_token = os.environ.get("ZOE_DEVICE_TOKEN") or os.environ.get("DEVICE_TOKEN") or ""

    samples: list[Sample] = []
    samples.append(sample_http_get("health", f"{args.base_url}/health", min(args.timeout, 8)))
    samples.append(sample_http_get("system.status", f"{args.base_url}/api/system/status", min(args.timeout, 10)))
    samples.append(sample_http_get("voice.livekit_health", f"{args.base_url}/api/voice/livekit-health", min(args.timeout, 8)))

    chat_prompts = ["show shopping list", "What time is it?"]
    for idx in range(max(1, args.samples)):
        samples.append(sample_chat(args.base_url, chat_prompts[idx % len(chat_prompts)], args.timeout, idx + 1))

    if voice_token:
        samples.append(sample_voice_command(args.base_url, voice_token, args.panel_id, args.timeout))
    else:
        samples.append(Sample("voice.command", True, 0.0, None, "skipped: set ZOE_DEVICE_TOKEN to measure authenticated voice command"))

    summary = summarize(samples)
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "summary": summary,
        "samples": [asdict(sample) for sample in samples],
    }
    write_json(args.results, payload)

    baseline = load_json(args.baseline)
    warnings = compare(summary, baseline, args.warn_ratio, args.warn_ms)

    print(f"Zoe post-merge latency probe: {args.base_url}")
    failures = 0
    for sample in samples:
        if sample.elapsed_ms == 0.0 and sample.detail.startswith("skipped"):
            state = "SKIP"
        elif sample.ok:
            state = "PASS"
        else:
            state = "FAIL"
            failures += 1
        status = sample.status if sample.status is not None else "-"
        print(f"{state:4} {sample.name:22} {sample.elapsed_ms:8.1f} ms status={status} {sample.detail}")
    for warning in warnings:
        print(f"WARN {warning}")

    if args.update_baseline or not args.baseline.exists():
        write_json(args.baseline, payload)
        print(f"Baseline saved: {args.baseline}")
    print(f"Results saved: {args.results}")

    return 1 if (failures or warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

