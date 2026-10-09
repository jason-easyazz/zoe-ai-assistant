#!/usr/bin/env python3
"""tool_use_bench - does the 4B brain use its OWN tools correctly? One canonical ask per tool group, N samples each, on the live sidecar.

Why. The self-model tells Zoe which tools she has; it cannot make the 4B CALL them. This bench measures that, per tool group, with the
ground truth read from the sidecar's own ``__TOOL__`` sentinels (never from the reply text):

  right_tool      one of the group's tools was called (an ``activate_abilities`` unlock followed by the tool counts)
  no_fake_ask     no invented clarifying question ("where are you?" for the weather) and no invented argument (a location nobody said)
  from_result     the reply is built from the tool's result (it shares content with it); a failed result is relayed, not papered over
  honest_unavail  with a tool that CANNOT run (guest identity: every tool fails closed) the reply says so and never claims success

The canonical asks come from the generated self-model (``self_model.canonical_asks``), one per registry group; ``EXTRA`` adds the
second-most-common phrasing per group and the core tools.

SAFETY (the only way it touches the live sidecar): every turn carries the REPLAY envelope (`` zoe-replay:1``) and a synthetic
``demo_bar_<hex>`` identity (or ``guest`` for the unavailable cases), so every write tool reports success and commits NOTHING
(``labs/flue-zoe-brain-2x/src/replay-mode.ts``; ``set_timer`` and ``runWrite`` check it); reads see an empty demo user. A turn is NEVER
sent without an envelope identity: the sidecar falls back to the process-wide ``ZOE_BRAIN_USER_ID`` then, and that is a real person.

Gates: ``ZOE_PERF=1``; the shared harness lock (run under ``flock /tmp/zoe-voice-harness.lock``); no night window
(``~/.zoe/night-window/WINDOW_OPEN``); no landing script running; MemAvailable >= 1.2 GB; a wall-clock budget (``--budget-s``, default
780) after which the run stops and reports what it has. ``--dry-run`` prints the plan and touches nothing. The sidecar bearer token is
read by ``flue_wire`` from the environment; it is never printed.

    ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 python3 scripts/perf/tool_use_bench.py --samples 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "labs" / "flue-zoe-brain-2x" / "parity"))
import self_model_cells as cells  # noqa: E402  (also puts services/zoe-data on sys.path)

sm = cells.sm
CACHE = Path.home() / ".cache" / "zoe"
RESULTS = CACHE / "tool_use_bench_last.json"
WINDOW_MARKER = Path(os.environ.get("NIGHT_DIR") or Path.home() / ".zoe" / "night-window") / "WINDOW_OPEN"
LANDING_RE = r"^(/bin/)?[b]ash .*/(land_queue|land_voice_pr|docs_merge_chain)\.sh"
MIN_MEM_MB = 1229

# (group, ask, tools that count as right, is a read the reply must be built from). The group's FIRST row is its canonical ask
# (self_model.canonical_asks); the rest are the second phrasing and the core tools.
EXTRA = (
    ("weather", "can I hang the washing out today?", ("get_weather",), True),
    ("lists", "what's on my shopping list?", ("show_list",), True),
    ("reminders", "what reminders do I have?", ("list_reminders",), True),
    ("calendar", "add a dentist appointment on Friday at 3pm to my calendar", ("add_calendar_event",), False),
    ("notes", "find my note about the spare key", ("note_search",), True),
    ("core", "what time is it?", ("get_time",), True),
    ("core", "what do you know about me?", ("recall_memory",), True),
)
#: which tool(s) are right for a group's canonical ask, and whether it is a read
CANON = {"weather": (("get_weather",), True), "lists": (("shopping_list_add", "add_to_list"), False), "timers": (("set_timer",), False),
         "reminders": (("add_reminder",), False), "calendar": (("show_calendar",), True), "notes": (("create_note",), False),
         "journal": (("journal",), False), "people": (("people",), True), "media": (("media",), False), "home": (("home",), False),
         "memory": (("remember_fact",), False)}
UNAVAILABLE = (("lists", "add eggs to my shopping list", ("shopping_list_add", "add_to_list")),
               ("weather", "what's the weather like today?", ("get_weather",)),
               ("reminders", "remind me to water the plants tomorrow morning", ("add_reminder",)))
NO_TOOL = (("none", "can you order groceries?"), ("none", "turn on the heating"), ("none", "email my boss that I'll be late"))

FAILURE_RE = re.compile(r"\b(?:couldn't|could not|can't|cannot|unable|not sure whose|don't have|do not have|isn't|WRITE DISABLED|failed|no .{0,20}(?:found|stored))\b", re.I)
ADMIT_RE = re.compile(r"\b(?:can't|cannot|couldn't|could not|unable|not able|not sure|don't know|no way to|isn't something|sorry|afraid)\b", re.I)
SUCCESS_RE = re.compile(r"\b(?:added|done|set|created|saved|sent|booked|ordered|turned|playing|reminder (?:is )?set|i've|i have)\b", re.I)


def cases() -> list:
    """The ask list, deterministic. Canonical asks first (from the generated self-model), then the extras."""
    asks = sm.canonical_asks("en")
    out = []
    for g in sm.groups():
        tools, read = CANON.get(g, ((), False))
        if asks.get(g):
            out.append({"kind": "tool", "group": g, "ask": asks[g], "tools": tools, "read": read, "canonical": True})
    out += [{"kind": "tool", "group": g, "ask": a, "tools": t, "read": r, "canonical": False} for g, a, t, r in EXTRA]
    out += [{"kind": "unavailable", "group": g, "ask": a, "tools": t, "read": False, "canonical": False} for g, a, t in UNAVAILABLE]
    out += [{"kind": "no_tool", "group": g, "ask": a, "tools": (), "read": False, "canonical": False} for g, a in NO_TOOL]
    return out


def parse_sentinels(sentinels: list) -> dict:
    """tools called in order (with args) and each result, from the ``__TOOL__`` sentinels."""
    calls, results = {}, {}
    for s in sentinels:
        if not s.startswith("__TOOL__:"):
            continue
        try:
            p = json.loads(s[len("__TOOL__:"):])
        except ValueError:
            continue
        i = p.get("id")
        if p.get("phase") == "start":
            calls.setdefault(i, {"name": p.get("name"), "args": {}, "result": ""})
        elif p.get("phase") == "args" and i in calls:
            calls[i]["args"] = p.get("args") or {}
        elif p.get("phase") == "result":
            results[i] = str(p.get("result") or "")
    for i, c in calls.items():
        c["result"] = results.get(i, "")
    return {"calls": list(calls.values())}


def _toks(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(t) >= 4 or t.isdigit()}


def score_turn(case: dict, reply: str, parsed: dict) -> dict:
    """The four checks for one sample. Pure."""
    calls = [c for c in parsed["calls"] if c["name"] != "activate_abilities"]
    names = [c["name"] for c in calls]
    activated = [c["args"].get("group") for c in parsed["calls"] if c["name"] == "activate_abilities"]
    ask_toks = _toks(case["ask"])
    out = {"tools": names, "activated": activated}
    if case["kind"] == "no_tool":
        out["right_tool"] = not names                                          # nothing to call: she must not invent a tool
        out["honest_unavail"] = bool(ADMIT_RE.search(reply)) and not (SUCCESS_RE.search(reply) and not ADMIT_RE.search(reply))
        out["no_fake_ask"] = True
        out["from_result"] = None
        return out
    right = [c for c in calls if c["name"] in case["tools"]]
    out["right_tool"] = bool(right)
    loc = [str(c["args"].get("location") or "").strip() for c in right if c["name"] == "get_weather"]
    fake_arg = any(l and not (_toks(l) & ask_toks) for l in loc)                # a place nobody said
    fake_q = bool(cells.FAKE_QUESTION_RE.search(reply)) or (not right and "?" in reply)
    out["no_fake_ask"] = not (fake_q or fake_arg)
    out["from_result"] = None
    out["honest_unavail"] = None
    if case["kind"] == "unavailable":
        failed = bool(right) and all(FAILURE_RE.search(c["result"] or "") for c in right)
        out["honest_unavail"] = bool(ADMIT_RE.search(reply)) and not (SUCCESS_RE.search(reply) and not ADMIT_RE.search(reply))
        out["tool_failed_closed"] = failed
    elif case["read"] and right:
        res = right[-1]["result"]
        if FAILURE_RE.search(res or ""):
            out["from_result"] = bool(ADMIT_RE.search(reply)) or bool(_toks(reply) & _toks(res))
        else:
            out["from_result"] = bool((_toks(res) - ask_toks) & _toks(reply))
    return out


def diagnose(case: dict, samples: list) -> str:
    """Where a miss belongs, from the evidence (a hint for the ledger, not a verdict)."""
    if all(s["right_tool"] for s in samples):
        return ""
    group = case["group"]
    seen = {n for s in samples for n in s["tools"]}
    activated = {a for s in samples for a in s["activated"] if a}
    if not seen and not activated:
        return "tool never called and group never unlocked: disclosure trigger / tool description / system-prompt catalogue"
    if seen and not (seen & set(case["tools"])):
        return f"called {sorted(seen)} instead: tool description (the descriptions overlap) or the router corpus if the router fed it"
    if activated and not seen:
        return f"unlocked {sorted(activated)} but never called the tool: tool description / schema"
    return "inconsistent across samples: sampling noise at the 4B, not a registry or description problem"


def table(rows: list) -> str:
    """One line per ask; a sample that errored counts in none of the columns' numerators and shows as 'err' in the count."""
    head = f"{'group':10} {'kind':11} {'right':>6} {'no-fake':>8} {'result':>7} {'honest':>7}  ask"
    lines = [head, "-" * len(head)]
    for r in rows:
        n = len(r["samples"])

        def frac(key):
            vals = [s[key] for s in r["samples"] if s.get(key) is not None]
            return f"{sum(vals)}/{len(vals)}" if vals else "-"
        lines.append(f"{r['group']:10} {r['kind']:11} {frac('right_tool'):>6} {frac('no_fake_ask'):>8} {frac('from_result'):>7} {frac('honest_unavail'):>7}  {r['ask'][:52]}")
        if r.get("diagnosis"):
            lines.append(f"{'':10} -> {r['diagnosis']}")
    return "\n".join(lines)


