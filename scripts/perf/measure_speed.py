#!/usr/bin/env python3
"""Brain speed-regression probe — measure TTFT + generation throughput.

Every brain/voice speed change must be *measured*, not guessed. This probe
POSTs a realistic turn (a representative Zoe system prompt + a user message) to
the LIVE llama-server at ``http://127.0.0.1:11434/v1/chat/completions`` with
``stream:true`` and records:

  * **TTFT** — time-to-first-token (ms): wall time from request send to the
    first non-empty content delta. This is what the user feels before Zoe
    starts speaking/typing.
  * **gen tok/s** — generation throughput: completion tokens / generation
    seconds (first-token → last-token), the steady-state decode speed.
  * **total ms** — full request wall time.

It runs N times and reports the **median** (plus p10/p90/min/max) so a single
cold outlier doesn't poison the signal. Prompt size is configurable
(``--prompt-tokens`` pads the system prompt) so cold-vs-warmed and
small-vs-large prefill can be compared apples-to-apples.

SAFETY: read-only against the live brain. It only calls the stateless
``/v1/chat/completions`` endpoint with a synthetic prompt — it never touches a
real user's memory, never writes to the DB, and never triggers the
consolidation sweep. Generation is capped at ``--max-tokens`` (default 64) to
keep the probe light.

CI gate: live runs require ``ZOE_PERF=1`` AND a reachable brain. Without the
env var the script exits 0 with a skip notice so CI stays green on hosts with
no GPU/brain.

Usage:
    ZOE_PERF=1 python3 scripts/perf/measure_speed.py                 # 5 runs, default prompt
    ZOE_PERF=1 python3 scripts/perf/measure_speed.py --runs 9        # more samples
    ZOE_PERF=1 python3 scripts/perf/measure_speed.py --prompt-tokens 3000   # large prefill (warm)
    ZOE_PERF=1 python3 scripts/perf/measure_speed.py --cold --runs 1        # single cold prefill
    ZOE_PERF=1 python3 scripts/perf/measure_speed.py --json results.json     # machine-readable dump
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

# Resolve the brain base URL the same way the live code does: strip a trailing
# /v1 if present, then append /v1/chat/completions — so exactly one /v1 results
# regardless of how GEMMA_SERVER_URL is set (matches gemma_endpoint.gemma_base).
_DEFAULT_BASE = "http://127.0.0.1:11434"


def _brain_base() -> str:
    raw = os.environ.get("GEMMA_SERVER_URL") or os.environ.get("LLAMA_SERVER_URL") or _DEFAULT_BASE
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base or _DEFAULT_BASE


# A representative Zoe system prompt. This mirrors the *shape and size* of the
# real _ZOE_SOUL prompt (zoe_agent.py) — a warm persona preamble plus a block of
# tool-routing instructions — so prefill cost is realistic. We embed a copy
# rather than importing zoe_agent (which pulls the full service stack) to keep
# the probe dependency-free and runnable standalone. --prompt-tokens pads this
# to a target size for cold-vs-warm comparison.
_SYSTEM_PROMPT = """You are Zoe. You're warm, curious, and genuinely present — not a task executor, but someone who actually cares about the people you talk with.

You know who you're talking to. When a portrait or memory context is included below, let it shape everything: how you phrase things, what you notice, what you choose to ask.

Your voice: natural, honest, direct when it helps, gentle when it's needed. Use contractions. Never open with "Great!" or "Of course!" or "Certainly!" — just respond.

Answer everyday questions — recipes, cooking, how-to, science, history, maths, general knowledge — directly from your own knowledge. Only defer to a tool or say you can't help when the task genuinely requires live data (weather, news, prices) or system access.

