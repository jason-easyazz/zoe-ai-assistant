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
import os
import sys
import time
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

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
    """Tool names invoked *during the prompt operation*, in order.

    We count `tool_start` events (one per invocation) and read their `toolName`,
    but scope them to the `prompt` operation so setup/lifecycle/multi-operation
    tool events can't inflate the count. When the stream exposes operation
    boundaries (`operation_start`/`operation_end` with an `operationKind`/`kind`),
    we only count tool starts while inside a `prompt` operation; a tool start in a
    non-prompt operation is ignored. Older/simpler streams emit no operation
    markers at all — then `in_prompt` stays `None` and we count every tool start,
    preserving the original behaviour.
    """
    names: list[str] = []
    in_prompt: bool | None = None  # None => no operation markers seen yet
    for e in events:
        etype = str(e.get("type") or "").strip().lower().replace("-", "_")
        if etype in ("operation_start", "operationstart"):
            kind = str(e.get("operationKind") or e.get("kind") or "").strip().lower()
            in_prompt = kind == "prompt"
            continue
        if etype in ("operation_end", "operationend"):
            in_prompt = False
            continue
        if etype == "tool_start":
            if in_prompt is False:
                continue  # tool fired outside the prompt operation — don't count it
            tn = e.get("toolName")
            if tn:
                names.append(tn)
    return names


def reply_text(body: dict) -> str:
    res = body.get("result", body)
    if isinstance(res, dict):
        return (res.get("text") or "").strip()
    return str(res).strip()


def assert_dry_run() -> None:
    """Fail loudly unless the live sidecar is confirmed to have writes DISABLED.

    This harness later sends mutation-shaped ``shopping_list_add`` prompts. If the
    sidecar was started with ``ZOE_BRAIN_ALLOW_WRITES=true`` those trials would add
    REAL items while we only report tool-call reliability, so we gate on dry-run
    first.

    The preflight is deliberately NON-MUTATING. Earlier it sent a real
    ``shopping_list_add`` prompt and inferred dry-run from the tool's
    ``WRITE DISABLED`` marker — but on a misconfigured (writes-ENABLED) sidecar
    that probe could itself add the sentinel item before this code could read the
    stream and abort, i.e. the safety check performed the very mutation it exists
    to prevent. Instead we require the operator to positively attest that the
    sidecar is read-only, and we only ever exercise a READ-ONLY tool here:

    - ``TOOL_REL_WRITES_CONFIRMED_OFF=1`` is REQUIRED. It is a human attestation
      that the sidecar was launched with ``ZOE_BRAIN_ALLOW_WRITES=false``. Without
      it we refuse to send any write-shaped prompt (fail closed) — there is no
      prompt-path way to read write-state without risking a write, so the operator
      must vouch for it.
    - We still send ONE probe, but a read-only ``show_list`` request (never a
      write tool), purely to confirm the sidecar is reachable and answering before
      the run starts. Even on a writes-ENABLED sidecar this cannot mutate anything.
    - ``TOOL_REL_SKIP_WRITE_CHECK=1`` additionally skips the reachability probe for
      environments where even the read call is undesirable; the attestation above
      is still required.

    RESIDUAL RISK (by design, on the operator): the attestation is TRUSTED, not
    verified. If it is set against a sidecar that is actually writes-ENABLED, the
    ``show_list`` probe cannot detect the mismatch, and the benchmark's own
    ``shopping_list_add`` reliability prompts (sent later in ``main``) can then add
    real items. This is unavoidable for a client-side, prompt-only harness — there
    is no non-mutating prompt that reveals server write-state — so the guarantee is
    only ever as good as the operator's attestation. What this preflight removes is
    the sharper footgun of the *safety check itself* performing a write; it does
    not (and cannot) make a mis-attested run safe.
    """
    if os.environ.get("TOOL_REL_WRITES_CONFIRMED_OFF") != "1":
        sys.exit(
            "ABORT: refusing to send write-shaped prompts without a dry-run "
            "attestation. Start the sidecar with ZOE_BRAIN_ALLOW_WRITES=false and "
            "set TOOL_REL_WRITES_CONFIRMED_OFF=1 to confirm. (There is no "
            "non-mutating prompt that can verify write-state from the client, so "
            "the operator must vouch for it.)"
        )

    if os.environ.get("TOOL_REL_SKIP_WRITE_CHECK") == "1":
        print(
            "dry-run ATTESTED (TOOL_REL_WRITES_CONFIRMED_OFF=1); reachability probe "
            "SKIPPED (TOOL_REL_SKIP_WRITE_CHECK=1)."
        )
        return

    # Read-only reachability probe. `show_list` never writes, so this is safe even
    # if the operator's attestation is wrong — no sentinel item can be added.
    sid = f"rel-probe-{uuid.uuid4().hex[:12]}"
    try:
        _post(POST_URL.format(sid=sid), {"message": "show me my shopping list"})
        _get(GET_URL.format(sid=sid))
    except Exception as exc:  # sidecar unreachable / errored
        sys.exit(f"ABORT: could not reach the sidecar for the preflight probe ({exc}).")
    print(
        "dry-run ATTESTED (TOOL_REL_WRITES_CONFIRMED_OFF=1); sidecar reachable "
        "(read-only show_list probe)."
    )


def main() -> None:
    # tool -> {"correct": n, "total": n}
    per_tool = defaultdict(lambda: {"correct": 0, "total": 0})
    misfires: list[dict] = []
    rows: list[dict] = []

    total_correct = 0
    total_calls = 0

    # Refuse to send write-shaped prompts unless the sidecar is confirmed dry-run.
    assert_dry_run()

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
    # Write next to this script (not cwd-relative) so the summary lands correctly
    # regardless of the directory the harness was invoked from.
    out_path = Path(__file__).parent / "tool_reliability_last.json"
    with out_path.open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
