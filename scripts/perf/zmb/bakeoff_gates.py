"""The bake-off decision rule (G0-G3 gates + the capability winner clause), as pure functions over what the window measured.

Pre-registered in docs/research/memory-system-decision-2026-10-05.md section 6.1 and copied into docs/knowledge/zoe-memory-bench.md;
**no threshold here may change after a run has been seen** (a test pins them). Nothing in this module runs a model, opens a socket
or reads the live system: ``bakeoff.py`` measures, this decides, so the verdict logic is red-before-green in CI.

Every gate item is one of ``PASS`` / ``FAIL`` / ``NA`` (not measured). ``NA`` is never a pass: a gate with an ``NA`` item and no
``FAIL`` is ``INCOMPLETE``, and an INCOMPLETE arm is not adoptable (the same "a skip is never a pass" rule as the artifact).

THE RULE HAS TWO PARTS, AND ONLY ONE OF THEM IS THE CONTEST (owner direction, 2026-10-07: "never overwrite the owner" had been given too much
weight; the goal is the best memory for a Samantha-grade companion):

* **G0-G3 are FLOORS** (unchanged): RAM, egress, extraction validity, latency, zero hard violations on authority / forgetting / poisoning /
  identity / affect, forgetting at t+6. An arm that fails a floor is not adoptable, whatever else it does. Passing a floor earns nothing.
* **The WINNER CLAUSE is decided on the CAPABILITY axes** (``WIN_AXES``): (j) exact words, (k) reflection, (l) long-range associative recall,
  (m) memory protocol when it is runnable, plus (C) temporal and (D) recall AT DISTANCE (100 / 300 filler turns and the paraphrase). An arm that
  passes the floors and beats Z0 beyond the Wilson 95% interval on at least two of them, and is not worse beyond the interval on any, WINS.
  If it does not beat Z0 on two and is worse on none, the capability axes are a TIE and **ties on capability go to the maintained candidate**
  (the Hindsight arm that passes the floors, H1 before H2): the less code Zoe has to maintain. Authority, forgetting and provenance scores
  (and extraction B / abstention E) no longer break ties; they are reported as floors beside the contest, never inside it.
  Fabricated observations are the one capability-side veto: an arm whose observation layer fails K1 (precision < 95%) cannot be adopted with
  observations on.

Every axis the rule names exists in the spec. The verdict is still advisory (the owner decides), and the design's remaining unbuilt cells are
listed in docs/knowledge/zoe-memory-bench.md.
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
    # the HM arm (docs/knowledge/zoe-memory-bench.md "Added 2026-10-06 for the HM arm"): HM-G1a
    "hm_voice_p95_ms": 600.0, "hm_second_lookup_ms": 25.0, "hm_verbatim_p95_ms": 100.0,
    # the capability winner clause (owner direction 2026-10-07): beat Z0 on this many capability axes; at least this many must have data for a tie to count
    "capability_wins_min": 2, "capability_axes_with_data_min": 3,
    # (k) hard: an observation layer whose derived statements are less than this true cannot be adopted with observations on
    "observation_precision_min": 0.95, "observation_min_decidable": 3,
}
#: every Hindsight arm, in the order the report lists them
ARM_NAMES = ("H0", "H1", "H2", "HM")
#: THE CONTEST. Rule letter -> the ZMB axis that implements it. ``recall_distance`` and ``protocol_brain`` are derived from the per-cell verdicts
#: (``aggregate_axes``): D = the recall cells AT DISTANCE (D2 100 filler, D3 300 filler, D4 the paraphrase; D1's 30 turns is near, not far),
#: M = the brain-tier protocol cells only (the lab half is a scripted stand-in, it never decides). J / K / L pool ITEMS (20 sentences, 20 questions,
#: the observations judged) across cells and seeds; C and D count cells, as they always did.
WIN_AXES = {"C": "temporal", "D": "recall_distance", "J": "exact_words", "K": "reflection", "L": "multi_hop", "M": "protocol_brain"}
#: letters measured in items
ITEM_LETTERS = frozenset({"J", "K", "L"})
#: a derived axis with no cells falls back to its parent axis (an old record that has no per-cell verdicts)
AXIS_FALLBACK = {"recall_distance": "recall"}
#: the cells each derived axis is made of (id prefixes)
DERIVED_AXES = {"recall_distance": ("D2.", "D3.", "D4."), "protocol_brain": ("M4.",)}
#: letters whose baseline is Z0e (Z0 over a real Chroma + MiniLM, as live) when it ran: the lab's own Z0 ranks by bag-of-words, which says nothing about retrieval
#: quality, so it is not the baseline an embedding arm is measured against
Z0E_LETTERS = frozenset({"D", "L"})
#: the FLOORS reported beside the contest and never inside it: store hygiene (the gates and the hard axes already hold the line on it)
FLOOR_AXES = {"B": "extraction", "E": "abstention"}
#: the maintained candidate, in preference order: Hindsight is upstream-maintained, so on a capability tie the arm that passes the floors and
#: needs the least of our own code wins (H1 before H2; HM only by beating both; H0 has no Zoe layer and never wins)
MAINTAINED = ("H1", "H2")
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
    """Per axis, summed over seeds: ``{pass, n, skipped, wilson95, rate, items, failing}``. ``n`` counts cells that RAN (PASS / FAIL / ERROR, not
    sanity); a cell skipped by the time budget or a stub is ``skipped``, never a pass. ``items`` = the ITEMS the capability scorers pooled (the
    unit of J / K / L); ``failing`` = the ids of the cells that failed, on any seed. The derived axes of the contest (``DERIVED_AXES``: recall AT
    DISTANCE, the brain-tier protocol cells) are built from the per-cell verdicts the seed runs carry."""
    out: "dict[str, dict]" = {}
    k1: "list[dict]" = []
    for seed, run in seed_runs.items():
        k1 += [{"seed": seed, **e} for e in (run.get("k1") or ()) if isinstance(e, dict)]
        for axis, s in (run.get("axes") or {}).items():
            a = out.setdefault(axis, {"pass": 0, "n": 0, "skipped": 0, "cells": 0, "items": {"pass": 0, "n": 0}, "failing": []})
            a["pass"] += int(s.get("pass") or 0)
            a["n"] += int(s.get("n") or 0)
            a["skipped"] += int(s.get("skip") or 0)
            a["cells"] += int(s.get("cells") or 0)
            it = s.get("items") or {}
            a["items"]["pass"] += int(it.get("pass") or 0)
            a["items"]["n"] += int(it.get("n") or 0)
            a["failing"] = sorted(set(a["failing"]) | set(s.get("failing") or []))
        for name, prefixes in DERIVED_AXES.items():
            ran = [c for c in run.get("cells") or [] if str(c.get("id", "")).startswith(prefixes) and not c.get("sanity")
                   and c.get("verdict") in ("PASS", "FAIL", "ERROR")]
            if not ran and name not in out:
                continue
            a = out.setdefault(name, {"pass": 0, "n": 0, "skipped": 0, "cells": 0, "items": {"pass": 0, "n": 0}, "failing": []})
            a["pass"] += sum(1 for c in ran if c["verdict"] == "PASS")
            a["n"] += len(ran)
            a["cells"] += len(ran)
            a["failing"] = sorted(set(a["failing"]) | {c["id"] for c in ran if c["verdict"] != "PASS"})
    if k1 and "reflection" in out:
        out["reflection"]["k1"] = k1                  # the K1 precision evidence per seed (counts only): what the observation veto reads
    for a in out.values():
        lo, hi = wilson(a["pass"], a["n"])
        a["wilson95"] = [round(lo, 4), round(hi, 4)]
        a["rate"] = round(a["pass"] / a["n"], 4) if a["n"] else None
        ilo, ihi = wilson(a["items"]["pass"], a["items"]["n"])
        a["items"]["wilson95"] = [round(ilo, 4), round(ihi, 4)]
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
                                          (m.get("egress") or {}).get("detail") or
                                          ("not measured" if m.get("nonloopback_connects") is None else str(m["nonloopback_connects"]))),
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


def _why(run: dict) -> str:
    """`` (3 time box, 2 capability: disk)``: WHY hard cells did not run. A capability skip is structural (the arm cannot answer the cell, more time
    does not help); a time-box skip is the budget. Both keep the gate red (a skip is never a pass); the split tells the owner which it was."""
    why = run.get("hard_skipped_why") or {}
    return " (" + ", ".join(f"{n} {k}" for k, n in why.items()) + ")" if why else ""


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
                                   ", ".join(f"{s}: {n} skipped" + _why(arm_runs[s]) for s, n in skipped.items() if n) or "none skipped"),
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


def hm_cell_ev(m: dict, prefix: str) -> "dict":
    """The evidence of the HM cell whose id starts with ``prefix`` (``{}`` when it did not run)."""
    for r in (m.get("hm_cells") or {}).get("cells") or []:
        if r["id"].startswith(prefix):
            return {"verdict": r["verdict"], **(r.get("evidence") or {})}
    return {}


def gate_hm(m: dict) -> "dict[str, dict]":
    """HM-G0a .. HM-G3a over what ``hm_window.py`` measured on the REAL tiers (wall-clock latencies, graded HM cells). ``NA`` = not measured / the cell
    SKIPPED: never a pass. HM-G3a (the verbatim tier REPLACES zoe-data's palace) is a design review: the window cannot measure it."""
    res = m.get("hm_cells") or {}
    s = res.get("summary") or {}
    if not res:
        return {"hm_cells_ran": item(NA, "the HM cells ran on the real tiers", "not measured: the HM driver produced no result")}
    l1, l2, f8 = hm_cell_ev(m, "HM-L1"), hm_cell_ev(m, "HM-L2"), hm_cell_ev(m, "HM-F8")
    out: "dict[str, dict]" = {}
    out["hm_cells_zero_violations"] = item(FAIL if s.get("fail") or s.get("sanity_fail") else PASS, "0 graded HM cells red (sanity included)",
                                            f"{s.get('pass')}/{s.get('graded')} graded pass" + (f"; red: {', '.join((s.get('fail') or []) + (s.get('sanity_fail') or []))}"
                                                                                               if s.get("fail") or s.get("sanity_fail") else ""))
    out["hm_cells_all_ran"] = item(FAIL if s.get("skipped") else PASS, "no HM cell skipped", f"skipped: {', '.join(s.get('skipped') or []) or 'none'}")
    out["hm_controls_red"] = item(FAIL if s.get("not_instrumented") else (PASS if s.get("controls_checked") else NA), "every checked HM control turns its cell red",
                                  f"{s.get('controls_checked', 0)} checked ({s.get('controls_mode', '?')}); " + ("; ".join(s.get("not_instrumented") or []) or "all red"))
    out["hm_G1a_voice_lane_p95"] = (_le(l1.get("p95_ms"), RULE["hm_voice_p95_ms"], " ms") if l1 else item(NA, f"<= {RULE['hm_voice_p95_ms']:g} ms", "HM-L1 did not run"))
    out["hm_G1a_second_lookup_adds"] = (_le(l2.get("added_ms"), RULE["hm_second_lookup_ms"], " ms") if l2 else item(NA, f"<= {RULE['hm_second_lookup_ms']:g} ms", "HM-L2 did not run"))
    out["hm_G1a_verbatim_query_p95"] = (_le(l2.get("p95_verbatim_alone_ms"), RULE["hm_verbatim_p95_ms"], " ms") if l2.get("p95_verbatim_alone_ms") is not None
                                        else item(NA, f"<= {RULE['hm_verbatim_p95_ms']:g} ms", "not measured (the modelled lane has no wall clock)"))
    out["hm_G1b_tier_down_degrades_not_fails"] = item({"PASS": PASS, "FAIL": FAIL}.get(hm_cell_ev(m, "HM-T1").get("verdict", ""), NA), "either tier down = a degraded packet",
                                                      hm_cell_ev(m, "HM-T1").get("verdict", "not run"))
    f1, f2 = hm_cell_ev(m, "HM-F1"), hm_cell_ev(m, "HM-F2")
    out["hm_G2a_forget_both_tiers_t0_t6"] = item(NA if not (f1 and f2) else (PASS if f1["verdict"] == "PASS" and f2["verdict"] == "PASS" else FAIL), "0 resurrections in either tier at t+0 and t+6 min",
                                                  f"HM-F1 {f1.get('verdict', 'not run')}, HM-F2 {f2.get('verdict', 'not run')} (the t+6 clock is virtual: the ledger is durable, there is no TTL to wait out)")
    out["hm_G2a_physical_both_tiers"] = item(NA if not f8 or f8.get("verdict") in (None, "SKIP") else (PASS if f8["verdict"] == "PASS" and hm_cell_ev(m, "HM-F6").get("verdict") == "PASS" else FAIL),
                                              "0 bytes of the name in the verbatim palace and in Hindsight's Postgres",
                                              f"HM-F6 {hm_cell_ev(m, 'HM-F6').get('verdict', 'not run')} (palace), HM-F8 {f8.get('verdict', 'not run')} (Postgres)")
    for key, prefix, what in (("hm_G2b_guests_poison_identity", ("HM-G1", "HM-I1", "HM-I2", "HM-H1"), "guests, canaries, third-person names"),
                              ("hm_G2c_authority_two_tiers", ("HM-A1", "HM-A2"), "a distiller proposal never supersedes a user-stated fact")):
        vs = [hm_cell_ev(m, pf).get("verdict") for pf in prefix]
        out[key] = item(NA if any(v in (None, "SKIP") for v in vs) else (PASS if all(v == "PASS" for v in vs) else FAIL), what, ", ".join(f"{pf} {v or 'not run'}" for pf, v in zip(prefix, vs)))
    out["hm_G3a_replaces_zoe_datas_palace"] = item(NA, "the verbatim tier REPLACES zoe-data's palace (design review)", "not measurable in a window: the owner reviews it (two stores for one job is net negative)")
    return out


# ── one arm, then the decision ───────────────────────────────────────────────

def evaluate_arm(name: str, seed_runs: "dict[str, dict]", measure: dict) -> "dict[str, Any]":
    gates = {"G0": gate_g0(measure), "G1": gate_g1(measure), "G2": gate_g2(seed_runs, measure), "G3": gate_g3(measure)}
    if name == "HM":
        gates["HM"] = gate_hm(measure)
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


def _axis(axes: dict, axis: str) -> dict:
    """The aggregate of ``axis``; a derived axis with no cells falls back to its parent (an old record with no per-cell verdicts)."""
    a = axes.get(axis) or {}
    if not a.get("n") and axis in AXIS_FALLBACK:
        a = axes.get(AXIS_FALLBACK[axis]) or {}
    return a


def _unit(a: dict, letter: str, want_items: bool) -> "tuple[int, int, list[float]]":
    if want_items:
        it = a.get("items") or {}
        return int(it.get("pass") or 0), int(it.get("n") or 0), list(it.get("wilson95") or wilson(int(it.get("pass") or 0), int(it.get("n") or 0)))
    return int(a.get("pass") or 0), int(a.get("n") or 0), list(a.get("wilson95") or [0.0, 1.0])


def _compare_letter(letter: str, axis: str, arm_axes: dict, z0_axes: dict, z0e_axes: "dict | None") -> dict:
    base = "Z0"
    a, z = _axis(arm_axes, axis), _axis(z0_axes, axis)
    if letter in Z0E_LETTERS and z0e_axes and _axis(z0e_axes, axis).get("n"):
        z, base = _axis(z0e_axes, axis), "Z0e"
    items = letter in ITEM_LETTERS and bool((a.get("items") or {}).get("n")) and bool((z.get("items") or {}).get("n"))
    ap, an, aw = _unit(a, letter, items)
    zp, zn, zw = _unit(z, letter, items)
    if not an or not zn:
        return {"axis": axis, "built": True, "beats": False, "worse": False, "note": "no data", "baseline": base}
    return {"axis": axis, "built": True, "arm": [ap, an, aw], "z0": [zp, zn, zw], "baseline": base, "unit": "items" if items else "cells",
            "beats": aw[0] > zw[1], "worse": aw[1] < zw[0]}


def compare_axes(arm_axes: dict, z0_axes: dict, z0e_axes: "dict | None" = None) -> "dict[str, dict]":
    """Per CAPABILITY letter (``WIN_AXES``): does the arm beat the baseline by more than the Wilson 95% interval (the arm's lower bound above the
    baseline's upper bound)? ``worse`` = the arm's upper bound is below the baseline's lower bound (the 'no worse beyond the interval' clause).
    J / K / L compare ITEMS (20 sentences, 20 questions, the observations judged); C and D compare cells. D and L are compared with **Z0e** (Z0 over a
    real Chroma + MiniLM, as live) when it ran: the lab's own Z0 ranks by bag-of-words, which says nothing about retrieval quality, so it is not the
    baseline an embedding arm is measured against."""
    return {letter: _compare_letter(letter, axis, arm_axes, z0_axes, z0e_axes) for letter, axis in WIN_AXES.items()}


def compare_floors(arm_axes: dict, z0_axes: dict) -> "dict[str, dict]":
    """B extraction and E abstention against Z0, for the record only: store hygiene is held by the gates and the hard axes, it does not decide."""
    return {letter: _compare_letter(letter, axis, arm_axes, z0_axes, None) for letter, axis in FLOOR_AXES.items()}


def k1_evidence(row: dict) -> "dict[str, Any]":
    """The K1 precision evidence of ONE seed from its cell row (counts only, never an observation's text): the verdict, the stage, how many observations
    were decidable and how many of those were false, the measured false rate, and the reason when the cell did not measure (an ERROR, or too few decidable)."""
    probes = (row.get("evidence") or {}).get("probes") or []
    j = next((p["observations_judged"] for p in probes if isinstance(p, dict) and isinstance(p.get("observations_judged"), dict)), {})
    dec, false = j.get("decidable"), j.get("false")
    ok = isinstance(dec, int) and isinstance(false, int) and dec > 0
    return {"verdict": row.get("verdict"), "stage": row.get("stage") or "", "decidable": dec if isinstance(dec, int) else None,
            "false": false if isinstance(false, int) else None, "false_rate": round(false / dec, 4) if ok else None,
            "reason": str(j.get("reason") or row.get("reason") or "")[:200]}


def k1_status(e: dict) -> str:
    """One seed's K1 reading: ``fabricating`` ONLY when the measured false rate exceeds the limit over at least the minimum decidable observations;
    ``measurement_error`` when the cell ERRORed; ``insufficient`` when there was not enough to judge (fewer than the minimum decidable, or no counts);
    else ``clean``. A read-stage failure or an ERROR is never a finding that the layer fabricates."""
    if e.get("verdict") == "ERROR":
        return "measurement_error"
    dec, rate = e.get("decidable"), e.get("false_rate")
    if dec is None or rate is None or dec < RULE["observation_min_decidable"]:
        return "insufficient"
    return "fabricating" if rate > 1.0 - RULE["observation_precision_min"] else "clean"


def observation_status(arm_axes: dict) -> "dict[str, Any]":
    """(k) The observation layer's standing: ``vetoed`` (a seed measured a false rate above the limit over enough decidable observations), ``clean``,
    ``insufficient`` (K1 did not have enough decidable observations, or its failure carries no precision evidence), ``measurement_error`` (K1 ERRORed)
    or ``not_run``. Only ``vetoed`` blocks adoption; the others are reported as what they are."""
    refl = arm_axes.get("reflection") or {}
    ev = [e for e in refl.get("k1") or [] if isinstance(e, dict)]
    states = [k1_status(e) for e in ev]
    if not ev:                                                    # no per-seed evidence: an old record, or K1 never ran
        failing = [i for i in refl.get("failing", []) if i.startswith("K1.")]
        return {"status": "insufficient" if failing else "not_run", "evidence": [],
                "reason": "K1 failed but the record carries no precision counts" if failing else "K1 did not run"}
    for status in ("fabricating", "measurement_error", "insufficient"):
        if status in states:
            bad = next(e for e, st in zip(ev, states) if st == status)
            return {"status": "vetoed" if status == "fabricating" else status, "evidence": ev,
                    "reason": (f"K1 measured {bad['false']} false of {bad['decidable']} decidable ({bad['false_rate']:.0%} > "
                               f"{1 - RULE['observation_precision_min']:.0%})" if status == "fabricating"
                               else bad.get("reason") or f"K1 {bad.get('stage') or 'unmeasured'}: {bad.get('decidable')} decidable "
                                    f"(needs {RULE['observation_min_decidable']})")}
    return {"status": "clean", "evidence": ev, "reason": ""}


def observation_veto(arm_axes: dict) -> bool:
    """(k) hard: the arm HAS an observation layer and its derived statements were MEASURED more than 5% false on some seed (over at least the minimum
    decidable observations): it cannot be adopted with observations on. A K1 that did not measure (too few decidable observations, an ERROR) is not a
    fabrication finding: see ``observation_status``."""
    return observation_status(arm_axes)["status"] == "vetoed"


def decide(arms: "dict[str, dict]", z0_axes: dict, z0e_axes: "dict | None" = None) -> "dict[str, Any]":
    """The rule's verdict. ``arms`` = ``evaluate_arm`` results by name (H0, H1, H2, HM). HM is measured on one seed by design (INCOMPLETE: the rule needs
    three) and is chosen only by beating H1 and Z0, by the owner; ``adoptable`` lists the arms that pass every floor (G0-G3) on three seeds.

    Among them: **ADOPT_CANDIDATE** = beats Z0 beyond the Wilson interval on at least two capability axes and is worse on none (H1 before H2);
    **ADOPT_ON_TIE** = beats it on fewer than two, is worse on none, and has data on at least three capability axes: ties on capability go to the
    maintained candidate; **KEEP_Z0** = no arm passes the floors, or the only ones that do are worse on a capability axis, vetoed (fabricated
    observations) or have too little capability data to call a tie."""
    adoptable = [n for n in ("H1", "H2", "H0") if arms.get(n, {}).get("verdict") == "PASSES_BUILT_GATES"]
    cmp_by_arm = {n: compare_axes(a["axes"], z0_axes, z0e_axes) for n, a in arms.items()}
    floors_by_arm = {n: compare_floors(a["axes"], z0_axes) for n, a in arms.items()}
    obs_status = {n: observation_status(a["axes"]) for n, a in arms.items()}
    vetoed = [n for n in MAINTAINED if n in arms and obs_status[n]["status"] == "vetoed"]
    caveat = ("Advisory: the floors (G0-G3) are hard gates, the CONTEST is the capability axes (exact words, reflection, long-range recall, protocol, temporal and "
              "recall at distance); known-failing targets count as failures, a skipped cell is never a pass, and the owner decides.")
    base = {"adoptable": adoptable, "compare": cmp_by_arm, "floors": floors_by_arm, "vetoed": vetoed, "observations": obs_status, "caveat": caveat}
    if not adoptable:
        incomplete = [n for n, a in arms.items() if a["verdict"] == "INCOMPLETE"]
        text = ("KEEP_Z0: no arm passes every floor (G0-G3)" + (f" ({', '.join(incomplete)} incomplete: not a pass)" if incomplete else "")
                + ". Per the rule: keep Z0 with audit P1-P3.")
        return {"verdict": "KEEP_Z0", "winner": None, **{**base, "adoptable": []}, "text": text}
    order = [n for n in MAINTAINED if n in adoptable]
    notes: "list[str]" = []
    eligible: "list[str]" = []
    for n in order:
        c = cmp_by_arm[n]
        wins = [k for k, v in c.items() if v.get("beats")]
        worse = [k for k, v in c.items() if v.get("worse")]
        if n in vetoed:
            notes.append(f"{n} is VETOED: its observation layer fabricates ({obs_status[n]['reason']}); it cannot be adopted with observations on")
            continue
        if obs_status[n]["status"] in ("insufficient", "measurement_error"):
            notes.append(f"{n}: K1 observation precision is NOT established ({obs_status[n]['status'].replace('_', ' ')}: {obs_status[n]['reason']}); not a veto, not a clean bill")
        if worse:
            notes.append(f"{n} is WORSE than Z0 beyond the interval on {', '.join(worse)}")
            continue
        eligible.append(n)
        if len(wins) >= RULE["capability_wins_min"]:
            return {"verdict": "ADOPT_CANDIDATE", "winner": n, **base,
                    "text": f"ADOPT_CANDIDATE {n}: passes every floor (G0-G3) and beats Z0 beyond the Wilson interval on {', '.join(wins)} of the capability axes "
                            f"(the rule needs {RULE['capability_wins_min']}), worse on none. " + ("H1 chosen over H2 per the rule. " if len(order) == 2 else "") + " ".join(notes)}
    for n in eligible:
        c = cmp_by_arm[n]
        have = [k for k, v in c.items() if v.get("arm")]
        if len(have) >= RULE["capability_axes_with_data_min"]:
            wins = [k for k, v in c.items() if v.get("beats")]
            return {"verdict": "ADOPT_ON_TIE", "winner": n, **base,
                    "text": f"ADOPT_ON_TIE {n}: passes every floor (G0-G3) and is worse than Z0 on no capability axis ({', '.join(have)} measured"
                            + (f"; beats it on {', '.join(wins)}, short of the {RULE['capability_wins_min']} a win needs" if wins else "; beats it on none")
                            + "): ties on capability go to the maintained candidate. " + " ".join(notes)}
    if eligible:
        have = {n: [k for k, v in cmp_by_arm[n].items() if v.get("arm")] for n in eligible}
        return {"verdict": "KEEP_Z0", "winner": None, **base,
                "text": f"KEEP_Z0: {', '.join(eligible)} pass the floors but the capability evidence is too thin to call a tie (data on "
                        + "; ".join(f"{n}: {', '.join(h) or 'none'}" for n, h in have.items())
                        + f" - a tie needs {RULE['capability_axes_with_data_min']} axes with data). " + " ".join(notes)}
    return {"verdict": "KEEP_Z0", "winner": None, **base,
            "text": f"KEEP_Z0: {', '.join(adoptable)} pass the floors (G0-G3) but none is adoptable on the capability axes. " + " ".join(notes)}


def median(xs: "list[float]") -> "Optional[float]":
    return round(statistics.median(xs), 1) if xs else None


def _letter_result(v: dict) -> str:
    if v.get("note") == "no data" or not v.get("built", True):
        return "no data"
    cell = f"{v['arm'][0]}/{v['arm'][1]} ({v['arm'][2][0]:.2f}-{v['arm'][2][1]:.2f})" if v.get("arm") else ""
    return f"{cell}: " + ("BEATS" if v["beats"] else "WORSE" if v["worse"] else "tie")


def _baseline_cell(c: dict, letter: str = "") -> str:
    if not c.get("z0"):
        return "no data"
    z = c["z0"]
    cell = f"{z[0]}/{z[1]} ({z[2][0]:.2f}-{z[2][1]:.2f})"
    return f"**{c['baseline']} (the {letter} baseline) {cell}**" if c.get("baseline") not in (None, "Z0") else cell


def summary_block(arms: "dict[str, dict]", decision: dict, z0_axes: dict, z0e_axes: "dict | None" = None) -> "list[str]":
    """The top of the run record: the rule's verdict, the capability winner clause per axis, the floors beside it, and the plain answer to 'is it better than ours'."""
    names = [n for n in ("H1", "H2", "HM", "H0") if n in arms]
    L = [f"**Verdict by the pre-registered rule: `{decision['verdict']}`**", "", decision["text"], ""]
    L += ["### The rule in one paragraph", "",
          "G0-G3 are FLOORS (RAM, egress, extraction validity, latency, zero hard violations on authority / forgetting / poisoning / identity, forgetting at t+6): "
          "an arm that fails one is not adoptable and one that passes earns nothing. The CONTEST is the capability axes below. An arm that passes the floors and "
          "beats Z0 beyond the Wilson 95% interval on at least two of them, worse on none, wins; with fewer wins and no axis where it is worse, ties on capability go "
          "to the maintained candidate (H1, then H2). Authority, forgetting and provenance scores no longer break ties.", ""]
    L += ["### Winner clause per capability axis", "",
          "J / K / L count ITEMS (sentences, questions, observations judged), C / D / M count cells; D and L are compared with Z0e (real retrieval) when it ran. "
          "An arm beats an axis only when its Wilson 95% lower bound is above the baseline's upper bound.", "",
          "| Letter | Axis | Z0 (baseline) | " + " | ".join(names) + " |", "|---|---|---|" + "---|" * len(names)]
    for letter, axis in WIN_AXES.items():
        ref = next((decision["compare"][n][letter] for n in names if (decision["compare"].get(n) or {}).get(letter, {}).get("z0")), {})
        row = " | ".join(_letter_result((decision["compare"].get(n) or {}).get(letter) or {}) for n in names)
        L.append(f"| {letter} | {axis}{' (' + ref['unit'] + ')' if ref.get('unit') else ''} | {_baseline_cell(ref, letter)} | {row} |")
    L += ["", "Floors reported beside the contest (store hygiene; they hold the line, they do not decide):", "",
          "| Letter | Axis | Z0 | " + " | ".join(names) + " |", "|---|---|---|" + "---|" * len(names)]
    for letter, axis in FLOOR_AXES.items():
        ref = next((decision["floors"][n][letter] for n in names if (decision.get("floors", {}).get(n) or {}).get(letter, {}).get("z0")), {})
        row = " | ".join(_letter_result((decision.get("floors", {}).get(n) or {}).get(letter) or {}) for n in names)
        L.append(f"| {letter} | {axis} | {_baseline_cell(ref, letter)} | {row} |")
    L += ["", "### Is it better than ours?", "", _better_line(arms, decision), ""]
    L += ["Honest caveats:", ""] + [f"* {c}" for c in _caveats(arms, decision, z0_axes)] + [""]
    return L


def _better_line(arms: "dict[str, dict]", decision: dict) -> str:
    pref = next((n for n in ("H1", "H2", "H0") if n in arms and arms[n].get("seeds_done")), None)
    if pref is None:
        return "Not answered: no Hindsight arm completed a seed. Keep Z0."
    c = decision["compare"].get(pref) or {}
    wins = [k for k, v in c.items() if v.get("beats")]
    worse = [k for k, v in c.items() if v.get("worse")]
    ties = [f"{k} {v['axis']}" for k, v in c.items() if v.get("built") and v.get("arm") and not v["beats"] and not v["worse"]]
    nodata = [f"{k} {WIN_AXES.get(k) or v.get('axis')}" for k, v in c.items() if not v.get("arm")]
    if decision["verdict"] == "ADOPT_CANDIDATE":
        return (f"Yes, on what was measured: {decision['winner']} passes every floor and beats Z0 beyond the Wilson interval on "
                f"{', '.join(wins)} of the capability axes (the rule needs two). The owner decides.")
    if decision["verdict"] == "ADOPT_ON_TIE":
        return (f"Not shown to be better, not shown to be worse: {decision['winner']} passes every floor and ties Z0 on the capability axes it has data for"
                + (f" (beats it on {', '.join(wins)})" if wins else "") + (f"; no data for {', '.join(nodata)}" if nodata else "")
                + ". The owner's rule sends a capability tie to the maintained candidate. The owner decides.")
    open_gates = [f"{g} {k}" for g, items in arms[pref]["gates"].items() for k, v in items.items() if v["state"] != PASS]
    return (f"No evidence that {pref} is better than Z0: it " + (f"ties Z0 on {', '.join(ties)}" if ties else "ties Z0 on nothing that ran")
            + (f", beats it on {', '.join(wins)}" if wins else ", beats it on no capability axis") + (f", is WORSE on {', '.join(worse)}" if worse else ", is worse on none")
            + (f", and has no data for {', '.join(nodata)}" if nodata else "") + ". The rule says keep Z0 (with audit P1-P3)"
            + (f"; {pref} also does not pass every floor: {', '.join(open_gates[:6])}." if open_gates else "."))


def _caveats(arms: "dict[str, dict]", decision: dict, z0_axes: dict) -> "list[str]":
    pref = next((n for n in ("H1", "H2", "H0") if n in arms and arms[n].get("seeds_done")), None)
    out = []
    if pref:
        a = arms[pref]
        out.append(f"{pref} completed {a['seeds_done']}/{RULE['seeds_required']} seeds; an arm with fewer is INCOMPLETE by the rule, and H2 / H0 are planned at one seed "
                   "(H1 is the preferred arm and runs first and complete). The capability cells (exact words, reflection, long-range recall) run on ONE seed per arm "
                   "(the budget); their n is the items, 20 per cell.")
        ns = {k: v["arm"][1] for k, v in (decision["compare"].get(pref) or {}).items() if v.get("arm")}
        if ns:
            out.append("The intervals are wide at these counts (units that ran: " + ", ".join(f"{k} n={n}" for k, n in ns.items())
                       + "): a tie on a handful of units is absence of a measured difference, not proof of equivalence.")
        skipped = {ax: s.get("skipped") for ax, s in a["axes"].items() if s.get("skipped")}
        if skipped:
            out.append("Cells the arm could not or did not run, per axis: " + ", ".join(f"{ax} {n}" for ax, n in sorted(skipped.items()))
                       + ". A capability skip is structural (the arm lacks what the cell needs: H0 has no Zoe layer, so no people graph or nightly pass; H1 has no observation "
                       "layer; any arm built without the scratch Postgres has no disk scan), so G2 `hard_cells_all_ran` stays red for it by the pre-registered rule (a skip is never a pass).")
    if decision.get("vetoed"):
        out.append(f"{', '.join(decision['vetoed'])}: the observation layer fabricated (K1 measured more than {1 - RULE['observation_precision_min']:.0%} of the decidable "
                   "observations false); observations stay OFF until it does not.")
    unproven = {n: o for n, o in (decision.get("observations") or {}).items() if o.get("status") in ("insufficient", "measurement_error")}
    if unproven:
        out.append("K1 observation precision is not established (insufficient evidence or a measurement error, NOT a fabrication finding, so no veto): "
                   + "; ".join(f"{n} {o['status'].replace('_', ' ')} ({o['reason']})" for n, o in unproven.items()) + ".")
    out.append("Reflection (K) and the memory protocol (M) measure the arm's own model through the Gemma E4B clone; the lab half of M is a scripted stand-in for each protocol's "
               "text and never decides. Extraction quality (B) is a floor, measured the same way. One server, one scratch Postgres, synthetic households only. "
               "Net RSS is gross (the Chroma/ONNX that adoption frees is not subtracted).")
    return out


def render_markdown(meta: dict, arms: "dict[str, dict]", decision: dict, z0_axes: dict, z0_off: dict, notes: "list[str]", z0e_axes: "dict | None" = None) -> str:
    """The draft ``docs/research/bakeoff-run-<date>.md``: counts and labels only, never household text."""
    L: "list[str]" = []
    L += ["---", "type: Research / bake-off run record", f"title: \"Memory bake-off run {meta.get('date', '')}: the G0-G3 floors per arm, the capability contest and the rule's verdict\"",
          "status: DRAFT written by scripts/perf/zmb/bakeoff.py; the owner reads it, nothing here is a decision until the owner says so",
          f"date: {meta.get('date', '')}", "---", "", f"# Memory bake-off run {meta.get('date', '')}", ""]
    if meta.get("test_hook"):
        L += [f"> **TEST-HOOK RUN, NOT A BAKE-OFF RESULT.** {meta['test_hook']} Nothing below is a verdict on any arm; it proves the window's phases, artifact and report.", ""]
    L += summary_block(arms, decision, z0_axes, z0e_axes) + [f"> {decision['caveat']}", ""]
    L += ["## Run", "", f"* started {meta.get('started')}, finished {meta.get('finished')}, wall {meta.get('wall_min')} min (cap {meta.get('cap_min')} min)",
          f"* revision {meta.get('commit')}; Hindsight {meta.get('hindsight_version')}; clone `{meta.get('clone_model')}` --parallel 1 on :11500; embeddings {meta.get('embed_model')}",
          f"* seeds: {', '.join(meta.get('seeds', []))}; arms run: {', '.join(meta.get('arms_run', []))}; aborted: {meta.get('aborted') or 'no'}",
          f"* brain restored: {meta.get('restore')}", ""]
    for name in ARM_NAMES:
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
          "| Axis | Z0 | Z0e (real retrieval) | Z0-off (negative control) | " + " | ".join(n for n in ARM_NAMES if n in arms) + " |",
          "|---|---|---|---|" + "---|" * len([n for n in ARM_NAMES if n in arms])]
    names = [n for n in ARM_NAMES if n in arms]

    def cell(a: dict, axis: str) -> str:
        s = (a or {}).get(axis)
        if not s or not s.get("n"):
            return "-"
        it = s.get("items") or {}
        return (f"{s['pass']}/{s['n']} ({s['wilson95'][0]:.2f}-{s['wilson95'][1]:.2f})" + (f", {s['skipped']} skipped" if s.get("skipped") else "")
                + (f"; items {it['pass']}/{it['n']}" if it.get("n") else ""))
    for axis in sorted({ax for a in [z0_axes, z0_off] + [arms[n]["axes"] for n in names] for ax in a} | set(z0e_axes or {})):
        L.append(f"| {axis} | {cell(z0_axes, axis)} | {cell(z0e_axes or {}, axis)} | {cell(z0_off, axis)} | " + " | ".join(cell(arms[n]["axes"], axis) for n in names) + " |")
    L += ["", "## Winner clause (floors G0-G3 first; then beats Z0 beyond the Wilson interval on 2 capability axes, worse on none; a tie goes to the maintained candidate)", ""]
    for n, c in decision["compare"].items():
        L.append(f"* {n}: " + "; ".join(f"{k}={'beats' if v['beats'] else ('WORSE' if v['worse'] else ('no data' if v.get('note') == 'no data' else 'tie') if v['built'] else 'not built')}"
                                        for k, v in c.items()))
    L += ["", "Floors beside the contest (never inside it): " + "; ".join(
        f"{n}: " + ", ".join(f"{k}={'beats' if v['beats'] else ('WORSE' if v['worse'] else ('no data' if v.get('note') == 'no data' else 'tie'))}" for k, v in (decision.get('floors', {}).get(n) or {}).items())
        for n in decision["compare"])]
    L += ["", "## What this run did not verify", ""] + [f"* {n}" for n in notes] + [""]
    return "\n".join(L)
