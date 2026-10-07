#!/usr/bin/env python3
"""The MPA / HMA / ZMA arms' measurement inside a bake-off window (and the same code as the brain-free LAB run and the <= 30 call live-brain SMOKE).

What it measures, each its own key in the JSON the window reads back (the contract ``bakeoff_measure.phase_mpa`` reads):

* ``mpa_cells``  - the lab cells with their negative controls (scripted brain, REAL ``mempalace-mcp`` server) + the BRAIN cells (the clone brain operates the tools): the four protocol
                   metrics ``M4.<metric>.mempalace5``, tool-call validity, supersede correctness, exact words and two-fact questions through the agent.
* ``generic``    - the spec's store-tier cells on the arm for ONE seed, time-boxed, in the runner's row format (the window folds them into the axes table and the winner clause).
* ``brain``      - model calls, prompt-token peak, tool calls / valid, searched-before-answering, supersede counts, model seconds.
* ``driver``     - the MemPalace server's RSS (steady and high-water: the G0 number with the brain driving it), per-tool latency, the arm's process growth, the shared-embedder saving.
* ``reflect``    - with ``--reflect-only``: the reflection (K) cells and the closet / consolidation pass against the clone it is pointed at (the night phase variants).

Modes: window (default; the clone brain is ``--clone-url``), ``--lab`` (no brain: the scripted stand-in and, for the closet pass, a scripted loopback LLM server; the arm still
drives the REAL MemPalace server) and ``--smoke-live`` (at most ``--max-calls`` model calls, default 30, against ``--clone-url``; the wrapper ``mpa_smoke.sh`` takes the brain lock).
Nothing here starts a brain, a service or a container; the MemPalace server is the arm's own child process, stopped on every exit path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zmb import cells as cellmod, mpa_cells, spec  # noqa: E402
from zmb.arms.mempalace_agent import MemPalaceAgentArm, mpa_glue_lines  # noqa: E402
from zmb.arms.mpa_model import BudgetExhausted, HttpChatModel, ScriptedChatModel, ScriptedLLMServer  # noqa: E402
from zmb.arms.mpa_palace import library_available  # noqa: E402
from zmb.hm_window import PanelGuard, hwm_mb, pss_mb  # noqa: E402
from zmb.runner import _row  # noqa: E402
from zmb.world import make_world  # noqa: E402

STUB_PORT = 11511


class ServerSampler:
    """Samples the RSS of every MemPalace server this driver's arms started (children whose command line names ``mempalace-mcp`` and a ``zmb-mpa-`` palace): the G0 number with the brain
    driving them. ``steady`` = the median of the summed RSS while at least one server ran, ``peak`` = its maximum, ``servers`` = the most that ran at once."""

    def __init__(self, period_s: float = 1.0):
        import threading
        self.period, self.samples, self.max_servers, self._stop = period_s, [], 0, threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def scan() -> "tuple[float, int]":
        total, n = 0.0, 0
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                if "mempalace-mcp" in cmd and "zmb-mpa-" in cmd:
                    for line in (d / "status").read_text().splitlines():
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1]) / 1024.0
                            n += 1
            except (OSError, ValueError):
                continue
        return total, n

    def _run(self) -> None:
        while not self._stop.wait(self.period):
            t, n = self.scan()
            if n:
                self.samples.append(t)
                self.max_servers = max(self.max_servers, n)

    def start(self) -> "ServerSampler":
        self._t.start()
        return self

    def summary(self) -> "dict[str, float]":
        xs = sorted(self.samples)
        return {"server_rss_steady_mb": round(xs[len(xs) // 2], 1) if xs else 0.0, "server_rss_peak_mb": round(xs[-1], 1) if xs else 0.0, "servers": self.max_servers,
                "server_samples": len(xs)}

    def stop(self) -> None:
        self._stop.set()


def percentile(xs: "list[float]", q: float) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(round(q * len(s) + 0.5)) - 1))], 2) if s else 0.0


def build_arm(kind: str, *, model: Any, embed_url: str = "", closet_url: str = "", closet_scripted: bool = False, hindsight_url: str = "", pg: Any = None,
              tokenizer: Any = None, workdir: "Path | None" = None) -> Any:
    """The REAL arm: the real MemPalace server per account, the brain ``model`` operating it; HMA over the window's Hindsight, ZMA over the lab's Z0e."""
    kw: "dict[str, Any]" = dict(model=model, embed_url=embed_url, closet_url=closet_url, closet_scripted=closet_scripted, tokenizer=tokenizer)
    if workdir is not None:
        kw["work_dir"] = workdir
    if kind == "MPA":
        return MemPalaceAgentArm(**kw)
    if kind == "HMA":
        from zmb.arms.hindsight import HindsightClient
        from zmb.arms.hma import HMAArm, ReflectiveTier
        return HMAArm(MemPalaceAgentArm(**kw), ReflectiveTier(HindsightClient(hindsight_url), pg=pg))
    if kind == "ZMA":
        from zmb.arms.zma import BRAIN_TOOLS, ZMAArm
        kw.update(tool_names=BRAIN_TOOLS, protocol_rules=(1, 2, 3), aaak=False, rules_paragraph=False)
        return ZMAArm(mpa=MemPalaceAgentArm(**kw), embed_url=embed_url)
    raise ValueError(kind)


def run_generic(kind: str, mk, seed: str, box_s: float, smoke: int, guard) -> dict:
    from zmb.bakeoff_measure import interleave, pick_smoke
    store = [c for c in spec.load_cells() if c.tier == "store"]
    world = make_world(seed)
    arm = mk()
    ordered = pick_smoke(store, smoke) if smoke else interleave(store)
    t0 = time.monotonic()
    rows = []
    try:
        for cell in ordered:
            guard()
            if time.monotonic() - t0 >= box_s:
                o = cellmod.Outcome("SKIP", reason="time box reached")
            else:
                try:
                    o = cellmod.run_cell(cell.rendered(world), world, arm)
                except BudgetExhausted:
                    o = cellmod.Outcome("SKIP", reason="model-call budget spent")
            rows.append(_row(cell, o))
    finally:
        arm.close()
    ran = sum(1 for r in rows if r["verdict"] != "SKIP")
    return {"seed": seed, "rows": rows, "cells_ran": ran, "cells_selected": len(rows), "duration_s": round(time.monotonic() - t0, 1), "unreachable": False}


def run_reflect(kind: str, mk, seed: str, guard) -> dict:
    """The night phase: the K (reflection) cells with the closet / consolidation pass running against whatever brain ``mk`` is pointed at."""
    world = make_world(seed)
    arm = mk()
    cells = [c for c in spec.load_cells() if c.tier == "store" and c.axis == "reflection"]
    t0 = time.monotonic()
    rows = []
    try:
        for c in cells:
            guard()
            rows.append(_row(c, cellmod.run_cell(c.rendered(world), world, arm)))
        m = arm.mpa if hasattr(arm, "mpa") else arm
        st = m.tool_stats()
        return {"k_cells": rows, "wall_s": round(time.monotonic() - t0, 1), "model_calls": st["model_calls"], "tool_calls": st["tool_calls"], "tool_calls_valid": st["tool_calls_valid"],
                "prompt_tokens_max": st["prompt_tokens_max"], "closet": dict(m.closet_stats)}
    finally:
        arm.close()


SMOKE_TURNS = (
    ("statement", "Please remember that my friend Aldo lives in Bergvik."), ("statement", "My dentist is Dr Okonkwo and the surgery is on Elm Street."),
    ("needed", "Where does Aldo live?"), ("needed", "Who is my dentist?"), ("change", "Aldo has moved to Oldmere now."), ("needed", "Where does Aldo live these days?"),
    ("unneeded", "turn on the kitchen lights"), ("silent", "What does Brigid drive?"), ("statement", "My sister Tove plays the cello on Tuesdays."), ("needed", "What instrument does Tove play?"))


def run_smoke(arm: Any, model: Any, out: dict, guard) -> None:
    """10 synthetic household turns, the brain operating MemPalace's tools (the wrapper takes the brain lock; ``model`` has the call budget)."""
    arm.reset(mpa_cells.USER)
    arm.mc.router_bypass = False                      # the quiet turn must reach the brain to be judged
    rows, stopped = [], ""
    for kind, text in SMOKE_TURNS:
        guard()
        try:
            tr = arm.converse(text)
        except BudgetExhausted:
            stopped = f"model-call budget spent before: {text!r}"
            break
        rows.append({"kind": kind, "text": text, "model_calls": tr.model_calls, "tools": [{"name": t.name, "valid": t.valid, "errors": t.errors[:2], "extra": t.extra, "refused": t.refused,
                                                                                          "ms": round(t.ms, 1)} for t in tr.tools], "searched": tr.searched_before_answer,
                     "reply": tr.reply[:160], "seconds": round(tr.seconds, 2), "prompt_tokens": tr.prompt_tokens, "filter_misses": tr.filter_misses})
    st = arm.tool_stats()
    kg = [t for t in arm._kg_dump()]
    needed = [r for r in rows if r["kind"] in ("needed", "silent")]
    aldo = [t for t in kg if str(t["subject"]).lower() == "aldo"]
    sup_ok = any(str(t["object"]).lower() == "oldmere" and not t["valid_to"] for t in aldo) and all(not (str(t["object"]).lower() == "bergvik" and not t["valid_to"]) for t in aldo)
    drawers = arm.stats()["rows"]
    out["smoke"] = {"turns": rows, "stopped": stopped, "model_calls": model.calls, "tokenize_calls": getattr(model, "tokenize_calls", 0), "tool_stats": st,
                    "search_before_answer": [sum(1 for r in needed if r["searched"]), len(needed)],
                    "quiet_on_device_turn": [r for r in rows if r["kind"] == "unneeded"][0]["searched"] is False if any(r["kind"] == "unneeded" for r in rows) else None,
                    "supersede_correct": sup_ok, "kg_after": [[str(t["subject"]), str(t["predicate"]), str(t["object"]), t["valid_to"]] for t in kg][:12],
                    "drawers": [[r.get("wing"), r.get("room"), r["text"][:70], r["authority_class"]] for r in drawers if r.get("origin", "").startswith("brain:") and r.get("room") != "diary"][:12],
                    "wings_used": sorted({str(r.get("wing")) for r in drawers if r.get("wing")}), "rooms_used": sorted({str(r.get("room")) for r in drawers if r.get("room")}),
                    "server_rss": arm.server_rss(), "tool_ms": {k: {"n": len(v), "p50": percentile(v, 0.5), "p95": percentile(v, 0.95)} for k, v in arm._need().tool_ms.items()},
                    "prompt_cost": arm.prompt_cost()}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arm", choices=("MPA", "HMA", "ZMA"), default="MPA")
    ap.add_argument("--clone-url", default="http://127.0.0.1:11500")
    ap.add_argument("--hindsight-url", default="http://127.0.0.1:18888")
    ap.add_argument("--embedder-url", default=os.environ.get("ZMB_HM_EMBEDDER_URL", ""))
    ap.add_argument("--seed", default="zmb-v1")
    ap.add_argument("--box-s", type=float, default=300.0)
    ap.add_argument("--smoke", type=int, default=0, help="test hook: this many generic cells spread over the axes")
    ap.add_argument("--lab", action="store_true", help="no brain: the scripted stand-in + a scripted loopback LLM for the closet pass")
    ap.add_argument("--stub-embedder", action="store_true", help="a ~10 MB deterministic loopback embedder instead of the model (plumbing and RAM, not retrieval quality)")
    ap.add_argument("--smoke-live", action="store_true", help="10 turns against --clone-url, at most --max-calls model calls")
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--reflect-only", action="store_true")
    ap.add_argument("--ctx", type=int, default=0, help="informational: the context size of the clone being pointed at")
    ap.add_argument("--model-name", default="")
    ap.add_argument("--controls", choices=("all", "none"), default="all")
    ap.add_argument("--no-pg", action="store_true")
    ap.add_argument("--quiet-since", type=float, default=None)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    out: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "arm": a.arm, "mode": "lab" if a.lab else "smoke-live" if a.smoke_live else "window", "clone_url": a.clone_url}
    if not library_available():
        out["skipped"] = "the real MemPalace server is not available here (run in an environment with the bake-off venv)"
        a.out.write_text(json.dumps(out))
        return 0
    guard = PanelGuard(a.quiet_since)
    srv = stub = None
    try:
        import mempalace  # noqa: F401  (only to record the version when running inside the venv)
        out["library"] = f"mempalace {getattr(mempalace, '__version__', '?')}"
    except ImportError:
        out["library"] = "mempalace 3.10.0 (server child; not importable in this interpreter)"
    base_pss = pss_mb()
    sampler = ServerSampler().start()
    try:
        embed_url = a.embedder_url
        if (a.lab or a.stub_embedder) and not embed_url:                                   # a deterministic loopback embedder: the lab measures plumbing and RAM, not retrieval
            import threading
            from http.server import ThreadingHTTPServer
            from zmb import stub_embed
            stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), stub_embed.Handler)
            threading.Thread(target=stub.serve_forever, daemon=True).start()
            embed_url = f"http://127.0.0.1:{STUB_PORT}"
        if embed_url:
            os.environ["ZMB_HM_EMBEDDER_URL"] = embed_url              # the lab cells' arms ask the same loopback embedder
        closet_url, closet_scripted = "", False
        if a.lab:
            srv = ScriptedLLMServer()
            model: Any = ScriptedChatModel("diligent")
            closet_url, closet_scripted = srv.url, True
        else:
            model = HttpChatModel(a.clone_url, max_calls=a.max_calls if a.smoke_live else None, model=a.model_name or "local")
            closet_url = a.clone_url.rstrip("/") + "/v1"
        out["model"] = a.model_name or ("scripted" if a.lab else "clone")
        pg = None
        if a.arm == "HMA" and not a.no_pg and not a.lab:
            from zmb.arms.pg_store import PG_CONTAINER, ScratchPostgres
            pg = ScratchPostgres(PG_CONTAINER)
        tok = (lambda t: model.count_tokens(t)) if not a.lab and hasattr(model, "count_tokens") else None
        mk = lambda: build_arm(a.arm, model=model, embed_url=embed_url, closet_url=closet_url, closet_scripted=closet_scripted, hindsight_url=a.hindsight_url, pg=pg, tokenizer=tok)  # noqa: E731
        if a.smoke_live:
            arm = mk()
            try:
                arm = arm.mpa if hasattr(arm, "mpa") else arm
                run_smoke(arm, model, out, guard)
            finally:
                arm.close()
        elif a.reflect_only:
            out["reflect"] = run_reflect(a.arm, mk, a.seed, guard)
            out["reflect"]["model"], out["reflect"]["ctx"] = out["model"], a.ctx
        else:
            lab = mpa_cells.run_all(a.arm, "library", controls=a.controls, guard=guard, workdir=None)
            cells_out = lab
            if not a.lab:
                arm = mk()
                try:
                    brain = mpa_cells.run_brain(arm, a.seed, guard=guard)
                finally:
                    arm.close()
                cells_out = {"summary": {**lab["summary"], "pass": lab["summary"]["pass"] + brain["summary"]["pass"], "graded": lab["summary"]["graded"] + brain["summary"]["graded"],
                                         "fail": lab["summary"]["fail"] + brain["summary"]["fail"], "skipped": lab["summary"]["skipped"] + brain["summary"]["skipped"]},
                             "cells": lab["cells"] + brain["cells"]}
                out["brain"] = {"model_calls": model.calls, "prompt_tokens_max": model.prompt_tokens_max, "model_s_total": round(model.seconds_total, 1), **brain["summary"]["tools"]}
                b2 = next((r for r in brain["cells"] if r["id"] == "MPA-B2.supersede_correct"), None)
                if b2:
                    out["brain"]["supersede"] = b2["evidence"]["supersede"]
            out["mpa_cells"] = cells_out
            if a.box_s > 0 and not a.lab:
                out["generic"] = run_generic(a.arm, mk, a.seed, a.box_s, a.smoke, guard)
        out["driver"] = {"pss_before_mb": round(base_pss, 1), "pss_after_mb": round(pss_mb(), 1), "peak_rss_mb": round(hwm_mb(), 1), "glue_lines": mpa_glue_lines(),
                         "pss_added_mb": round(max(0.0, pss_mb() - base_pss), 1), **sampler.summary()}
        if a.arm == "ZMA":
            out["driver"]["embedder_shared_mb"] = 120.0 if embed_url else 0.0       # the RAM lab's lower bound for the second ONNX session ZMA no longer loads (measured 120..191 MB); 0 = no shim, nothing shared
            out["driver"]["embedder_shared_basis"] = "RAM lab 2026-10-06: a second MiniLM session is +120 (measured) to +191 MB (the driver's growth over the cells)"
    except SystemExit as exc:
        out["aborted"] = str(exc)
    except NotImplementedError as exc:
        out["skipped"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - the window reads whatever was measured
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sampler.stop()
        for s in (srv,):
            if s is not None:
                s.close()
        if stub is not None:
            stub.shutdown()
            stub.server_close()
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    a.out.write_text(json.dumps(out, default=str, indent=1))
    print(f"mpa_window: wrote {a.out} ({'aborted: ' + out['aborted'] if out.get('aborted') else 'error: ' + out['error'] if out.get('error') else 'skipped: ' + out['skipped'][:80] if out.get('skipped') else 'ok'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
