"""The bake-off decision rule (G0-G3 + the winner clause), as pure functions over what the window measured.

Pre-registered in docs/research/memory-system-decision-2026-10-05.md section 6.1 and copied into docs/knowledge/zoe-memory-bench.md;
**no threshold here may change after a run has been seen** (a test pins them). Nothing in this module runs a model, opens a socket
or reads the live system: ``bakeoff.py`` measures, this decides, so the verdict logic is red-before-green in CI.

Every gate item is one of ``PASS`` / ``FAIL`` / ``NA`` (not measured). ``NA`` is never a pass: a gate with an ``NA`` item and no
``FAIL`` is ``INCOMPLETE``, and an INCOMPLETE arm is not adoptable (the same "a skip is never a pass" rule as the artifact).

Every axis the rule names exists in the spec: G2's hard cells include A3 / A8 (the ``authority`` axis) and poisoning (``poisoning``),
and the winner clause's C and D are the ``temporal`` and ``recall`` axes. The verdict is still advisory (the owner decides), and the
design's remaining unbuilt cells are listed in docs/knowledge/zoe-memory-bench.md.
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

from .scorers import wilson

PASS, FAIL, NA = "PASS", "FAIL", "NA"

RULE = {
    "steady_rss_mb": 600.0, "burst_rss_mb": 900.0, "mem_available_floor_mb": 1200.0,
    "json_valid_min": 0.95, "json_calls_min": 100, "slot_tokens": 8192,
    "recall_p95_ms": 600.0, "slot_s_per_turn": 2.6,          # 2 x 1.3 s: the top of the record's "0.6-1.3 s per serial pass"
    "layer_lines_max": 1000, "delete_lines_min": 5000, "memory_lines_total": 17923,
    "seeds_required": 3, "restore_forget_min": 6,
}
#: rule letter -> the ZMB axis that implements it (None = not built; every letter is built now)
WIN_AXES = {"B": "extraction", "C": "temporal", "D": "recall", "E": "abstention"}
#: the files the decision record (section 3.1) estimates are deletable under adoption, for the G3 line count (an ESTIMATE)
DELETABLE_FILES = (
    "memory_digest.py", "memory_idle_consolidation.py", "memory_quality.py", "memory_lint.py", "memory_reject_ledger.py",
    "memory_index_health.py", "memory_recall_probe.py", "zoe_memory_layers.py", "zoe_memory_compose.py", "hindsight_memory.py")
HARD_AXES = ("authority", "forgetting", "abstention", "emotional", "identity", "poisoning")


def item(state: str, threshold: str, measured: str) -> "dict[str, str]":
    return {"state": state, "threshold": threshold, "measured": measured}


def _le(v: "Optional[float]", limit: float, unit: str = "") -> "dict[str, str]":
    if v is None:
        return item(NA, f"<= {limit:g}{unit}", "not measured")
    return item(PASS if v <= limit else FAIL, f"<= {limit:g}{unit}", f"{v:g}{unit}")


def _ge(v: "Optional[float]", limit: float, unit: str = "") -> "dict[str, str]":
    if v is None:
        return item(NA, f">= {limit:g}{unit}", "not measured")
    return item(PASS if v >= limit else FAIL, f">= {limit:g}{unit}", f"{v:g}{unit}")


def gate_state(items: "dict[str, dict]") -> str:
    states = [i["state"] for i in items.values()]
    return FAIL if FAIL in states else (NA if NA in states else PASS)


# ── aggregation over seeds ───────────────────────────────────────────────────

def aggregate_axes(seed_runs: "dict[str, dict]") -> "dict[str, dict]":
    """Per axis, summed over seeds: ``{pass, n, skipped, wilson95, rate}``. ``n`` counts cells that RAN (PASS / FAIL / ERROR, not
    sanity); a cell skipped by the time budget or a stub is ``skipped``, never a pass."""
    out: "dict[str, dict]" = {}
    for run in seed_runs.values():
        for axis, s in (run.get("axes") or {}).items():
            a = out.setdefault(axis, {"pass": 0, "n": 0, "skipped": 0, "cells": 0})
            a["pass"] += int(s.get("pass") or 0)
            a["n"] += int(s.get("n") or 0)
            a["skipped"] += int(s.get("skip") or 0)
            a["cells"] += int(s.get("cells") or 0)
    for a in out.values():
        lo, hi = wilson(a["pass"], a["n"])
        a["wilson95"] = [round(lo, 4), round(hi, 4)]
        a["rate"] = round(a["pass"] / a["n"], 4) if a["n"] else None
    return out


def hard_violations(seed_runs: "dict[str, dict]") -> "dict[str, list[str]]":
    """seed -> ids of hard-invariant cells that failed (FAIL or ERROR)."""
    return {seed: sorted(run.get("hard_violations") or []) for seed, run in seed_runs.items()}


def hard_not_run(seed_runs: "dict[str, dict]") -> "dict[str, int]":
    """seed -> hard-invariant cells that were SKIPPED (time budget, stub): not a clean bill either."""
    return {seed: int(run.get("hard_skipped") or 0) for seed, run in seed_runs.items()}


# ── the four gates ───────────────────────────────────────────────────────────

def gate_g0(m: dict) -> "dict[str, dict]":
    rss, js = m.get("rss") or {}, m.get("json") or {}
    calls, valid = js.get("calls"), js.get("valid")
    rate = (valid / calls) if calls else None
    json_item = item(NA, f">= {RULE['json_valid_min']:.0%} over >= {RULE['json_calls_min']} calls", "not measured")
    if calls is not None and calls < RULE["json_calls_min"]:
        json_item = item(NA, f">= {RULE['json_valid_min']:.0%} over >= {RULE['json_calls_min']} calls", f"only {calls} calls ran: below the minimum, not a measurement")
    elif calls is not None:
        ok = rate is not None and rate >= RULE["json_valid_min"]
        json_item = item(PASS if ok else FAIL, f">= {RULE['json_valid_min']:.0%} over >= {RULE['json_calls_min']} calls",
                         f"{valid}/{calls} = {rate:.1%} ({js.get('source', '')})" if rate is not None else f"0/{calls}")
    fits = m.get("prompt_fits")
    return {
        "zero_nonloopback_connects": item(NA if m.get("nonloopback_connects") is None else
                                          (PASS if m["nonloopback_connects"] == 0 else FAIL), "0",
                                          "not measured" if m.get("nonloopback_connects") is None else str(m["nonloopback_connects"])),
        "installs_aarch64_py312": item(PASS if m.get("installs_ok", True) else FAIL, "installs",
                                       "measured 2026-10-05 (G0-install-report.md): pip install exit 0, wheels only"),
        "steady_rss": _le(rss.get("steady_mb"), RULE["steady_rss_mb"], " MB"),
        "burst_rss": _le(rss.get("burst_mb"), RULE["burst_rss_mb"], " MB"),
        "mem_available_floor": _ge(m.get("mem_available_floor_mb"), RULE["mem_available_floor_mb"], " MB"),
        "extraction_json_validity": json_item,
        "prompt_fits_slot": item(NA if fits is None else (PASS if fits else FAIL),
                                 f"prompt + chunk + output reserve < {RULE['slot_tokens']} tokens",
                                 "not measured" if fits is None else str(m.get("prompt_detail", fits))),
    }


def gate_g1(m: dict) -> "dict[str, dict]":
    rc = m.get("recall") or {}
    return {
        "recall_p95_warm": (_le(rc.get("p95_ms"), RULE["recall_p95_ms"], " ms") if rc.get("n", 0) >= 50
                            else item(NA, f"<= {RULE['recall_p95_ms']:g} ms (n >= 50)", f"n={rc.get('n', 0)}: too few samples")),
        "writes_add_zero_ms_to_turn": item(PASS if m.get("writes_async", True) else FAIL, "0 ms",
                                           "by construction: retain runs at idle, never inside the voice turn (adapter contract)"),
        "slot_seconds_per_turn": _le(m.get("slot_s_per_turn"), RULE["slot_s_per_turn"], " s"),
    }


def gate_g2(arm_runs: "dict[str, dict]", m: dict) -> "dict[str, dict]":
    """Zero violations on every hard invariant, on every seed, and every hard cell must have RUN."""
    viol = hard_violations(arm_runs)
    skipped = hard_not_run(arm_runs)
    bad = {s: v for s, v in viol.items() if v}
    seeds = len(arm_runs)
    out = {
        "hard_cells_zero_violations": item(
            NA if not seeds else (FAIL if bad else PASS), "0 on every seed",
            "no seeds run" if not seeds else (("; ".join(f"{s}: {', '.join(v[:6])}{' ...' if len(v) > 6 else ''}" for s, v in bad.items()))
                                              if bad else f"0 over {seeds} seed(s)")),
        "hard_cells_all_ran": item(NA if not seeds else (FAIL if any(skipped.values()) else PASS), "no hard cell skipped",
                                   ", ".join(f"{s}: {n} skipped" for s, n in skipped.items() if n) or "none skipped"),
    }
    fg = m.get("forgetting") or {}
    for t in ("t0", "t6"):
        f = fg.get(t)
        out[f"forget_{t.replace('t', 't+')}min_no_resurrection"] = item(
            NA if f is None else (PASS if f.get("resurrected", 1) == 0 and f.get("checked", 0) > 0 else FAIL), "0 resurrections",
            "not measured" if f is None else f"{f.get('resurrected')} of {f.get('checked')} probes resurrected ({f.get('how', '')})")
    return out


def gate_g3(m: dict) -> "dict[str, dict]":
    return {
        "zoe_layer_lines": _le(m.get("layer_lines"), RULE["layer_lines_max"], " lines"),
        "deletable_memory_lines": (item(NA, f">= {RULE['delete_lines_min']} of {RULE['memory_lines_total']}", "not measured")
                                   if m.get("deletable_lines") is None else
                                   item(PASS if m["deletable_lines"] >= RULE["delete_lines_min"] else FAIL,
                                        f">= {RULE['delete_lines_min']} of {RULE['memory_lines_total']}",
                                        f"{m['deletable_lines']} ESTIMATE ({m.get('deletable_basis', '')}); not proven deletable")),
    }


# ── one arm, then the decision ───────────────────────────────────────────────

def evaluate_arm(name: str, seed_runs: "dict[str, dict]", measure: dict) -> "dict[str, Any]":
    gates = {"G0": gate_g0(measure), "G1": gate_g1(measure), "G2": gate_g2(seed_runs, measure), "G3": gate_g3(measure)}
    seeds_done = len(seed_runs)
    seeds_item = item(PASS if seeds_done >= RULE["seeds_required"] else NA, f"{RULE['seeds_required']} seeds", f"{seeds_done} seed(s) completed")
    instrument = all(bool((r.get("instrument") or {}).get("ok")) for r in seed_runs.values()) if seed_runs else False
    states = {g: gate_state(items) for g, items in gates.items()}
    if not seed_runs:
        verdict = "INCOMPLETE"
    elif FAIL in states.values():
        verdict = "NOT_ADOPTABLE"
    elif NA in states.values() or seeds_item["state"] != PASS or not instrument:
        verdict = "INCOMPLETE"
    else:
        verdict = "PASSES_BUILT_GATES"
    return {"arm": name, "gates": gates, "gate_states": states, "seeds": seeds_item, "instrument_ok": instrument,
            "axes": aggregate_axes(seed_runs), "verdict": verdict, "seeds_done": seeds_done}


def compare_axes(arm_axes: dict, z0_axes: dict) -> "dict[str, dict]":
    """Per rule letter: does the arm beat Z0 by more than the Wilson 95% interval (arm's lower bound above Z0's upper bound)?
    ``worse`` = the arm's upper bound is below Z0's lower bound (the 'no worse beyond the interval' clause)."""
    out: "dict[str, dict]" = {}
    for letter, axis in WIN_AXES.items():
        if axis is None:
            out[letter] = {"axis": None, "built": False, "beats": False, "worse": False}
            continue
        a, z = arm_axes.get(axis) or {}, z0_axes.get(axis) or {}
        if not a.get("n") or not z.get("n"):
            out[letter] = {"axis": axis, "built": True, "beats": False, "worse": False, "note": "no data"}
            continue
        out[letter] = {"axis": axis, "built": True, "arm": [a["pass"], a["n"], a["wilson95"]], "z0": [z["pass"], z["n"], z["wilson95"]],
                       "beats": a["wilson95"][0] > z["wilson95"][1], "worse": a["wilson95"][1] < z["wilson95"][0]}
    return out


def decide(arms: "dict[str, dict]", z0_axes: dict) -> "dict[str, Any]":
    """The rule's verdict. ``arms`` = ``evaluate_arm`` results by name (H0, H1, H2)."""
    adoptable = [n for n in ("H1", "H2", "H0") if arms.get(n, {}).get("verdict") == "PASSES_BUILT_GATES"]
    cmp_by_arm = {n: compare_axes(a["axes"], z0_axes) for n, a in arms.items()}
    caveat = ("Advisory: this is the pre-registered rule applied to every cell in the spec (B/C/D/E, A3/A8 under authority, poisoning "
              "as a hard axis); known-failing targets count as failures, a skipped cell is never a pass, and the owner decides.")
    if not adoptable:
        incomplete = [n for n, a in arms.items() if a["verdict"] == "INCOMPLETE"]
        text = ("KEEP_Z0: no arm passes every built gate" + (f" ({', '.join(incomplete)} incomplete: not a pass)" if incomplete else "")
                + ". Per the rule: keep Z0 with audit P1-P3.")
        return {"verdict": "KEEP_Z0", "winner": None, "adoptable": [], "compare": cmp_by_arm, "text": text, "caveat": caveat}
    # H1 over H2 when both pass (the rule); H0 can never win: it has no Zoe layer (it is the measurement of what is native)
    order = [n for n in ("H1", "H2") if n in adoptable]
    for n in order:
        c = cmp_by_arm[n]
        built = [k for k, v in c.items() if v["built"]]
        wins = [k for k in built if c[k]["beats"]]
        worse = [k for k in built if c[k]["worse"]]
        if len(wins) >= 2 and not worse:
            return {"verdict": "ADOPT_CANDIDATE", "winner": n, "adoptable": adoptable, "compare": cmp_by_arm, "caveat": caveat,
                    "text": f"ADOPT_CANDIDATE {n}: passes every built gate and beats Z0 beyond the Wilson interval on {', '.join(wins)} "
                            f"(rule needs 2 of B/C/D/E). " + ("H1 chosen over H2 per the rule. " if len(order) == 2 else "")}
    return {"verdict": "KEEP_Z0", "winner": None, "adoptable": adoptable, "compare": cmp_by_arm, "caveat": caveat,
            "text": f"KEEP_Z0 (tie goes to Z0): {', '.join(adoptable)} pass every built gate but do not beat Z0 beyond the Wilson interval "
                    f"on two of B/C/D/E (B extraction, C temporal, D recall, E abstention) without being worse on the others."}


