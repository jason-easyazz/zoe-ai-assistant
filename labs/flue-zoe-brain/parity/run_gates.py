#!/usr/bin/env python3
"""Unified runner for the Flue-brain quality gates (LAB-ONLY).

Provisions ONE fresh empty-store test user, mints its session, runs the
selected gates against the live brain, and writes a single aggregated report
(JSON + markdown). Bakes in the operational guards the ad-hoc runs learned the
hard way, so a gate result is trustworthy by construction:

  * memory guard — refuse to run when the box is too tight (skippable);
  * quiet-window guard — refuse if main moved in the last few minutes
    (deploy.yml restarts zoe-data on every push and would corrupt a run);
  * preflight — one real chat turn must 200 before the suite starts;
  * integrity stamp — records zoe-data's start time before and after; a
    mid-run restart invalidates the numbers and is reported.

Gates are discovered dynamically: any ``*_gate.py`` in this directory exposing a
module-level ``GATE = GateSpec(...)`` is runnable by name. So new gates
(reliability, tool-breadth, security, …) light up here automatically once added.

Usage:
  python3 run_gates.py                 # all gates, fresh user, full guards
  python3 run_gates.py --gates hard,corpus
  python3 run_gates.py --list
  python3 run_gates.py --user test-foo # reuse a specific user (skips provision)
  python3 run_gates.py --skip-guards   # ignore memory/quiet-window (dev only)

Exit code is non-zero if any row is FAIL or ERROR (JUDGE rows never fail the
run — they need a human/model read, surfaced in the report).

LAB ONLY — never imported by services/zoe-data.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import gatelib

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "gate-results"
MIN_MEM_MB = 1700  # brain+STT+TTS headroom; below this a run risks OOM
QUIET_WINDOW_S = 240  # main SHA must be stable this long before measuring


def discover_gates() -> dict[str, gatelib.GateSpec]:
    gates: dict[str, gatelib.GateSpec] = {}
    for path in sorted(HERE.glob("*_gate.py")):
        if path.name == "run_gates.py":
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        # Catch SystemExit too: standalone gates that sys.exit() at import (e.g.
        # a missing session file) must be skipped, not crash discovery. Only a
        # conformant gate (module-level GATE, no import-time side effects) is run
        # here; a standalone script is left to its own `__main__`.
        except (Exception, SystemExit) as e:  # noqa: BLE001
            print(f"! skipping {path.name}: not runner-conformant ({e})")
            continue
        gate = getattr(module, "GATE", None)
        if isinstance(gate, gatelib.GateSpec):
            gates[gate.name] = gate
    return gates


def guards(skip: bool) -> None:
    if not gatelib.health_ok():
        sys.exit("GUARD: zoe-data /health is not 200 — brain not serving.")
    if skip:
        print("GUARD: --skip-guards set, ignoring memory + quiet-window checks")
        return
    mem = gatelib.available_mb()
    if mem < MIN_MEM_MB:
        sys.exit(f"GUARD: available memory {mem}MB < {MIN_MEM_MB}MB — defer to avoid OOM.")
    sha = gatelib.main_sha()
    print(f"GUARD: waiting {QUIET_WINDOW_S}s to confirm main ({sha}) is quiet…")
    time.sleep(QUIET_WINDOW_S)
    if gatelib.main_sha() != sha:
        sys.exit("GUARD: main moved during the quiet window — a deploy is in flight, retry later.")


def write_report(rows: list[dict], meta: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=1))

    by_gate: dict[str, Counter] = {}
    for r in rows:
        by_gate.setdefault(r["gate"], Counter())[r["verdict"]] += 1
    lines = [f"# Gate run {stamp}", ""]
    lines.append(f"- user: `{meta['user']}` · nonce: `{meta['nonce']}`")
    lines.append(f"- integrity: zoe-data start {meta['svc_before']} → {meta['svc_after']} "
                 f"({'STABLE' if meta['svc_before'] == meta['svc_after'] else 'RESTARTED MID-RUN — INVALID'})")
    lines.append(f"- main: `{meta['main_sha']}`")
    lines.append("")
    lines.append("| gate | PASS | FAIL | ERROR | JUDGE |")
    lines.append("|---|---|---|---|---|")
    for g, c in by_gate.items():
        lines.append(f"| {g} | {c['PASS']} | {c['FAIL']} | {c['ERROR']} | {c['JUDGE']} |")
    fails = [r for r in rows if r["verdict"] in ("FAIL", "ERROR")]
    if fails:
        lines += ["", "## Failures / errors", ""]
        for r in fails:
            lines.append(f"- **{r['gate']}/{r['category']}** — {r['query'][:60]!r}: {r['why']}")
            lines.append(f"  - reply: {r['reply'][:140]!r}")
    judges = [r for r in rows if r["verdict"] == "JUDGE"]
    if judges:
        lines += ["", f"## Needs judgment ({len(judges)})", ""]
        for r in judges:
            lines.append(f"- {r['gate']}/{r['category']} {r['query'][:50]!r} → {r['reply'][:110]!r}")
    md = RESULTS_DIR / f"{stamp}.md"
    md.write_text("\n".join(lines) + "\n")
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gates", default="all", help="comma list of gate names, or 'all'")
    ap.add_argument("--user", default="", help="reuse this test user (must exist); default provisions fresh")
    ap.add_argument("--skip-guards", action="store_true")
    ap.add_argument("--list", action="store_true", help="list discovered gates and exit")
    args = ap.parse_args()

    available = discover_gates()
    if args.list:
        for name, g in available.items():
            print(f"{name:14s} {g.description}")
        return
    if not available:
        sys.exit("no gates discovered (*_gate.py with a module-level GATE)")

    wanted = list(available) if args.gates == "all" else [g.strip() for g in args.gates.split(",")]
    unknown = [g for g in wanted if g not in available]
    if unknown:
        sys.exit(f"unknown gate(s): {unknown}. Available: {list(available)}")

    guards(args.skip_guards)

    nonce = str(int(time.time()))
    user = args.user or f"test-gate-{nonce[-6:]}"
    if not args.user:
        print(f"provisioning fresh user {user}…")
    password = gatelib.provision_user(user)
    sid = gatelib.login(user, password)

    # A REAL second user for two-user isolation checks (hard_gate section G,
    # security_gate). Reached via the internal override, but provisioned so it's
    # a faithful account, not a synthetic id — and empty-store like the primary.
    user_b = f"test-gate-iso-{nonce[-6:]}"
    gatelib.provision_user(user_b)
    print(f"provisioned isolation user {user_b}")

    # Preflight: a real turn on a nonce'd session must succeed before we measure.
    ctx = gatelib.GateContext(sid=sid, user_id=user, nonce=nonce,
                              recorder=gatelib.Recorder(), token=gatelib.internal_token(),
                              user_b=user_b)
    reply, _ = ctx.chat("preflight — say ready", ctx.session("preflight"))
    if reply.startswith("(ERROR") or any(m in reply.lower() for m in gatelib.BRAIN_FALLBACK_MARKERS):
        sys.exit(f"PREFLIGHT failed: {reply[:120]}")
    print(f"preflight ok: {reply[:60]!r}")

    svc_before = gatelib.service_started_at()
    all_rows: list[dict] = []
    try:
        for name in wanted:
            gate = available[name]
            print(f"\n=== GATE {name}: {gate.description}")
            ctx.recorder = gatelib.Recorder(gate=name)
            try:
                gate.run(ctx)
            except Exception as e:  # noqa: BLE001 — one gate crashing must not lose the others
                ctx.recorder.add(name, "(gate crashed)", str(e), 0, "ERROR", "gate raised")
            all_rows.extend(ctx.recorder.rows)
    finally:
        # ALWAYS hard-purge this run's writes — even on crash/Ctrl-C. Gate writes
        # ("add X to my shopping list") land on the FAMILY-shared surface, so
        # leaving them pollutes the real household's list. Runs regardless of
        # exit path so a killed run can't leave cruft on live data.
        try:
            purged = gatelib.purge_artifacts(nonce)
            print(f"cleanup: purged gate writes { {k: v for k, v in purged.items() if not v.endswith(' 0')}}")
        except Exception as e:  # noqa: BLE001 — cleanup failure must not mask the run
            print(f"!! cleanup purge failed ({e}) — check for leftover gate writes on real lists")
    svc_after = gatelib.service_started_at()

    meta = {"user": user, "nonce": nonce, "gates": wanted,
            "svc_before": svc_before, "svc_after": svc_after, "main_sha": gatelib.main_sha()}
    report = write_report(all_rows, meta)

    counts = Counter(r["verdict"] for r in all_rows)
    total = len(all_rows)
    auto = counts["PASS"] + counts["FAIL"] + counts["ERROR"]
    print(f"\n{'=' * 60}")
    print(f"TOTAL {total} rows: {dict(counts)}")
    if auto:
        print(f"auto-verdict pass rate: {counts['PASS']}/{auto} = {100 * counts['PASS'] // auto}%")
    if svc_before != svc_after:
        print("!! zoe-data RESTARTED mid-run — results INVALID, re-run in a quiet window")
    print(f"report: {report}")
    sys.exit(1 if (counts["FAIL"] or counts["ERROR"] or svc_before != svc_after) else 0)


if __name__ == "__main__":
    main()
