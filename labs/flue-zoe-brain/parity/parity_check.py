#!/usr/bin/env python3
"""Brain parity + latency harness (LAB-ONLY).

Compares Zoe's CURRENT production brain (zoe-data /api/chat, which runs the
Pi-CLI brain behind the fast-path) against the Flue Zoe-brain sidecar
(labs/flue-zoe-brain), on a small representative prompt set. Records each reply
and its latency, and surfaces the gap.

This is the cutover GATE: it shows how close the Flue brain is to production.
Increment 1 of the Flue brain is persona-only (no memory/abilities tools), so
tool-needing prompts (time, lists, memory) are EXPECTED to differ — that gap is
exactly what tells us what Phase 3 must wire before any cutover.

Run (with the Flue brain serving on :3578):
    python3 parity/parity_check.py
"""
from __future__ import annotations
import json
import statistics
import time
import urllib.request

PI_URL = "http://127.0.0.1:8000/api/chat/?stream=false"          # current prod brain
FLUE_URL = "http://127.0.0.1:3578/agents/zoe/{sid}?wait=result"  # Flue brain sidecar

# (category, prompt). `needs_tools` categories are expected to expose the gap.
PROMPTS = [
    ("persona", "Hi, who are you?"),
    ("persona", "What sort of things can you help me with?"),
    ("smalltalk", "I'm feeling a bit flat today."),
    ("knowledge", "What's the capital of France?"),
    ("reasoning", "If I have 3 apples and eat one, how many are left?"),
    ("tool:time", "What's the time right now?"),
    ("tool:list", "Add milk to my shopping list."),
    ("tool:memory", "What did I just ask you about?"),
]


def _post(url: str, payload: dict, timeout: float = 90.0) -> tuple[dict, float]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode())
    return body, (time.time() - t0) * 1000.0


def ask_pi(prompt: str, sid: str) -> tuple[str, float]:
    body, ms = _post(PI_URL, {"message": prompt, "session_id": sid})
    return (body.get("response") or body.get("error") or "(no response)"), ms


def ask_flue(prompt: str, sid: str) -> tuple[str, float]:
    body, ms = _post(FLUE_URL.format(sid=sid), {"message": prompt})
    res = body.get("result", body)
    text = res.get("text") if isinstance(res, dict) else str(res)
    return (text or "(no response)"), ms


def main() -> None:
    pi_ms: list[float] = []
    flue_ms: list[float] = []
    rows = []
    for i, (cat, prompt) in enumerate(PROMPTS):
        # Separate, stable sessions per brain so each keeps its own context.
        pi_reply, p_ms = ask_pi(prompt, f"parity-pi-{i}")
        fl_reply, f_ms = ask_flue(prompt, f"parity-flue-{i}")
        pi_ms.append(p_ms)
        flue_ms.append(f_ms)
        rows.append((cat, prompt, pi_reply, p_ms, fl_reply, f_ms))

    print("=" * 100)
    print("BRAIN PARITY — current prod (/api/chat, Pi-CLI brain) vs Flue brain sidecar")
    print("=" * 100)
    for cat, prompt, pi_reply, p_ms, fl_reply, f_ms in rows:
        print(f"\n[{cat}]  {prompt}")
        print(f"  PROD ({p_ms:6.0f}ms): {pi_reply.strip()[:240]}")
        print(f"  FLUE ({f_ms:6.0f}ms): {fl_reply.strip()[:240]}")

    def summ(name: str, xs: list[float]) -> str:
        return f"{name}: median={statistics.median(xs):.0f}ms  max={max(xs):.0f}ms  n={len(xs)}"

    print("\n" + "=" * 100)
    print("LATENCY")
    print("  " + summ("PROD (/api/chat)", pi_ms))
    print("  " + summ("FLUE brain     ", flue_ms))
    print("\nNOTE: tool:* prompts (time/list/memory) are EXPECTED to differ — the Flue")
    print("brain (increment 1) is persona-only. That gap = the Phase-3 tools/memory work")
    print("that must land + parity-prove before any production cutover.")


if __name__ == "__main__":
    main()