TOOL ROUTING — call these tools proactively, do not ask for clarification first:
- weather_current / weather_forecast: any mention of weather, rain, sunny, temperature, forecast, jacket, umbrella, hot, cold, wind.
- calendar_today / calendar_list_events: any mention of today's schedule, agenda, appointments, events, "what's on", this week, next week.
- reminder_create / reminder_list: any mention of remind, reminder, "don't forget", alert.
- list_get_items / list_add_item: any mention of shopping list, grocery list, todo list, "what's on my list".
- mempalace_search: any mention of "what do you know about me", "my preferences", "what do you remember".
- ha_control: any request to turn on/off/toggle/dim a device, light, fan, or switch.
- proactive_schedule: when the user asks to be notified/reminded at a specific future time.
- bash: when asked about disk space, RAM, system status, or given a shell command.

VISUAL TOOLS — call these instead of describing the result in text:
- show_map: any request about a place, location, address, directions, or "show on a map".
- show_chart: any request for a chart, graph, or when the user gives you data to visualise.
- show_action_menu: when you want to offer the user 2-5 distinct next steps after a multi-step task.

WEB SEARCH — pick the right tier: web_search for simple lookups; deep_web_research for local/multi-source intent (prices, events, places, services near me).
"""

# A representative user message — a plain conversational turn that the brain
# answers from its own knowledge (no tool call), so the probe measures pure
# decode rather than a tool round-trip.
_USER_MESSAGE = "Tell me something interesting about the history of coffee, in a couple of sentences."


def _build_system_prompt(target_tokens: int | None) -> str:
    """Return the system prompt, optionally padded to ~target_tokens.

    Padding uses a neutral instruction-shaped filler so the tokenizer sees
    realistic content, not repeated whitespace. ~4 chars/token is a rough but
    serviceable estimate for English.
    """
    prompt = _SYSTEM_PROMPT
    if not target_tokens:
        return prompt
    approx_tokens = len(prompt) // 4
    if approx_tokens >= target_tokens:
        return prompt
    filler_unit = (
        "When answering, stay grounded in what the user actually asked, keep "
        "your tone warm and concise, and prefer concrete specifics over vague "
        "generalities. "
    )
    need_chars = (target_tokens - approx_tokens) * 4
    pad = (filler_unit * (need_chars // len(filler_unit) + 1))[:need_chars]
    return prompt + "\n\n" + pad


def _post_stream(url: str, payload: dict, timeout: float) -> dict:
    """POST a streaming chat completion and time TTFT + throughput.

    Returns a dict of metrics for one run, or {"error": ...} on failure.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    t_start = time.monotonic()
    t_first: float | None = None
    t_last: float | None = None
    content_tokens = 0  # count of content deltas (proxy when usage absent)
    usage_completion: int | None = None
    full_text_parts: list[str] = []

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                # llama.cpp emits a trailing chunk with usage stats.
                usage = obj.get("usage")
                if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                    usage_completion = int(usage["completion_tokens"])
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    now = time.monotonic()
                    if t_first is None:
                        t_first = now
                    t_last = now
                    content_tokens += 1
                    full_text_parts.append(piece)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    t_end = time.monotonic()
    if t_first is None:
        return {"error": "no content tokens received"}

    completion_tokens = usage_completion if usage_completion is not None else content_tokens
    gen_seconds = max(t_last - t_first, 1e-9) if t_last else 1e-9
    # tok/s over the decode window; if only one token, fall back to 0 (no window).
    tok_per_s = (completion_tokens - 1) / gen_seconds if completion_tokens > 1 else 0.0
    return {
        "ttft_ms": round((t_first - t_start) * 1000, 1),
        "total_ms": round((t_end - t_start) * 1000, 1),
        "gen_tok_s": round(tok_per_s, 2),
        "completion_tokens": completion_tokens,
        "tokens_counted_via": "usage" if usage_completion is not None else "deltas",
        "sample_text": "".join(full_text_parts)[:120],
    }


