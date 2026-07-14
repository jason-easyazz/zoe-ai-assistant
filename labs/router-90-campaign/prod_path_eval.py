#!/usr/bin/env python3
"""Production-path eval: the frozen 81-case corpus THROUGH the real
`semantic_router.route_two_stage()` (SetFit head gate 0.5 + FunctionGemma r2
sidecar on :11436) — not the lab harness.

Gates (the router-90 campaign acceptance bar):
  overall accuracy      >= 90.0 %
  chat false positives  == 0
  latency p50 (wall)    <  600 ms

Every decision whose `source` != 'two_stage' ('gate_abstain',
'shortlist_miss', 'error_fallback') is a BRAIN FALLBACK: the router made no
tool call and the turn goes to the Gemma brain. For scoring that is a
no-call (correct iff the case expects general_chat); the fallback rate is
reported per source.

Scoring/corpus are identical to labs/needle-benchmark /
labs/two-stage-router-eval (imported, not copied).

The sidecar is expected on :11436. Pass --launch-sidecar to start it ad hoc
from the r2 GGUF (killed on exit); same memory-gate + identity-check
discipline as the sibling labs.

Usage (repo root):
  python3 labs/router-90-campaign/prod_path_eval.py --launch-sidecar
  python3 labs/router-90-campaign/prod_path_eval.py --stub   # contract smoke
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
REPO = LABS.parent
sys.path.insert(0, str(LABS / "two-stage-router-eval"))
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
from run_two_stage import (  # noqa: E402
    CORPUS, MIN_AVAILABLE_MB, mem_available_mb, score, start_server,
    summarize)

SIDECAR_PORT = int(os.environ.get("ZOE_ROUTER_SIDECAR_PORT", "11436"))
GGUF_R2 = os.environ.get(
    "GGUF_R2",
    "/home/zoe/models/lab/functiongemma-270m-zoe-functok-r2-Q8_0.gguf")
FALLBACK_SOURCES = ("gate_abstain", "shortlist_miss", "error_fallback")

# Acceptance gates
OVERALL_MIN_PCT = 90.0
CHAT_FP_MAX_PCT = 0.0
P50_MAX_MS = 600.0


def _stub_route_two_stage(text: str):
    """Contract stub — structural testing only, never a real measurement."""
    from dataclasses import dataclass

    @dataclass
    class RouterDecision:
        tool: str | None
        args: dict
        confidence: float
        source: str
        latency_ms: float

    return RouterDecision(tool=None, args={}, confidence=0.0,
                          source="error_fallback", latency_ms=0.0)


def load_router(stub: bool):
    if stub:
        return _stub_route_two_stage
    try:
        from semantic_router import route_two_stage  # the real prod function
    except ImportError as e:
        raise SystemExit(
            "ABORT: semantic_router.route_two_stage is not available on this "
            "checkout — it lands with the two-stage-router-active PR (lane 1)."
            f" Import error: {e}") from e
    return route_two_stage


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_sidecar_identity(port: int, gguf: str) -> None:
    """A pre-existing server on :port must actually serve the r2 GGUF."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/props",
                                    timeout=5) as r:
            served = json.load(r).get("model_path", "")
    except OSError as e:
        raise SystemExit(
            f"ABORT: no reachable sidecar on :{port} ({e}); "
            "pass --launch-sidecar to start one") from e
    # the resident systemd sidecar serves the r2 GGUF from its own copy under
    # /home/zoe/models/functiongemma-router/ — accept the same inode or a
    # byte-identical copy (sha256), never a mere basename match
    sp, gp = Path(served), Path(gguf)
    try:
        same = sp.exists() and gp.exists() and (
            sp.samefile(gp) or _sha256(sp) == _sha256(gp))
    except OSError as e:
        raise SystemExit(
            f"ABORT: cannot verify sidecar model identity ({e})") from e
    if not same:
        raise SystemExit(
            f"ABORT: sidecar on :{port} serves {served!r}, which is not "
            f"byte-identical to {gguf!r} — refusing numbers from the wrong "
            "model")


