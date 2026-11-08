#!/usr/bin/env python3
"""Read-only report of the brain's prompt-cache health (for the --cache-ram trial).

Prints one JSON object:
  * metrics     - cumulative prompt_tokens_total / prompt_tokens_cached_total from the
                  brain's /metrics and the resulting reuse ratio (the "85%" figure).
                  Cumulative since the server started, so diff two readings to get a window.
  * journal     - per-task view from `journalctl --user -u llama-server`: how many tasks
                  re-prefilled >= 1000 tokens (a main-turn cache MISS), 300-999 (aux miss),
                  and every "making room for prompt cache entry" eviction with its size.
                  Hits are not logged at default verbosity, so misses + evictions are the
                  observable signal; a smaller cap must not raise the miss share.

    python3 scripts/maintenance/brain_cache_report.py [--since "2026-11-08 00:00"]

Touches nothing: one GET to /metrics and a journal read.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.request


def metrics(url: str) -> dict:
    out: dict = {}
    try:
        body = urllib.request.urlopen(f"{url}/metrics", timeout=3).read().decode()
    except Exception as exc:  # brain down / metrics off
        return {"error": str(exc)}
    for line in body.splitlines():
        m = re.match(r"llamacpp:(prompt_tokens_total|prompt_tokens_cached_total|n_tokens_max)\s+([\d.e+]+)", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    p, c = out.get("prompt_tokens_total", 0.0), out.get("prompt_tokens_cached_total", 0.0)
    out["reuse_ratio"] = round(c / (p + c), 4) if (p + c) else None
    return out


def journal(since: str | None) -> dict:
    cmd = ["journalctl", "--user", "-u", "llama-server.service", "--no-pager", "-o", "cat"]
    if since:
        cmd += ["--since", since]
    text = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    evict = [float(x) for x in re.findall(r"removing oldest entry \(size = ([\d.]+) MiB\)", text)]
    evaluated = [int(x) for x in re.findall(r"prompt eval time =\s+[\d.]+ ms /\s+(\d+) tokens", text)]
    n = len(evaluated)
    return {
        "tasks": n,
        "reprefill_ge_1000": sum(t >= 1000 for t in evaluated),
        "reprefill_300_999": sum(300 <= t < 1000 for t in evaluated),
        "reprefill_ge_1000_share": round(sum(t >= 1000 for t in evaluated) / n, 4) if n else None,
        "evictions": len(evict),
        "evicted_mib_total": round(sum(evict), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--since", default=None)
    a = ap.parse_args()
    print(json.dumps({"metrics": metrics(a.url), "journal": journal(a.since)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