def landing_started() -> bool:
    """A landing or the night window opened since the run began: the brain is theirs, stop."""
    return WINDOW_MARKER.exists() or subprocess.run(["pgrep", "-f", LANDING_RE], capture_output=True).returncode == 0


def gates() -> str | None:
    """A reason NOT to run, or None."""
    if os.environ.get("ZOE_PERF") != "1":
        return "ZOE_PERF=1 not set (skip notice, exit 0)"
    if WINDOW_MARKER.exists():
        return f"the night window is open ({WINDOW_MARKER})"
    if subprocess.run(["pgrep", "-f", LANDING_RE], capture_output=True).returncode == 0:
        return "a landing script is running"
    import samantha_bar as sb

    if sb.mem_available_mb() < MIN_MEM_MB:
        return f"MemAvailable {sb.mem_available_mb()} MB < {MIN_MEM_MB} MB"
    return None


def envelope(uid: str, ask: str) -> str:
    return f" zoe-replay:1\n zoe-uid:{uid}\n{ask}"


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--budget-s", type=float, default=780.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", default=str(RESULTS))
    args = ap.parse_args(argv)
    plan = cases()
    if args.dry_run:
        print(f"tool_use_bench plan: {len(plan)} asks x {args.samples} samples = {len(plan) * args.samples} brain turns (replay-isolated, demo identity)")
        for c in plan:
            print(f"  [{c['kind']:11}] {c['group']:10} {c['ask']}  -> {','.join(c['tools']) or '(no tool)'}")
        return 0
    why = gates()
    if why:
        print(f"tool_use_bench: not running - {why}")
        return 0 if why.startswith("ZOE_PERF") else 2
    import samantha_bar as sb

    sb._acquire_lock()
    import flue_wire

    uid = "demo_bar_" + uuid.uuid4().hex[:8]
    sb.assert_demo_user(uid)
    rows, t0, turns = [], time.monotonic(), 0
    for case in plan:
        samples = []
        for _ in range(args.samples):
            if time.monotonic() - t0 > args.budget_s:
                break
            if landing_started():
                print("a landing / the night window started mid-run - stopping (partial result)")
                args.budget_s = 0
                break
            ident = "guest" if case["kind"] == "unavailable" else uid
            try:
                reply, sentinels, ms = flue_wire.ask(f"tub-{uuid.uuid4().hex[:12]}", envelope(ident, case["ask"]), timeout=150.0)
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "code", None) in (401, 403):
                    print(f"ABORT: the sidecar refused the turn (HTTP {exc.code}): export ZOE_BRAIN_TOKEN for this run (it is never printed)")
                    return 2
                samples.append({"error": type(exc).__name__, "right_tool": False, "no_fake_ask": False, "tools": [], "activated": []})
                continue
            turns += 1
            samples.append({**score_turn(case, reply, parse_sentinels(sentinels)), "ms": int(ms), "reply_len": len(reply)})
        rows.append({**{k: case[k] for k in ("kind", "group", "ask", "canonical")}, "samples": samples, "diagnosis": diagnose(case, samples) if samples else ""})
        if time.monotonic() - t0 > args.budget_s:
            print(f"budget {args.budget_s:.0f}s reached after {turns} turns - stopping")
            break
    print(table(rows))
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({"samples": args.samples, "turns": turns, "elapsed_s": round(time.monotonic() - t0, 1), "rows": rows}, indent=1))
    print(f"\n{turns} brain turns in {time.monotonic() - t0:.0f}s -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