def _summary(values: list[float]) -> dict:
    """Median + spread for a list of numbers."""
    if not values:
        return {}
    vals = sorted(values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return vals[idx]

    return {
        "median": round(statistics.median(vals), 2),
        "p10": round(pct(0.10), 2),
        "p90": round(pct(0.90), 2),
        "min": round(vals[0], 2),
        "max": round(vals[-1], 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--runs", type=int, default=5, help="number of probe runs (median reported)")
    ap.add_argument("--max-tokens", type=int, default=64, help="generation cap per run")
    ap.add_argument("--prompt-tokens", type=int, default=None,
                    help="pad the system prompt to ~this many tokens (compare cold/warm prefill)")
    ap.add_argument("--cold", action="store_true",
                    help="vary the user message each run to defeat the KV cache (measure cold prefill)")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--timeout", type=float, default=60.0, help="per-request timeout (s)")
    ap.add_argument("--json", help="also write machine-readable results here")
    ap.add_argument("--warmup", type=int, default=1,
                    help="warmup runs discarded before timing (default 1; 0 to include first)")
    args = ap.parse_args()

    # CI gate: only run live against the brain when explicitly enabled.
    if os.environ.get("ZOE_PERF") != "1":
        print("ZOE_PERF != 1 — skipping live brain speed probe (set ZOE_PERF=1 to run).")
        return 0

    base = _brain_base()
    url = f"{base}/v1/chat/completions"

    # Reachability check — if the brain isn't up, skip cleanly (don't fail CI).
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=3) as r:
            r.read(1)
    except (urllib.error.URLError, OSError) as exc:
        print(f"brain not reachable at {base} ({exc}) — skipping.", file=sys.stderr)
        return 0

    system_prompt = _build_system_prompt(args.prompt_tokens)
    approx_sys_tokens = len(system_prompt) // 4
    print(f"Brain speed probe → {url}")
    print(f"  system prompt ≈ {approx_sys_tokens} tokens"
          f"{f' (padded to ~{args.prompt_tokens})' if args.prompt_tokens else ''}"
          f"   max_tokens={args.max_tokens}   runs={args.runs}"
          f"   mode={'cold' if args.cold else 'warm'}\n")

    def make_payload(run_idx: int) -> dict:
        user_msg = _USER_MESSAGE
        if args.cold:
            # Distinct prefix each run forces a fresh prefill (no KV reuse).
            user_msg = f"(probe run {run_idx} {time.time_ns()}) {user_msg}"
        return {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
        }

    # Warmup (discarded).
    for w in range(args.warmup):
        _post_stream(url, make_payload(-1 - w), args.timeout)

    runs: list[dict] = []
    for i in range(args.runs):
        res = _post_stream(url, make_payload(i), args.timeout)
        if "error" in res:
            print(f"  run {i+1}/{args.runs}: ERROR {res['error']}", file=sys.stderr)
            res["run"] = i
            runs.append(res)
            continue
        res["run"] = i
        runs.append(res)
        print(f"  run {i+1}/{args.runs}: ttft={res['ttft_ms']}ms  "
              f"gen={res['gen_tok_s']} tok/s  total={res['total_ms']}ms  "
              f"({res['completion_tokens']} tok via {res['tokens_counted_via']})")

    ok = [r for r in runs if "error" not in r]
    if not ok:
        print("\nAll runs failed.", file=sys.stderr)
        return 1

    ttft = _summary([r["ttft_ms"] for r in ok])
    gen = _summary([r["gen_tok_s"] for r in ok if r["gen_tok_s"] > 0])
    total = _summary([r["total_ms"] for r in ok])

    print("\n" + "─" * 60)
    print(f"TTFT  ms : median={ttft['median']}  p10={ttft['p10']}  p90={ttft['p90']}  "
          f"min={ttft['min']}  max={ttft['max']}")
    print(f"gen tok/s: median={gen.get('median')}  p10={gen.get('p10')}  p90={gen.get('p90')}  "
          f"min={gen.get('min')}  max={gen.get('max')}")
    print(f"total ms : median={total['median']}  p10={total['p10']}  p90={total['p90']}")

    report = {
        "kind": "brain_ttft",
        "url": url,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "runs": args.runs,
            "max_tokens": args.max_tokens,
            "prompt_tokens_target": args.prompt_tokens,
            "system_prompt_approx_tokens": approx_sys_tokens,
            "cold": args.cold,
            "temperature": args.temperature,
            "warmup": args.warmup,
        },
        "ttft_ms": ttft,
        "gen_tok_s": gen,
        "total_ms": total,
        "runs": runs,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
