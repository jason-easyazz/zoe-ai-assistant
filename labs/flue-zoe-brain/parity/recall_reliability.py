#!/usr/bin/env python3
"""recall_memory tool-call reliability benchmark (LAB-ONLY).

The #1 cutover blocker for the Flue Zoe brain was that the local Gemma model
would answer "I don't remember" from its own head WITHOUT calling recall_memory
(a silent failure). The earlier parity run measured ~67% tool-call rate on
recall-style prompts.

This harness measures that rate directly and HONESTLY: it scores on whether a
`recall_memory` tool call actually FIRED in the agent's GET event stream — NOT on
the reply text. A reply that merely *sounds* like recall does not count.

Flue's Node event stream (GET /agents/zoe/:id → publicEventData) is a JSON LIST of
events, and a genuine tool firing is a `tool_start` event followed by a terminal
`tool` event, each carrying `toolName` (@flue/runtime FlueEventVariant). The scorer
is deliberately tolerant of shape drift so a real call is never undercounted as a
MISS: it accepts the history as a list OR an object wrapping an
events/messages/data/history array; recognises tool-fire events typed
tool_start/tool/tool_call/function_call (case- and separator-insensitive); and reads
the tool name from any of toolName/name/tool_name. It stays precise — only a genuine
recall_memory tool event counts, never a text mention.

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


# Event `type`s (normalized) that represent a tool/function actually FIRING. Flue's
# Node stream emits `tool_start` + a terminal `tool`; `tool_call`/`function_call` are
# tolerated for other adapters/wire shapes so a real call is never scored as a MISS.
_TOOL_EVENT_TYPES = frozenset({"tool_start", "tool", "tool_call", "function_call"})
# Keys a tool-fire event may carry the tool name under, across event shapes.
_TOOL_NAME_KEYS = ("toolName", "name", "tool_name")


def _norm_type(raw: object) -> str:
    """Lowercase and fold hyphens/spaces to underscores for tolerant type matching."""
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def _event_tool_name(event: dict) -> str:
    """The tool name an event carries (toolName/name/tool_name), or '' if none."""
    for key in _TOOL_NAME_KEYS:
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _is_tool_event(event: dict) -> bool:
    """True iff this event represents a tool/function actually firing."""
    return _norm_type(event.get("type")) in _TOOL_EVENT_TYPES


def _iter_events(history: object):
    """Yield event dicts from session history shaped as either a list of events or
    an object wrapping an events/messages/data/history array. Non-dict items skipped."""
    seq = history
    if isinstance(history, dict):
        seq = []
        for key in ("events", "messages", "data", "history"):
            val = history.get(key)
            if isinstance(val, list):
                seq = val
                break
    if not isinstance(seq, list):
        return
    for item in seq:
        if isinstance(item, dict):
            yield item


def recall_fired(history: object) -> bool:
    """True iff a recall_memory tool call actually fired (event stream, not text).

    Robust across event shapes (see module docstring) but precise: only a genuine
    recall_memory tool-fire event counts — never a text mention in a reply."""
    for event in _iter_events(history):
        if _is_tool_event(event) and _event_tool_name(event).lower() == "recall_memory":
            return True
    return False


def any_tool_fired(history: object) -> set:
    """Every tool name that actually fired in the session history (for honest output)."""
    names = set()
    for event in _iter_events(history):
        if _is_tool_event(event):
            name = _event_tool_name(event)
            if name:
                names.add(name)
    return names


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
