"""Orchestration and the CLI: select cells, PROVE the instrument, run, score, write the artifact.

The order of a run is the standing rule "break the fix and the test must go red" made mechanical:

1. **Controls first.** The selected cells that name a control are run on the lab arm with that feature
   switched OFF. Every one of them must go red. A control cell that stays green means the cell does not
   measure the feature - the instrument is not instrumented - and the run is REFUSED (status ``error``,
   exit 2, no axis table). A cell that errors or skips under its control is also not proof.
2. **Then the measurement** on the chosen arm (``--arm``), same spec, same seed.
3. **An artifact on every exit path**, a trend line, and (alone, deliberately) a baseline.

``--control off`` (or ``--arm Z0-off``) runs ONLY step 1 and reports it: the negative-control arm.
Nothing here touches the live service, the live palace, Postgres or the brain: the lab driver is in-process.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import HARNESS_VERSION, artifact, cells as cellmod
from .arms import ARMS, make_arm
from .lab_driver import CONTROLS, REPO
from .scorers import SCORER_VERSION
from .spec import AXES, Cell, SpecError, load_cells, select
from .world import BASELINE_SEED, fresh_seed, make_world

CACHE = Path.home() / ".cache" / "zoe"
DEFAULT_RESULTS = CACHE / "zmb_last.json"
DEFAULT_TREND = CACHE / "zmb_trend.jsonl"
DEFAULT_BASELINE = CACHE / "zmb_baseline.json"


def _log(msg: str) -> None:
    print(msg, flush=True)


def revision() -> "dict[str, Any] | None":
    """The commit (and dirtiness) of the checkout the bench ran from. Read-only; None when unknown."""
    try:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
                                    capture_output=True, text=True, timeout=10, check=True).stdout.strip())
        return {"commit": head, "dirty": dirty}
    except Exception:  # noqa: BLE001 - provenance is best-effort, never a reason to fail the run
        return None


def _guard_trips() -> int:
    """``live_store_guard``'s process-wide trip counter (0 when the service was never loaded). The run
    compares the counter before and after, so a trip an unrelated test caused earlier is not its own."""
    try:
        import live_store_guard
        return int(live_store_guard.trip_count())
    except Exception:  # noqa: BLE001 - only importable once the lab (or the test session) loaded the service
        return 0


def _row(cell: Cell, o: "cellmod.Outcome") -> "dict[str, Any]":
    return {"id": cell.id, "axis": cell.axis, "tier": cell.tier, "title": cell.title, "verdict": o.verdict,
            "stage": o.stage, "expected": cell.expected, "controls": list(cell.controls),
            "sanity": cell.sanity, "duration_s": o.duration_s, "brain_turns": o.brain_turns,
            "evidence": o.evidence, "reason": o.reason, "lme_map": cell.lme_map}


def run_cells(cells: "list[Cell]", world, arm, log=_log) -> "list[dict]":
    """Run every cell on ``arm`` (a brain-tier cell is a declared SKIP with its reason)."""
    rows = []
    for cell in cells:
        rendered = cell.rendered(world)
        if cell.kind not in ("", "script"):
            o = cellmod.Outcome("ERROR", reason=f"unknown cell kind {cell.kind!r}")
        else:
            o = cellmod.run_cell(rendered, world, arm)
        rows.append(_row(cell, o))
    return rows


def control_pass(cells: "list[Cell]", world, off: "frozenset[str]", log=_log) -> "dict[str, Any]":
    """Run the controlled cells on the lab arm with ``off`` switched off; every one must go RED.

    Returns ``{"off", "checked", "red", "green": [...], "not_run": [...], "ok", "rows"}``. ``green`` =
    controlled cells that PASSED with their feature off (the instrument does not measure the feature);
    ``not_run`` = ones that errored or skipped (no proof either way)."""
    from .arms.z0 import Z0Arm
    arm = Z0Arm(off=off)
    # a cell the arm cannot run here (the disk cells need chromadb, absent from the slim CI lane) is a declared
    # SKIP in the measurement, not "no proof either way" for the whole instrument
    todo = [c for c in cells if c.controls and set(c.controls) <= off and c.expected == "PASS"
            and c.tier == "store" and cellmod.required_capabilities(c) <= set(arm.capabilities)]
    # the night mind's cells (an own-model reflection: K1's citation control, K2 / K3 and K6-K12) are proven on Z0n, Z0 + the night pass with the lab's
    # fake brain: on plain Z0 the nightly model is scripted and they SKIP. Every other cell is proven on plain Z0, as before.
    night_todo = [c for c in todo if cellmod.uses_night(c)]
    todo = [c for c in todo if not cellmod.uses_night(c)]
    try:
        rows = run_cells(todo, world, arm, log) if todo else []
    finally:
        arm.close()
    if night_todo:
        narm = Z0Arm(off=off, night=True, name="Z0n-off")
        try:
            rows += run_cells(night_todo, world, narm, log)
        finally:
            narm.close()
    todo = todo + night_todo
    green = sorted(r["id"] for r in rows if r["verdict"] == "PASS")
    not_run = sorted(r["id"] for r in rows if r["verdict"] in ("SKIP", "ERROR"))
    red = sum(1 for r in rows if r["verdict"] == "FAIL")
    return {"off": sorted(off), "checked": len(todo), "red": red, "green": green, "not_run": not_run,
            "ok": not green and not not_run, "rows": rows}


def instrument_block(cp: "dict[str, Any]", cells: "list[Cell]") -> "dict[str, Any]":
    return {"controls_off": cp["off"], "checked": cp["checked"], "red": cp["red"],
            "lab_controls_red": f"{cp['red']}/{cp['checked']}", "green": cp["green"][:50],
            "not_run": cp["not_run"][:50], "ok": cp["ok"],
            "uncontrolled": sorted(c.id for c in cells if c.tier == "store" and not c.controls
                                   and not c.sanity and c.expected == "PASS")}


def plan_text(cells: "list[Cell]", tier: str, seed: str) -> str:
    lines = [f"zoe_memory_bench v{HARNESS_VERSION} - plan (no I/O, no model, no live service)",
             f"  tier={tier}  seed={seed}  cells={len(cells)}  spec={artifact.spec_digest(cells)[:12]}",
             "  controls (--control off switches ALL of these off in-process; every controlled cell must go red):"]
    lines += [f"    {k}: {v}" for k, v in CONTROLS.items()]
    for letter, axis in AXES.items():
        cs = [c for c in cells if c.axis == axis]
        if not cs:
            lines.append(f"  ({letter}) {axis}: no cells yet")
            continue
        st = [c for c in cs if c.tier == "store"]
        lines.append(
            f"  ({letter}) {axis}: {len(st)} store-tier ({sum(1 for c in st if c.controls)} controlled, "
            f"{sum(1 for c in st if c.sanity)} sanity, {sum(1 for c in st if c.is_target)} known-failing target(s)), "
            f"{len(cs) - len(st)} brain-tier declared-skipped")
    lines += ["  order: control pass on the lab arm (refuse if any control stays green) -> measurement on --arm",
              "  budget: lab = in-process, 0 brain turns; per-cell duration_s and brain_turns are in the artifact",
              "  teardown: nothing persisted (in-memory store); live_store_guard trips must be 0"]
    return "\n".join(lines)


def list_text(cells: "list[Cell]") -> str:
    out = []
    for c in cells:
        tag = "\tsanity" if c.sanity else ""
        out.append(f"{c.id}\t{c.axis}\t{c.tier}\t{c.expected}\t{','.join(c.controls) or '-'}{tag}\t{c.title}")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Zoe Memory Bench (ZMB): spec, scorers, lab driver with negative "
                                 "controls, arm adapters. docs/knowledge/zoe-memory-bench.md",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", choices=("store", "full"), default="store",
                    help="store = brain-free cells (the lab); full = also the brain-tier cells, which the lab "
                         "cannot run: they are declared SKIPs and the run is `partial`")
    ap.add_argument("--arm", default="Z0", help=f"the memory system to measure: {', '.join(ARMS)}")
    ap.add_argument("--model-url", default=None, metavar="http://127.0.0.1:11500/v1",
                    help="Z0n only: run the night mind against this llama-server (the clone brain, or the 12B in its night window) instead of the lab's fake brain. "
                         "Nothing is planted: the K cells then score what the real model does. Loopback only")
    ap.add_argument("--ctx-tokens", type=int, default=8192, help="Z0n with --model-url: the server's context size (the pass's chunk budget derives from it)")
    ap.add_argument("--model-name", default="", help="Z0n with --model-url: the model name sent in the request")
    ap.add_argument("--seed", default="baseline",
                    help="baseline (the fixed corpus seed), fresh (a held-out world of the same shapes; never a "
                         "baseline), or any string")
    ap.add_argument("--only", default=None, metavar="A1.digest.home,A2.*",
                    help="run only these cell ids (a trailing * is a prefix); a PARTIAL run, never a baseline")
    ap.add_argument("--axis", default=None, metavar="a,authority", help="run only these axes (letter or name)")
    ap.add_argument("--control", default=None, metavar="off|authority,identity,...",
                    help="run ONLY the negative-control pass with these features OFF (off = all); exit 2 if any "
                         "controlled cell stays green")
    ap.add_argument("--compare-baseline", action="store_true",
                    help="exit 1 on a regression; REFUSES (exit 2) with no valid baseline")
    ap.add_argument("--record-baseline", action="store_true",
                    help="save this run as the baseline (alone; full, baseline-seed, Z0, controls proven)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; nothing runs")
    ap.add_argument("--list", action="store_true", help="print the cell ids and exit")
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--trend", type=Path, default=DEFAULT_TREND)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    return ap


def _refused(args, reason: str, *, rev, extra: "dict | None" = None) -> int:
    print(f"REFUSED: {reason}", file=sys.stderr)
    payload = {"harness_version": HARNESS_VERSION, "scorer_version": SCORER_VERSION, "status": "error",
               "refusal": reason, "finished_at": artifact.now_iso(), "revision": rev, "arm": args.arm,
               "tier": args.tier, "axes": None, "cells": [], **(extra or {})}
    artifact.write_json(args.results, payload)
    artifact.append_trend(args.trend, payload)
    return 2


def _night_arm(args) -> Any:
    """Z0n pointed at a real server. Loopback only (the bench never talks to the network)."""
    from .arms.mpa_model import loopback
    from .arms.z0 import Z0Arm
    url = loopback(args.model_url)
    return Z0Arm(name="Z0n", night=True, night_url=url[:-3] if url.endswith("/v1") else url, night_model=args.model_name, night_ctx=args.ctx_tokens)


def main(argv: "list[str] | None" = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.arm not in ARMS:
        ap.error(f"unknown arm {args.arm!r} (known: {', '.join(ARMS)})")
    if args.compare_baseline and args.record_baseline:
        ap.error("--compare-baseline and --record-baseline are mutually exclusive: a regressed compare run "
                 "must never overwrite the bar (record alone, deliberately)")
    if args.model_url and args.arm != "Z0n":
        ap.error("--model-url applies to --arm Z0n only")
    if args.arm == "Z0-off":  # the negative-control arm IS the control pass
        args.control = args.control or "off"
    off: "frozenset[str] | None" = None
    if args.control is not None:
        off = frozenset(CONTROLS) if args.control.strip().lower() == "off" else \
            frozenset(t.strip() for t in args.control.split(",") if t.strip())
        unknown = sorted(off - set(CONTROLS))
        if unknown or not off:
            ap.error(f"unknown control(s) {', '.join(unknown) or '(none)'} (known: off, {', '.join(CONTROLS)})")
        if args.record_baseline or args.compare_baseline:
            ap.error("--control reports the instrument, not the system: it cannot record or compare a baseline")
    try:
        everything = load_cells()
        chosen = select(everything, args.only, args.axis)
    except SpecError as exc:
        ap.error(str(exc))
    partial_sel = args.only is not None or args.axis is not None
    if partial_sel and args.record_baseline:
        ap.error("--record-baseline is refused with --only/--axis: a partial run would turn every unselected "
                 "cell into 'new' - record the baseline from a full run")
    if args.seed == "fresh":
        seed = fresh_seed()
    elif args.seed == "baseline":
        seed = BASELINE_SEED
    else:
        seed = args.seed
    held_out = seed != BASELINE_SEED
    if held_out and args.record_baseline:
        ap.error("--record-baseline needs the baseline seed: a held-out world sits BESIDE the baseline, "
                 "never inside it")
    if args.arm != "Z0" and args.record_baseline:
        ap.error("--record-baseline records the Z0 bar; an arm under evaluation never becomes the baseline")

    if args.list:
        print(list_text(chosen))
        return 0
    if args.dry_run:
        print(plan_text(chosen, args.tier, seed))
        return 0

    rev = revision()
    world = make_world(seed)
    digest = artifact.spec_digest(everything)
    cells_by_id = {c.id: c for c in everything}
    started, t0 = artifact.now_iso(), time.monotonic()
    baseline, baseline_problem = artifact.load_baseline(args.baseline)
    if args.compare_baseline and baseline is None:
        return _refused(args, f"--compare-baseline needs a valid baseline: {baseline_problem}", rev=rev)

    base_payload = {"harness_version": HARNESS_VERSION, "scorer_version": SCORER_VERSION, "spec_digest": digest,
                    "started_at": started, "tier": args.tier, "arm": args.arm, "seed": args.seed,
                    "corpus_seed": seed, "held_out": held_out, "revision": rev,
                    "selection": {"only": args.only, "axis": args.axis}}
    run_error: "str | None" = None
    rows: "list[dict]" = []
    instrument: "dict[str, Any]" = {}
    refused: "str | None" = None
    trips_before = _guard_trips()

    try:
        # 1. the instrument ------------------------------------------------------------------------------
        cp = control_pass(chosen, world, off if off is not None else frozenset(CONTROLS))
        instrument = instrument_block(cp, chosen)
        if off is not None:
            rows = cp["rows"]  # --control: THIS is the report
        elif not cp["ok"]:
            refused = (f"instrument not instrumented: {len(cp['green'])} control cell(s) stayed green with their "
                       f"feature off, {len(cp['not_run'])} did not run (first: "
                       f"{', '.join((cp['green'] + cp['not_run'])[:5])}). Nothing was measured.")
        else:
            # 2. the measurement ---------------------------------------------------------------------------
            arm = make_arm(args.arm) if not (args.arm == "Z0n" and args.model_url) else _night_arm(args)
            try:
                rows = run_cells(chosen, world, arm)
            finally:
                arm.close()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - a guard trip (BaseException) must still leave an artifact
        run_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        _log(f"run aborted: {run_error}")

    guard_trips = max(0, _guard_trips() - trips_before)
    if refused:
        return _refused(args, refused, rev=rev, extra={"instrument": instrument, "spec_digest": digest,
                                                       "corpus_seed": seed, "seed": args.seed,
                                                       "selection": base_payload["selection"]})

    full_skipped = args.tier == "full" and any(r["tier"] == "full" and r["verdict"] == "SKIP" for r in rows)
    partial = partial_sel or full_skipped
    if off is not None:  # a control run reports the instrument: exit by it, never as a measurement
        status = "error" if (run_error or not instrument.get("ok", False)) else "ok"
        cmp = None
        axes = None
        hard: "list[str]" = []
    else:
        hard = artifact.hard_violations(rows, cells_by_id)
        cmp = artifact.compare_baseline(artifact.verdict_map(rows), baseline, corpus_seed=seed, digest=digest) \
            if args.compare_baseline else None
        status = artifact.decide_status(run_error=run_error, refused=None, partial=partial, results=rows,
                                        hard=hard, cmp=cmp, compare_requested=args.compare_baseline)
        axes = artifact.axis_stats(rows, cells_by_id, instrument.get("ok", False))
    payload = {**base_payload, "status": status, "run_kind": "control" if off is not None else "measure",
               "run_error": run_error, "finished_at": artifact.now_iso(),
               "duration_s": round(time.monotonic() - t0, 2), "partial": partial,
               "instrument": instrument, "axes": axes, "cells": rows, "hard_violations": hard, "compare": cmp,
               "baseline_ref": {"path": str(args.baseline), "created_at": (baseline or {}).get("created_at")},
               "teardown": {"proven": guard_trips == 0 and run_error is None, "kind": "lab: in-memory store, "
                            "nothing persisted", "live_store_guard_trips": guard_trips}}
    if guard_trips:
        payload["status"] = status = "error"
    artifact.write_json(args.results, payload)
    artifact.append_trend(args.trend, payload)

    if args.record_baseline and status == "ok" and not partial and instrument.get("ok"):
        artifact.write_json(args.baseline, artifact.make_baseline(rows, corpus_seed=seed, digest=digest,
                                                                  arm=args.arm, revision=rev))
        _log(f"baseline recorded: {args.baseline}")
    elif args.record_baseline:
        _log(f"baseline NOT recorded: status={status} partial={partial}")

    for r in rows:
        if r["verdict"] != "PASS" or off is not None:
            exp = f" (expected {r['expected']}: a target)" if r["expected"] == "FAIL" else ""
            _log(f"  {r['verdict']:<5} {r['id']}{exp}" + (f"  [{r['reason'][:80]}]" if r["reason"] else ""))
    if axes:
        for a, s in axes.items():
            if s["n"]:
                _log(f"  {a:<11} {s['pass']}/{s['n']} (Wilson95 {s['wilson95'][0]:.2f}-{s['wilson95'][1]:.2f})"
                     f"{' CLAIMABLE' if s['claimable'] else ''}"
                     f"{' HARD:' + ','.join(s['hard_violations'][:3]) if s['hard_violations'] else ''}")
    _log(f"  instrument: controls red {instrument.get('lab_controls_red')} ok={instrument.get('ok')}")
    if hard:
        _log(f"HARD INVARIANT VIOLATIONS: {', '.join(hard[:10])}")
    if cmp and cmp["regressions"]:
        _log(f"REGRESSIONS vs baseline: {', '.join(cmp['regressions'])}")
    _log(f"status={status}  results={args.results}")
    if status == "error":
        return 2
    return 1 if status == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