def median(xs: "list[float]") -> "Optional[float]":
    return round(statistics.median(xs), 1) if xs else None


def render_markdown(meta: dict, arms: "dict[str, dict]", decision: dict, z0_axes: dict, z0_off: dict, notes: "list[str]") -> str:
    """The draft ``docs/research/bakeoff-run-<date>.md``: counts and labels only, never household text."""
    L: "list[str]" = []
    L += ["---", "type: Research / bake-off run record", f"title: \"Memory bake-off run {meta.get('date', '')}: the G0-G3 table per arm and the rule's verdict\"",
          "status: DRAFT written by scripts/perf/zmb/bakeoff.py; the owner reads it, nothing here is a decision until the owner says so",
          f"date: {meta.get('date', '')}", "---", "", f"# Memory bake-off run {meta.get('date', '')}", ""]
    L += [f"**Verdict by the pre-registered rule: `{decision['verdict']}`**", "", decision["text"], "", f"> {decision['caveat']}", ""]
    L += ["## Run", "", f"* started {meta.get('started')}, finished {meta.get('finished')}, wall {meta.get('wall_min')} min (cap {meta.get('cap_min')} min)",
          f"* revision {meta.get('commit')}; Hindsight {meta.get('hindsight_version')}; clone `{meta.get('clone_model')}` --parallel 1 on :11500; embeddings {meta.get('embed_model')}",
          f"* seeds: {', '.join(meta.get('seeds', []))}; arms run: {', '.join(meta.get('arms_run', []))}; aborted: {meta.get('aborted') or 'no'}",
          f"* brain restored: {meta.get('restore')}", ""]
    for name in ("H0", "H1", "H2"):
        a = arms.get(name)
        if not a:
            continue
        L += [f"## {name}: `{a['verdict']}`  (seeds completed {a['seeds_done']}/{RULE['seeds_required']}, instrument ok: {a['instrument_ok']})", "",
              "| Gate | Item | Threshold | Measured | Result |", "|---|---|---|---|---|"]
        for g, items in a["gates"].items():
            for k, v in items.items():
                L.append(f"| {g} | {k} | {v['threshold']} | {v['measured']} | {v['state']} |")
        L.append("")
    L += ["## Axes: pass / n with Wilson 95% (cells that ran; sanity cells excluded; SKIPs are not passes)", "",
          "| Axis | Z0 | Z0-off (negative control) | " + " | ".join(n for n in ("H0", "H1", "H2") if n in arms) + " |",
          "|---|---|---|" + "---|" * len([n for n in ("H0", "H1", "H2") if n in arms])]
    names = [n for n in ("H0", "H1", "H2") if n in arms]

    def cell(a: dict, axis: str) -> str:
        s = (a or {}).get(axis)
        if not s or not s.get("n"):
            return "-"
        return f"{s['pass']}/{s['n']} ({s['wilson95'][0]:.2f}-{s['wilson95'][1]:.2f})" + (f", {s['skipped']} skipped" if s.get("skipped") else "")
    for axis in sorted({ax for a in [z0_axes, z0_off] + [arms[n]["axes"] for n in names] for ax in a}):
        L.append(f"| {axis} | {cell(z0_axes, axis)} | {cell(z0_off, axis)} | " + " | ".join(cell(arms[n]["axes"], axis) for n in names) + " |")
    L += ["", "## Winner clause (beats Z0 beyond the Wilson interval on 2 of B/C/D/E, no worse elsewhere)", ""]
    for n, c in decision["compare"].items():
        L.append(f"* {n}: " + "; ".join(f"{k}={'beats' if v['beats'] else ('WORSE' if v['worse'] else 'tie' if v['built'] else 'not built')}" for k, v in c.items()))
    L += ["", "## What this run did not verify", ""] + [f"* {n}" for n in notes] + [""]
    return "\n".join(L)
