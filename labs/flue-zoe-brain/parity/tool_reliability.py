#!/usr/bin/env python3
"""Tool-call RELIABILITY harness for the Flue Zoe-brain sidecar (LAB-ONLY).

Measures ONE thing in isolation: when a prompt clearly needs one of the brain's
three tools (get_time / recall_memory / shopping_list_add), does the local Gemma
brain actually CALL the right tool?

This is deliberately Flue-ONLY — it does NOT touch zoe-data/zoe-core's /api/chat,
does NOT compare against any other endpoint, and uses no audio/voice path. The
shopping_list_add tool stays a dry-run (server started with
ZOE_BRAIN_ALLOW_WRITES=false) so nothing is mutated.

Ground truth is the tool-call itself, NOT the reply text: for each call we read
the Flue session event stream (GET /agents/zoe/<sid>) and look at the actual
`tool_start` / `tool` events (each carries `toolName`) emitted under the
`operation_start` whose `operationKind` is `prompt`. Reply text can claim a tool
ran when it didn't (or vice-versa); the event stream cannot.

Run (with the Flue brain serving on :3578):
    python3 parity/tool_reliability.py
"""
from __future__ import annotations

import json
import time
import urllib.request
import uuid
from collections import defaultdict

BASE = "http://127.0.0.1:3578"
POST_URL = BASE + "/agents/zoe/{sid}?wait=result"
GET_URL = BASE + "/agents/zoe/{sid}"

REPEATS = 3  # per prompt
TIMEOUT = 120.0

# (tool, prompt) — varied phrasing per tool, no keyword that names the tool.
PROMPTS: list[tuple[str, str]] = [
    # get_time
    ("get_time", "what time is it"),
    ("get_time", "got the time?"),
    ("get_time", "what's today's date?"),
    ("get_time", "what day is it today?"),
    # recall_memory
    ("recall_memory", "what do you know about me"),
    ("recall_memory", "remember anything about me?"),
    ("recall_memory", "what have I told you about myself?"),
    # shopping_list_add
    ("shopping_list_add", "add milk to my list"),
    ("shopping_list_add", "put eggs on the shopping list"),
    ("shopping_list_add", "we're out of coffee, add it to the groceries"),
]


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _get(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def tools_called(events: list[dict]) -> list[str]:
    """Tool names actually invoked during the prompt operation, in order.

    We count `tool_start` events (one per invocation) and read their `toolName`.
    """
    names: list[str] = []
    for e in events:
        if e.get("type") == "tool_start":
            tn = e.get("toolName")
            if tn:
                names.append(tn)
    return names


def reply_text(body: dict) -> str:
    res = body.get("result", body)
    if isinstance(res, dict):
        return (res.get("text") or "").strip()
    return str(res).strip()


def main() -> None:
    # tool -> {"correct": n, "total": n}
    per_tool = defaultdict(lambda: {"correct": 0, "total": 0})
    misfires: list[dict] = []
    rows: list[dict] = []

    total_correct = 0
    total_calls = 0

    for expected, prompt in PROMPTS:
        for rep in range(REPEATS):
            sid = f"rel-{uuid.uuid4().hex[:12]}"
            t0 = time.time()
            body = _post(POST_URL.format(sid=sid), {"message": prompt})
            ms = (time.time() - t0) * 1000.0
            text = reply_text(body)

            # Authoritative signal: the session event stream.
            events = _get(GET_URL.format(sid=sid))
            called = tools_called(events)

            correct = expected in called
            per_tool[expected]["total"] += 1
            total_calls += 1
            if correct:
                per_tool[expected]["correct"] += 1
                total_correct += 1
            else:
                misfires.append(
                    {
                        "expected": expected,
                        "prompt": prompt,
                        "called": called or ["(none)"],
                        "reply": text[:160],
                    }
                )

            rows.append(
                {
                    "expected": expected,
                    "prompt": prompt,
                    "rep": rep + 1,
                    "called": called or ["(none)"],
                    "correct": correct,
                    "ms": ms,
                    "reply": text[:120],
                }
            )
            print(
                f"[{'OK ' if correct else 'MISS'}] {expected:18s} "
                f"called={','.join(called) or '(none)':18s} "
                f"{ms:6.0f}ms  {prompt!r}"
            )
            # Be gentle on the shared GPU.
            time.sleep(1.0)

    print("\n" + "=" * 78)
    print("TOOL-CALL RELIABILITY — Flue Zoe brain (local Gemma E4B), isolated")
    print("=" * 78)
    for tool in ("get_time", "recall_memory", "shopping_list_add"):
        c = per_tool[tool]["correct"]
        t = per_tool[tool]["total"]
        pct = (100.0 * c / t) if t else 0.0
        print(f"  {tool:18s} {c:2d}/{t:2d}  {pct:5.1f}%")
    overall = (100.0 * total_correct / total_calls) if total_calls else 0.0
    print(f"  {'OVERALL':18s} {total_correct:2d}/{total_calls:2d}  {overall:5.1f}%")

    if misfires:
        print("\nMISFIRES:")
        for m in misfires:
            print(
                f"  expected {m['expected']} on {m['prompt']!r} -> "
                f"called {','.join(m['called'])} | reply: {m['reply']!r}"
            )
    else:
        print("\nNo misfires.")

    # Emit machine-readable summary for RELIABILITY.md.
    summary = {
        "repeats": REPEATS,
        "prompts": len(PROMPTS),
        "total_calls": total_calls,
        "total_correct": total_correct,
        "overall_pct": overall,
        "per_tool": {
            t: {
                "correct": per_tool[t]["correct"],
                "total": per_tool[t]["total"],
            }
            for t in ("get_time", "recall_memory", "shopping_list_add")
        },
        "misfires": misfires,
    }
    with open("parity/tool_reliability_last.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("\nWrote parity/tool_reliability_last.json")


if __name__ == "__main__":
    main()
