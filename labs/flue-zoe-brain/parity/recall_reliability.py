#!/usr/bin/env python3
"""recall_memory tool-call reliability benchmark (LAB-ONLY).

The #1 cutover blocker for the Flue Zoe brain was that the local Gemma model
would answer "I don't remember" from its own head WITHOUT calling recall_memory
(a silent failure). The earlier parity run measured ~67% tool-call rate on
recall-style prompts.

This harness measures that rate directly and HONESTLY: it scores on whether a
`recall_memory` tool call actually FIRED — by inspecting the agent's GET event
stream for a `tool_start` (or persisted `tool`) event with
`toolName == "recall_memory"` — NOT on the reply text. A reply that merely
*sounds* like recall does not count.

Run (with the Flue brain serving on :3578):
    python3 parity/recall_reliability.py

Keep it modest/sequential (one shared GPU) — no concurrency.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:3578"
POST_URL = BASE + "/agents/zoe/{sid}?wait=result"
GET_URL = BASE + "/agents/zoe/{sid}"

# Varied phrasings that SHOULD trigger recall_memory — the model must consult
# stored memory rather than guess or claim ignorance from its own head.
PROMPTS = [
    "What do you know about me?",
    "Remember anything about me?",
    "What's my name?",
    "Do you know my preferences?",
    "Tell me about myself.",
    "What have you got stored about me?",
    "Who am I?",
    "What do you remember about me?",
    "Do you know anything about me?",
    "What can you tell me about myself?",
    "What's my lucky number?",
    "Do you remember what I like?",
    "What are my preferences?",
    "What do you know about my family?",
    "Have I told you anything about myself?",
    "What's stored in your memory about me?",
    "Do you know who I am?",
    "Remind me what you know about me.",
    "What details do you have on me?",
    "Tell me what you remember about me.",
    "Do you know my name?",
    "What do you know about my likes and dislikes?",
    "Anything you remember about me?",
    "What kind of music do I like?",
    "Do you have anything on file about me?",
    "What have I shared with you about myself?",
    "Recall what you know about me.",
    "What's my favourite kind of music?",
    "Do you remember my birthday or anything like that?",
    "Sum up what you know about me.",
    "What personal stuff do you remember about me?",
    "Do you know anything about my habits?",
]


def _post(url: str, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url: str, timeout: float = 30.0) -> list:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def recall_fired(events: list) -> bool:
    """True iff a recall_memory tool call actually fired (event stream, not text)."""
    for e in events:
        if e.get("type") in ("tool_start", "tool") and e.get("toolName") == "recall_memory":
            return True
    return False


def any_tool_fired(events: list) -> set:
    return {
        e.get("toolName")
        for e in events
        if e.get("type") in ("tool_start", "tool") and e.get("toolName")
    }


def main() -> None:
    n = len(PROMPTS)
    fired = 0
    rows = []
    print(f"recall_memory reliability — {n} trials (sequential)\n")
    for i, prompt in enumerate(PROMPTS):
        sid = f"recall-bench-{int(time.time())}-{i}"
        try:
            body = _post(POST_URL.format(sid=sid), {"message": prompt})
        except Exception as exc:  # noqa: BLE001
            rows.append((prompt, "ERR", f"post failed: {exc}", set()))
            print(f"[{i+1:2d}/{n}]  ERR   {prompt!r}  ({exc})")
            continue
        res = body.get("result", body)
        text = (res.get("text") if isinstance(res, dict) else str(res)) or ""
        # small settle so the persisted event log is complete
        time.sleep(0.3)
        try:
            events = _get(GET_URL.format(sid=sid))
        except Exception as exc:  # noqa: BLE001
            events = []
        ok = recall_fired(events)
        tools = any_tool_fired(events)
        if ok:
            fired += 1
        rows.append((prompt, "PASS" if ok else "MISS", text, tools))
        flag = "PASS" if ok else "MISS"
        print(f"[{i+1:2d}/{n}]  {flag}  tools={sorted(tools) or '-'}  {prompt!r}")
        print(f"          reply: {text.strip()[:130]}")

    pct = 100.0 * fired / n if n else 0.0
    print("\n" + "=" * 80)
    print(f"recall_memory FIRED: {fired}/{n}  =  {pct:.0f}%")
    print("=" * 80)
    # Surface the misses for honest inspection.
    misses = [(p, t, tl) for (p, st, t, tl) in rows if st == "MISS"]
    if misses:
        print("\nMISSES (no recall_memory tool call):")
        for p, t, tl in misses:
            print(f"  - {p!r}  tools={sorted(tl) or '-'}")
            print(f"      reply: {t.strip()[:160]}")

    # machine-readable summary line for scripting
    print(f"\nSUMMARY {{\"fired\": {fired}, \"n\": {n}, \"pct\": {pct:.1f}}}")
    sys.exit(0)


if __name__ == "__main__":
    main()
