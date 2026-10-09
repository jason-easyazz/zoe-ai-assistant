#!/usr/bin/env python3
"""Z0n in a window: the night mind's reflection cells (K1-K12) and Z0's brain half of the memory protocol (M4.*.zoe), on a REAL model - or on the lab's fake brain.

What the bake-off could not say (decision record 2026-10-08): K2 / K3 SKIPped on Z0 because the lab scripted its nightly model, and rule M read "no data" because Z0 had
no ``protocol_brain`` baseline. This runs both against whatever server ``--clone-url`` points at:

    z0n_window.py --lab --out /tmp/z0n-lab.json                                   # no model: the fake nightly brain + the scripted Zoe brain (CI-shaped, seconds)
    z0n_window.py --clone-url http://127.0.0.1:11500 --ctx 16384 --out ...         # the real clone (the 4B at 8k, or the 12B window): K1-K12 + M4.*.zoe
    z0n_window.py --clone-url http://127.0.0.1:11434 --smoke-live --max-calls 20 --out ...   # <= 20 model calls against the live brain (the wrapper takes the lock)

Output JSON (counts only, no household text): ``{"mode", "model", "ctx", "k_cells": [rows in the runner's format], "k1": {judged, true, false, observations},
"night": {calls, prompt_tokens, completion_tokens, wall_s, tok_per_s}, "protocol_brain": {"cells": [M4 rows], "brain": {...}}, "calls": N}``. The measured decode rate is
``completion_tokens / wall_s`` over the calls (a lower bound: it includes prefill) - the 8-vs-33 tok/s question the night budget depends on.
Nothing here starts a brain, a service or a container; it never talks to anything but the loopback URL it is given.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "zmb"

from . import cells as cellmod, spec, z0_brain  # noqa: E402
from .arms.mpa_model import BudgetExhausted, HttpChatModel, loopback  # noqa: E402
from .arms.z0 import Z0Arm  # noqa: E402
from .runner import _row  # noqa: E402
from .world import make_world  # noqa: E402


class CountingModel:
    """The night mind talks to the server through ``night_mind``'s own HTTP client; this wraps the call SEAM to count model calls against a budget (the smoke's
    hard cap is enforced here, not hoped for) and to time them."""

    def __init__(self, max_calls: Optional[int] = None):
        self.max_calls, self.calls, self.seconds = max_calls, 0, 0.0
        self.prompt_tokens = self.completion_tokens = 0
        self.raw: "list[str]" = []                   # the replies (first 900 chars): SYNTHETIC household text only - this driver never touches a real member
        self.per_call: "list[dict]" = []            # {"s", "prompt", "completion"}: the decode-rate question (8 vs 33 tok/s) is answered from these

    def tick(self) -> None:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"model-call budget {self.max_calls} spent")
        self.calls += 1


def run_k(arm: Z0Arm, seed: str, guard: Callable[[], None], budget: Optional[CountingModel]) -> "tuple[list[dict], dict]":
    """K1-K12 on ``arm`` (Z0n). Returns (rows in the runner's format, the K1 judged / true / false counts)."""
    world = make_world(seed)
    rows, k1 = [], {}
    for cell in (c for c in spec.load_cells() if c.axis == "reflection"):
        guard()
        try:
            o = cellmod.run_cell(cell.rendered(world), world, arm)
        except BudgetExhausted:
            o = cellmod.Outcome("SKIP", reason="model-call budget spent")
        rows.append(_row(cell, o))
        if cell.id.startswith("K1."):
            ev = ((o.evidence.get("probes") or [{}])[0]).get("observations_judged") or {}
            k1 = {"judged": ev.get("decidable", ev.get("n")), "true": ev.get("true"), "false": ev.get("false"), "observations": ev.get("observations"),
                  "verdict": o.verdict}
    return rows, k1


def decode_estimate(per_call: "list[dict]", prefill_tok_s: float = 650.0) -> "Optional[float]":
    """The decode rate the calls imply: completion tokens / (call seconds minus the prefill at ``prefill_tok_s``), over the calls that generated at least 60 tokens
    (a short answer is all overhead). The live brain's real night rate: 8 tok/s (the run-2 measurement) or 33 (the mind-layer figure)? ``None`` when no call qualifies."""
    use = [c for c in per_call if c["completion"] >= 60 and c["s"] > c["prompt"] / prefill_tok_s]
    if not use:
        return None
    return round(sum(c["completion"] for c in use) / sum(c["s"] - c["prompt"] / prefill_tok_s for c in use), 1)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clone-url", default="")
    ap.add_argument("--lab", action="store_true", help="no model: the fake nightly brain and the scripted Zoe brain")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--model-name", default="")
    ap.add_argument("--seed", default="zmb-v1")
    ap.add_argument("--smoke-live", action="store_true")
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--no-k", action="store_true")
    ap.add_argument("--keep-raw", action="store_true", help="keep the model's replies (synthetic text) in the output; always on with --smoke-live")
    ap.add_argument("--no-brain", action="store_true", help="skip Z0's protocol_brain half")
    ap.add_argument("--quiet-since", type=float, default=None, help="epoch seconds: stop if the panel hears a wake word after this (the live brain is shared)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if not a.lab and not a.clone_url:
        ap.error("--clone-url is required unless --lab")
    out: "dict[str, Any]" = {"mode": "lab" if a.lab else "smoke-live" if a.smoke_live else "window", "ctx": a.ctx, "seed": a.seed, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    url = "" if a.lab else loopback(a.clone_url).rstrip("/")
    url = url[:-3] if url.endswith("/v1") else url
    out["model"] = a.model_name or ("fake" if a.lab else "clone")
    budget = CountingModel(a.max_calls or (20 if a.smoke_live else None))
    from .hm_window import PanelGuard
    panel = PanelGuard(a.quiet_since)
    from . import lab_driver
    lab_driver.load_service()          # pins every per-user store to a scratch directory and puts the service tree on the path, BEFORE night_mind is imported
    import night_mind as nm

    real_complete = nm._complete

    async def counted(messages, max_tokens, cfg, usage, **kw):
        panel()                                                    # a voice turn on the panel ends the run (SystemExit): the live brain is shared
        budget.tick()
        p0, c0 = usage["prompt_tokens"], usage["completion_tokens"]
        t0 = time.monotonic()
        try:
            text = await real_complete(messages, max_tokens, cfg, usage, **kw)
            if a.smoke_live or a.keep_raw:
                budget.raw.append(text[:900])
            return text
        finally:
            dt = time.monotonic() - t0
            budget.seconds += dt
            dp, dc = usage["prompt_tokens"] - p0, usage["completion_tokens"] - c0
            budget.prompt_tokens, budget.completion_tokens = budget.prompt_tokens + dp, budget.completion_tokens + dc
            budget.per_call.append({"s": round(dt, 2), "prompt": dp, "completion": dc})
    nm._complete = counted
    t0 = time.monotonic()
    try:
        if not a.no_k:
            arm = Z0Arm(name="Z0n", night=True, night_url=url, night_model=a.model_name, night_ctx=a.ctx)
            try:
                rows, k1 = run_k(arm, a.seed, lambda: None, budget)
            finally:
                arm.close()
            out["k_cells"], out["k1"] = rows, k1
            graded = [r for r in rows if r["verdict"] in ("PASS", "FAIL")]
            out["k_summary"] = {"pass": sum(1 for r in graded if r["verdict"] == "PASS"), "graded": len(graded), "fail": [r["id"] for r in graded if r["verdict"] == "FAIL"],
                                "skip": [r["id"] for r in rows if r["verdict"] == "SKIP"], "error": [r["id"] for r in rows if r["verdict"] == "ERROR"]}
        if not a.no_brain:
            arm = Z0Arm()
            try:
                model: Any = z0_brain.ScriptedZoeBrain(eager=False) if a.lab else HttpChatModel(url, model=a.model_name or "local", max_calls=None)
                counted_model = _BudgetedBrain(model, budget)
                out["protocol_brain"] = z0_brain.run_z0_protocol_brain(arm, counted_model, a.seed)
            finally:
                arm.close()
    except BudgetExhausted as exc:
        out["stopped"] = str(exc)
    finally:
        nm._complete = real_complete
    wall = time.monotonic() - t0
    out["calls"] = budget.calls
    out["night"] = {"wall_s": round(wall, 1), "model_s": round(budget.seconds, 1), "prompt_tokens": budget.prompt_tokens, "completion_tokens": budget.completion_tokens,
                    "completion_tok_per_model_s": round(budget.completion_tokens / budget.seconds, 2) if budget.seconds and budget.completion_tokens else None,
                    "decode_tok_s_est": decode_estimate(budget.per_call), "per_call": budget.per_call[:60], "raw": budget.raw[:30]}
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    a.out.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out.get(k) for k in ("mode", "model", "ctx", "calls", "k_summary", "k1", "stopped")}, default=str))
    return 0


class _BudgetedBrain:
    """Counts the Zoe-brain half's calls against the same budget."""
    name = "budgeted"

    def __init__(self, inner: Any, budget: CountingModel):
        self.inner, self.budget = inner, budget
        self.calls = 0

    def complete(self, messages, tools, *, max_tokens: int = 512):
        self.budget.tick()
        self.calls += 1
        t0 = time.monotonic()
        try:
            return self.inner.complete(messages, tools, max_tokens=max_tokens)
        finally:
            self.budget.seconds += time.monotonic() - t0


if __name__ == "__main__":
    raise SystemExit(main())
