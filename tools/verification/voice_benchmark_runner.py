#!/usr/bin/env python3
import statistics
import time
from typing import List, Dict

import requests

API = "http://localhost:8000/api/voice/synthesize"

PROMPTS = [
    "Hi Jason, this is Zoe. I can help with your schedule and reminders.",
    "Weather tomorrow is mild with a chance of showers in the afternoon.",
    "I have added wood, nails, and glue to your shopping list.",
]


def run_once(text: str) -> Dict[str, float]:
    t0 = time.perf_counter()
    r = requests.post(API, json={"text": text}, timeout=40)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "ok": 1.0 if r.status_code == 200 else 0.0,
        "status_code": float(r.status_code),
        "latency_ms": elapsed_ms,
        "bytes": float(len(r.content)),
        "provider_edge": 1.0 if r.headers.get("x-zoe-tts-provider") == "edge-tts" else 0.0,
    }


def main() -> None:
    samples: List[Dict[str, float]] = []
    for text in PROMPTS:
        for _ in range(3):
            samples.append(run_once(text))

    ok = [s for s in samples if s["ok"] == 1.0]
    lat = [s["latency_ms"] for s in ok]
    bts = [s["bytes"] for s in ok]
    edge_ratio = (sum(s["provider_edge"] for s in ok) / len(ok)) if ok else 0.0

    print("Voice benchmark summary")
    print(f"total_samples={len(samples)} success={len(ok)}")
    if ok:
        print(f"provider_edge_ratio={edge_ratio:.2f}")
        print(f"latency_ms_p50={statistics.median(lat):.1f}")
        print(f"latency_ms_p95={sorted(lat)[int(0.95 * (len(lat)-1))]:.1f}")
        print(f"latency_ms_avg={statistics.mean(lat):.1f}")
        print(f"bytes_avg={statistics.mean(bts):.0f}")
    else:
        print("No successful synthesis samples.")


if __name__ == "__main__":
    main()
