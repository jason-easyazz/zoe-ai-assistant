"""Zoe Memory Bench: the NIGHT MIND's cells (K2 / K3 made real; K6-K12 new), Z0's brain half of the protocol axis, and the standalone drivers.

Nothing here needs a model or a server beyond a loopback fake: the arm is ``Z0n`` (Z0 + the night pass) with the lab's FAKE nightly brain, the scorers are the bench's own
pure ones, and every cell is shown RED when the check it claims is removed (the lab's ``night_*`` controls) - the instrument rule of the bench: break the fix, the cell must
go red; a SKIP or a timeout is never a pass.

The bench's finding that made K6 necessary (docs/research/night-mind-2026-10-09.md section 7.1): an ECHO arm - observations = the owner's own turns, unchanged - passes K1 (10/10),
K2 (4/4) and K4, so K2 alone cannot tell reflection from copying. The echo control below passes K2 and fails K6.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import artifact, bakeoff_gates as gates, cells as cellmod, life, runner, scorers_cap as cap, spec, world, z0_brain  # noqa: E402
from zmb.arms.base import Arm, IngestReport, Turn  # noqa: E402
from zmb.arms.z0 import Z0Arm  # noqa: E402
from zmb.night_brain import FakeNightBrain, FakeNightServer  # noqa: E402

CELLS = {c.id: c for c in spec.load_cells()}
K = [c for c in CELLS.values() if c.axis == "reflection"]
W = world.make_world("zmb-v1")
KIDS = [c.id for c in K]


def run(cid, arm, w=W):
    return cellmod.run_cell(CELLS[cid].rendered(w), w, arm)


@pytest.fixture(scope="module")
def z0n():
    arm = Z0Arm(name="Z0n", night=True)
    yield arm
    arm.close()


# ── the cells on Z0n: every one passes, on every seed ─────────────────────────

def test_every_reflection_cell_passes_on_z0n_with_the_fake_brain(z0n):
    got = {cid: run(cid, z0n) for cid in KIDS}
    assert {cid: o.verdict for cid, o in got.items()} == {cid: "PASS" for cid in KIDS}, {c: (o.verdict, o.reason, o.evidence) for c, o in got.items() if o.verdict != "PASS"}
    assert len(KIDS) == 13 and {"K6.compression", "K7.dense_day_late_plants", "K8.citation_validity", "K9.change_and_quiet", "K9.flat_week", "K10.restraint",
                                 "K11.resolution", "K12.weight_calibration"} <= set(KIDS)


@pytest.mark.parametrize("seed", ["fresh-held-out", "s3", "s4"])
def test_the_cells_hold_on_held_out_seeds(seed):
    w = world.make_world(seed)
    arm = Z0Arm(name="Z0n", night=True)
    try:
        bad = {cid: run(cid, arm, w).verdict for cid in KIDS if run(cid, arm, w).verdict != "PASS"}
    finally:
        arm.close()
    assert bad == {}


def test_k1_carries_its_judged_true_false_counts_on_every_run(z0n):
    """E0: a veto must always be able to say whether it saw false observations or too few."""
    o = run("K1.observations_are_true", z0n)
    j = o.evidence["probes"][0]["observations_judged"]
    assert {"n", "true", "false", "neutral", "observations", "decidable"} <= set(j) and j["false"] == 0 and j["decidable"] >= 3
    thin = cap.score_observations([{"text": "Nothing."}], {"entities": [], "pairs": [], "foreign": [], "stale": [], "claims": [], "threads": [], "hedges": [], "history": [], "stated": []},
                                  kind="true")
    assert thin.verdict == "FAIL" and "observations_judged" in thin.evidence and thin.evidence["observations_judged"]["observations"] == 1       # too few: still counted


def test_k2_and_k3_are_real_cells_now_and_skip_only_where_the_model_is_scripted(z0n):
    assert run("K2.thread_recall", z0n).verdict == "PASS" and run("K3.useful_answers", z0n).verdict == "PASS"
    plain = Z0Arm()
    try:
        for cid in ("K2.thread_recall", "K3.useful_answers", "K6.compression", "K7.dense_day_late_plants", "K8.citation_validity", "K9.change_and_quiet", "K9.flat_week",
                    "K10.restraint", "K11.resolution", "K12.weight_calibration"):
            o = run(cid, plain)
            assert o.verdict == "SKIP" and "scripted" in o.reason, (cid, o.verdict, o.reason)
        assert [run(c, plain).verdict for c in ("K1.observations_are_true", "K4.invalidated_fact_not_restated", "K5.user_stated_is_never_restated_as_inference")] == ["PASS"] * 3
    finally:
        plain.close()


# ── the negative controls: each one turns the cells that rely on it red ───────

#: control -> the cells that MUST be red with it off (and only that control off)
RED = {
    "night_mind": {"K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K9f", "K10", "K11"},
    "night_citations": {"K1", "K5", "K8"},
    "night_chunking": {"K7"},
    "night_echo": {"K6"},
    "night_restraint": {"K10"},
    "night_absence": {"K11"},
    "night_notice": {"K9f"},
    "night_weights": {"K12"},
}


@pytest.mark.parametrize("control", sorted(RED))
def test_each_night_control_turns_its_cells_red(control):
    arm = Z0Arm(off=frozenset({control}), night=True)
    try:
        red = {c.id.split(".")[0] + ("f" if c.id.endswith("flat_week") else "") for c in K if run(c.id, arm).verdict != "PASS"}
    finally:
        arm.close()
    assert RED[control] <= red, (control, sorted(red))


def test_the_echo_control_passes_k2_and_fails_k6_which_is_why_k6_exists():
    arm = Z0Arm(off=frozenset({"night_echo"}), night=True)
    try:
        assert run("K2.thread_recall", arm).verdict == "PASS" and run("K1.observations_are_true", arm).verdict in ("PASS", "FAIL")
        o = run("K6.compression", arm)
        assert o.verdict == "FAIL" and o.evidence["probes"][0]["compression"]["share"] > 0.6
    finally:
        arm.close()


class EchoArm(Arm):
    """The instrument's scripted echo: it 'reflects' by returning the owner's own turns unchanged (the 23 not superseded). Not a memory system - a control."""
    name = "echo"
    nightly_model = "own"
    capabilities = frozenset({"observations", "idle_pass"})

    def __init__(self):
        self.turns: "list[str]" = []

    def reset(self, user_id): self.turns = []
    def ingest(self, turns): self.turns += [t.text for t in turns if t.speaker == "owner_typed"]; return IngestReport()
    def recall(self, query, k=10): return []
    def forget(self, entity): return ""
    def as_of(self, query, ts): return []
    def stats(self): return {"rows": [], "counts": {}}
    def reflect_pass(self): return {}

    def observations(self, query=""):
        return {"items": [{"id": f"e{i}", "text": t, "stated_by": "user"} for i, t in enumerate(self.turns)], "model": "own"}


def test_the_scripted_echo_arm_passes_the_old_cells_and_fails_compression():
    echo = EchoArm()
    k2, k6 = run("K2.thread_recall", echo), run("K6.compression", echo)
    assert k2.verdict == "PASS"                                                    # copying covers every story
    assert k6.verdict == "FAIL" and k6.evidence["probes"][0]["compression"]["share"] > 0.9


# ── the new scorers, on their own ─────────────────────────────────────────────

LF = life.life("zmb-v1")
GOLD = life.gold_for_scoring(LF)


def test_compression_needs_a_reflection_not_a_copy_and_not_nothing():
    turns = [t["text"] for t in LF.turns if t["speaker"] == "typed"]
    assert cap.score_compression([{"text": t} for t in turns], GOLD, n_turns=len(turns)).verdict == "FAIL"             # a copy
    assert cap.score_compression([], GOLD, n_turns=len(turns)).verdict == "FAIL"                                          # nothing
    one_each = [{"text": f"{t.identity[0]} {t.key}"} for t in LF.threads]
    assert cap.score_compression(one_each, GOLD, n_turns=len(turns)).verdict == "PASS"


def test_citations_demand_a_real_turn_and_an_exact_span():
    turns = {"t1": "Tamsin got the offer from Pinecrest Mills!", "t2": "My knee is so much better after the physio."}
    items = [{"text": "Tamsin got the offer from Pinecrest Mills!", "turn_id": "t1"}, {"text": "my knee is so much better", "turn_id": "t2"}, {"text": "The knee is fine.", "turn_id": "t2"}]
    assert cap.score_citations(items[:2], turns.get, min_observations=2).verdict == "PASS"
    assert cap.score_citations(items, turns.get, min_observations=2).verdict == "FAIL"                                  # not a span of the turn it points at
    assert cap.score_citations([{"text": "x y z", "turn_id": "ghost"}], turns.get, min_observations=1).verdict == "FAIL"   # a turn that does not exist
    assert cap.score_citations([], turns.get).verdict == "FAIL"


def test_the_weight_correlation_and_accuracy_bars():
    gold = life.labelled_moments()
    good = [{"quote": g["text"], "kind": g["kind"], "feeling": g["feeling"], "weight": g["weight"]} for g in gold]
    assert cap.score_weights(good, gold).verdict == "PASS"
    flat = [{**m, "weight": 2} for m in good]
    assert cap.score_weights(flat, gold).verdict == "FAIL"                                                                # a constant weight has no rank correlation
    wrong = [{**m, "kind": "other", "feeling": "none"} for m in good]
    assert cap.score_weights(wrong, gold).verdict == "FAIL"
    assert cap.score_weights(good[:3], gold).verdict == "FAIL"                                                            # it picked too few of the labelled moments
    assert abs(cap.spearman([1, 2, 3, 4], [1, 2, 3, 4]) - 1.0) < 1e-9 and cap.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)


def test_the_dense_layout_plants_two_stories_late_and_pads_the_day_with_commands():
    items, late = life.dense_layout("zmb-v1", 40)
    assert late == ["job", "move"] and sum(1 for i in items if i.kind == "cmd") == 1200 and sum(1 for i in items if i.kind == "life") == 24
    by_day = {}
    for i in items:
        by_day.setdefault(i.day, []).append(i)
    job_name = LF.threads[0].identity[0]
    for day, its in by_day.items():
        for pos, it in enumerate(its):
            if it.kind == "life" and job_name in it.text.lower():
                assert pos / len(its) >= 0.85, (day, pos, len(its))                                                       # planted in the last 15% of its day


# ── the instrument as a whole ─────────────────────────────────────────────────

def test_the_control_pass_proves_the_night_cells_on_z0n_and_the_rest_on_z0():
    cp = runner.control_pass(list(CELLS.values()), W, frozenset(runner.CONTROLS))
    assert cp["ok"] and cp["green"] == [] and cp["not_run"] == []
    night_ids = {c.id for c in CELLS.values() if cellmod.uses_night(c) and c.controls}
    assert night_ids >= {"K1.observations_are_true", "K2.thread_recall", "K3.useful_answers", "K6.compression", "K12.weight_calibration"}
    assert night_ids <= {r["id"] for r in cp["rows"]}


def test_every_new_control_is_named_by_a_cell_and_listed():
    for ctl in RED:
        assert ctl in runner.CONTROLS and any(ctl in c.controls for c in CELLS.values()), ctl


def test_the_arm_registry_has_z0n_and_a_model_url_is_loopback_only(capsys):
    from zmb.arms import ARMS, make_arm
    assert "Z0n" in ARMS
    arm = make_arm("Z0n")
    try:
        assert arm.nightly_model == "own" and arm.takes_lies
    finally:
        arm.close()
    with pytest.raises(ValueError):
        from zmb.arms.mpa_model import loopback
        loopback("http://10.1.2.3:11500/v1")


# ── Z0's brain half of the protocol axis (M4.*.zoe): rule M stops reading "no data" ──

def test_z0_gets_a_protocol_brain_baseline_and_the_recall_floor_is_what_fires_it():
    arm = Z0Arm()
    try:
        on = z0_brain.run_z0_protocol_brain(arm, z0_brain.ScriptedZoeBrain(), "zmb-v1")
        off = z0_brain.run_z0_protocol_brain(arm, z0_brain.ScriptedZoeBrain(), "zmb-v1", use_floor=False)         # control: the floor lifted, the scripted brain never calls the tool
    finally:
        arm.close()
    by = {c["id"]: c["verdict"] for c in on["cells"]}
    assert set(by) == {f"M4.{m}.zoe" for m in z0_brain.METRICS}
    assert by["M4.fire_when_needed.zoe"] == "PASS" and by["M4.quiet_when_not_needed.zoe"] == "PASS" and on["brain"]["floor"] == 14
    assert {c["id"]: c["verdict"] for c in off["cells"]}["M4.fire_when_needed.zoe"] == "FAIL" and off["brain"]["floor"] == 0
    eager = Z0Arm()
    try:
        both = z0_brain.run_z0_protocol_brain(eager, z0_brain.ScriptedZoeBrain(eager=True), "zmb-v1", use_floor=False)
    finally:
        eager.close()
    assert {c["id"]: c["verdict"] for c in both["cells"]}["M4.fire_when_needed.zoe"] == "PASS" and both["brain"]["tool"] >= 14            # the tool path fires it too


def test_the_derived_protocol_brain_axis_exists_for_z0_once_its_rows_are_in_the_seed_run():
    arm = Z0Arm()
    try:
        res = z0_brain.run_z0_protocol_brain(arm, z0_brain.ScriptedZoeBrain(), "zmb-v1")
    finally:
        arm.close()
    rows = [{"id": c["id"], "axis": "protocol", "tier": "full", "verdict": c["verdict"], "stage": "", "expected": "PASS", "controls": [], "sanity": False, "duration_s": 0.0,
             "brain_turns": 0, "reason": "", "lme_map": None} for c in res["cells"]]
    axes = gates.aggregate_axes({"zmb-v1": {"axes": {}, "cells": rows}})
    assert "protocol_brain" in axes and axes["protocol_brain"]["n"] == 4 and axes["protocol_brain"]["pass"] >= 2
    assert "protocol_brain" not in gates.aggregate_axes({"zmb-v1": {"axes": {}, "cells": []}})                    # before this: Z0 had none, and M read "no data"


# ── the standalone drivers ────────────────────────────────────────────────────

def _run_py(args, **kw):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "PYTHONPATH", "TZ", "LANG")}
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, timeout=240, cwd=str(REPO), **kw)


def test_the_window_driver_in_lab_mode_scores_k_and_the_protocol_brain(tmp_path):
    out = tmp_path / "z0n.json"
    r = _run_py(["scripts/perf/zmb/z0n_window.py", "--lab", "--out", str(out)])
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(out.read_text())
    assert d["mode"] == "lab" and d["k_summary"]["fail"] == [] and d["k_summary"]["pass"] == 13 and d["k1"]["false"] == 0 and d["k1"]["judged"] >= 3
    assert {c["id"] for c in d["protocol_brain"]["cells"]} == {f"M4.{m}.zoe" for m in z0_brain.METRICS}
    assert d["calls"] > 20 and artifact.household_strings_in(d, W.all_strings()) == []                                  # counts and ids only


def test_the_smoke_budget_is_a_hard_cap_enforced_in_code(tmp_path):
    srv = FakeNightServer(FakeNightBrain())
    try:
        out = tmp_path / "smoke.json"
        r = _run_py(["scripts/perf/zmb/z0n_window.py", "--clone-url", srv.url, "--smoke-live", "--max-calls", "7", "--out", str(out)])
        assert r.returncode == 0, r.stderr[-800:]
        d = json.loads(out.read_text())
        assert d["calls"] == 7 and "budget" in d.get("stopped", "") and srv.requests >= 7
    finally:
        srv.close()


def _load_cli():
    spec_ = importlib.util.spec_from_file_location("zoe_night_mind_cli", REPO / "scripts" / "maintenance" / "zoe-night-mind.py")
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    return mod


def test_the_cli_contract_flags_and_summary_shape(tmp_path):
    srv = FakeNightServer(FakeNightBrain())
    turns = [{"id": f"t{i}", "text": t["text"], "at": f"2026-10-0{1 + i % 8}T01:00:00+00:00"} for i, t in enumerate(x for x in LF.turns if x["speaker"] == "typed")]
    f = tmp_path / "turns.json"
    f.write_text(json.dumps(turns))
    try:
        base = ["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "16384", "--user", "demo_bar_00000001", "--transcript-file", str(f)]
        dry = _run_py(base + ["--dry-run"])
        assert dry.returncode == 0, dry.stderr[-800:]
        d = json.loads(dry.stdout)
        assert d["status"] == "ok" and d["mode"] == "shadow" and d["dry_run"] is True and d["ctx_tokens"] == 16384 and d["chunk_budget"] == 4800 and d["max_calls"] == 7
        (m,) = d["members"]
        assert m["user_id"] == "demo_bar_00000001" and m["written"] == 0 and m["observations_written"] >= 5 and m["calls"] >= 2 and m["moments_verified"] >= 5
        assert {"calls", "prompt_tokens", "completion_tokens", "wall_s", "observations_written", "observations_pending", "completion_tokens_per_wall_s", "members"} <= set(d["totals"])
        live = json.loads(_run_py(base).stdout)
        assert live["mode"] == "enforce" and live["members"][0]["written"] >= 5
        assert "Tamsin" not in dry.stdout and "knee" not in dry.stdout                                                       # counts and ids only, never the owner's words
        body = srv.last_body
        assert body["max_tokens"] > 0 and body["model"]
    finally:
        srv.close()


def test_the_cli_writes_nothing_and_exits_2_when_the_model_is_unreachable(tmp_path):
    srv = FakeNightServer(FakeNightBrain())
    srv.up = False
    f = tmp_path / "turns.json"
    f.write_text(json.dumps([{"id": "t1", "text": "Tamsin got the offer from Pinecrest Mills and I am so glad for her!", "at": "2026-10-08T01:00:00+00:00"}] * 3))
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--user", "demo_bar_00000001", "--transcript-file", str(f)])
        assert r.returncode == 2
        d = json.loads(r.stdout)
        assert d["status"] == "llm_unreachable" and d["members"] == [] and d["totals"]["calls"] == 0
    finally:
        srv.close()
    gone = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", "http://127.0.0.1:1/v1", "--user", "demo_bar_00000001", "--transcript-file", str(f)])
    assert gone.returncode == 2 and json.loads(gone.stdout)["status"] == "llm_unreachable"


def test_the_cli_refuses_a_remote_model_and_a_run_with_no_target(tmp_path):
    r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", "http://203.0.113.9:11500/v1", "--user", "demo_bar_00000001"])
    assert r.returncode != 0 and "non-loopback" in (r.stderr + r.stdout)
    r2 = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", "http://127.0.0.1:1/v1"])
    assert r2.returncode != 0 and "required" in (r2.stderr + r2.stdout)


def test_the_cli_can_score_the_k_cells_against_a_server(tmp_path):
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells"])
        assert r.returncode == 0, r.stderr[-800:]
        d = json.loads(r.stdout)
        c = d["cells"]
        assert c["error"] == 0 and c["pass"] + c["fail"] + c["skip"] == 13 and c["k1"]["judged"] is not None
        assert d["totals"]["members"] == 0
    finally:
        srv.close()


# ── the window phase (flag-dark): Z0n's cells and Z0's protocol_brain rows reach the report's structures ──────────

def _phase_ctx(on=True, left_s=10_000.0, result=None):
    import types
    from zmb import bakeoff_measure as measure
    win = types.SimpleNamespace(time_left_s=lambda: left_s, guard=lambda: None, z0n_runner=(lambda ctx, seed, box: result), run_id="r1")
    ctx = types.SimpleNamespace(cfg=types.SimpleNamespace(z0n=on), win=win, label=lambda *_a: None, host=types.SimpleNamespace(mono=lambda: 0.0), log=lambda *_a: None,
                                notes=[], z0={"zmb-v1": {"cells": [], "instrument": {"ok": True}}}, z0n={})
    return measure, ctx


def test_the_z0n_phase_is_off_by_default_and_fills_the_baseline_when_it_runs(tmp_path):
    out = tmp_path / "lab.json"
    assert _run_py(["scripts/perf/zmb/z0n_window.py", "--lab", "--out", str(out)]).returncode == 0
    res = json.loads(out.read_text())
    measure, ctx = _phase_ctx(on=False, result=res)
    measure.phase_z0n(ctx, "zmb-v1", [], CELLS)
    assert ctx.z0n == {} and ctx.z0["zmb-v1"]["cells"] == []                                  # off: nothing touched
    measure, ctx = _phase_ctx(on=True, result=res)
    measure.phase_z0n(ctx, "zmb-v1", [], CELLS)
    assert ctx.z0n["zmb-v1"]["k1"]["false"] == 0 and ctx.z0n["zmb-v1"]["axes"]["reflection"]["pass"] == 13
    ids = {c["id"] for c in ctx.z0["zmb-v1"]["cells"]}
    assert ids == {f"M4.{m}.zoe" for m in z0_brain.METRICS}
    axes = gates.aggregate_axes(ctx.z0)
    assert axes["protocol_brain"]["n"] == 4                                                   # rule M now has a Z0 baseline


def test_the_z0n_phase_skips_with_a_reason_when_there_is_no_time_or_no_result():
    measure, ctx = _phase_ctx(on=True, left_s=60.0, result={})
    measure.phase_z0n(ctx, "zmb-v1", [], CELLS)
    assert ctx.z0n == {} and any("skipped" in n and "min left" in n for n in ctx.notes)
    measure, ctx = _phase_ctx(on=True, result={"error": "boom"})
    measure.phase_z0n(ctx, "zmb-v1", [], CELLS)
    assert ctx.z0n == {} and any("did not produce a result: boom" in n for n in ctx.notes)