def run(route_fn, cases: list[dict]) -> tuple[list[dict], list[float]]:
    results, lat = [], []
    route_fn("warmup: what time is it")  # embedder + sidecar warm, excluded
    for case in cases:
        t0 = time.perf_counter()
        d = route_fn(case["text"])
        wall_ms = (time.perf_counter() - t0) * 1000
        lat.append(wall_ms)
        fallback = d.source != "two_stage"
        name = None if fallback else d.tool
        results.append({
            "id": case["id"], "style": case["style"],
            "expected": case["expected"], "predicted": name,
            "source": d.source, "confidence": round(d.confidence, 3),
            "args": d.args, "ok": score(case, name),
            "latency_ms": round(wall_ms, 1),
            "router_latency_ms": round(d.latency_ms, 1),
        })
    return results, lat


def evaluate(results: list[dict], lat: list[float]) -> dict:
    summary = summarize(results, lat)
    by_source: dict[str, int] = {}
    for r in results:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    n_fb = sum(v for k, v in by_source.items() if k != "two_stage")
    summary["fallback_by_source"] = by_source
    summary["brain_fallback_pct"] = round(100 * n_fb / len(results), 1)
    router_lat = [r["router_latency_ms"] for r in results]
    summary["router_reported_ms_p50"] = round(statistics.median(router_lat), 1)
    summary["gates"] = {
        "overall_ge_90": (summary["accuracy_overall_pct"] or 0) >= OVERALL_MIN_PCT,
        "chat_fp_zero": (summary["chat_false_positive_pct"] or 0) <= CHAT_FP_MAX_PCT,
        "p50_lt_600ms": summary["latency_ms_p50"] < P50_MAX_MS,
    }
    summary["gates_pass"] = all(summary["gates"].values())
    return summary


def print_table(results: list[dict], summary: dict) -> None:
    print(f"{'id':<28} {'style':<11} {'source':<15} {'predicted':<22} ok  ms")
    for r in results:
        print(f"{r['id']:<28} {r['style']:<11} {r['source']:<15} "
              f"{str(r['predicted']):<22} {'OK ' if r['ok'] else 'FAIL'} "
              f"{r['latency_ms']:>7.1f}")
    print("-" * 92)
    print(f"overall {summary['accuracy_overall_pct']}%  "
          f"chat-FP {summary['chat_false_positive_pct']}%  "
          f"p50 {summary['latency_ms_p50']}ms  "
          f"p90 {summary['latency_ms_p90']}ms  "
          f"brain-fallback {summary['brain_fallback_pct']}% "
          f"{summary['fallback_by_source']}")
    print("gates:", summary["gates"],
          "=> PASS" if summary["gates_pass"] else "=> FAIL")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true",
                    help="contract stub instead of the real router (smoke)")
    ap.add_argument("--launch-sidecar", action="store_true",
                    help=f"start the r2 sidecar on :{SIDECAR_PORT} ad hoc")
    ap.add_argument("--out", default=str(HERE / "results" / "prod-path.json"))
    ap.add_argument("--no-assert", action="store_true",
                    help="report only; exit 0 even if gates fail")
    args = ap.parse_args()

    avail = mem_available_mb()
    if not args.stub and avail < MIN_AVAILABLE_MB:
        print(f"ABORT: MemAvailable={avail} MB < {MIN_AVAILABLE_MB} MB",
              file=sys.stderr)
        return 2

    # import before spawning anything so an import failure leaks no server
    route_fn = load_router(stub=args.stub)
    cases = [json.loads(l) for l in CORPUS.open()]

    proc = None
    try:
        if args.launch_sidecar:
            proc = start_server(GGUF_R2, SIDECAR_PORT)
        elif not args.stub:
            check_sidecar_identity(SIDECAR_PORT, GGUF_R2)
        results, lat = run(route_fn, cases)
    finally:
        if proc is not None:
            proc.kill()
            proc.wait()

    summary = evaluate(results, lat)
    out = {"harness": "prod_path_eval", "stub": args.stub,
           "sidecar_port": SIDECAR_PORT,
           "mem_available_mb_before": avail,
           "summary": summary, "cases": results}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1))
    print_table(results, summary)
    print("wrote", out_path)
    if args.stub:
        print("NOTE: stub run — structural only, numbers are meaningless")
        return 0
    return 0 if (summary["gates_pass"] or args.no_assert) else 1


if __name__ == "__main__":
    sys.exit(main())
