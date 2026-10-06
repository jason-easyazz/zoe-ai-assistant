"""Zoe Memory Bench: the runner's contract - exit codes, the artifact on EVERY exit path, the refusal when a
control stays green, hard invariants, partial runs, the baseline rules.

The memory system is replaced by a scripted arm and the control pass by a scripted report, so this file
needs no service module and runs in the slim ``-m ci_safe`` lane. The REAL controls (the lab driver over the
real MemoryService, and the refusal when a control genuinely stays green) are proven in
``services/zoe-data/tests/test_zmb_lab.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import artifact, cells as cellmod, runner, spec, world  # noqa: E402
from zmb.arms.base import Arm  # noqa: E402


class _Closable(Arm):
    name = "scripted"

    def reset(self, u): ...
    def ingest(self, t): ...
    def recall(self, q, k=10): ...
    def forget(self, e): ...
    def as_of(self, q, ts): ...
    def stats(self): ...


@pytest.fixture
def bench(monkeypatch, tmp_path):
    """Run ``runner.main`` with a scripted arm. ``state['verdicts']`` maps cell id -> verdict (default: PASS for
    a store cell that expects PASS, FAIL for a target, SKIP for a brain-tier cell); ``state['control']``
    is the scripted control report."""
    state = {"verdicts": {}, "controls": None, "measured": 0, "raise": None, "built": []}

    def control_pass(chosen, w, off, log=None):
        if state["controls"] is not None:
            return state["controls"](chosen, off)
        todo = [c for c in chosen if c.controls and set(c.controls) <= off and c.expected == "PASS"
                and c.tier == "store"]
        rows = [runner._row(c, cellmod.Outcome("FAIL", "write")) for c in todo]
        return {"off": sorted(off), "checked": len(todo), "red": len(todo), "green": [], "not_run": [],
                "ok": True, "rows": rows}

    def run_cells(chosen, w, arm, log=None):
        state["measured"] += 1
        if state["raise"]:
            raise state["raise"]
        rows = []
        for c in chosen:
            v = state["verdicts"].get(c.id) or ("SKIP" if c.tier == "full" else
                                                 ("FAIL" if c.expected == "FAIL" else "PASS"))
            rows.append(runner._row(c, cellmod.Outcome(v, "write" if v == "FAIL" else "", {"probes": []},
                                                       "brain tier" if v == "SKIP" else "", 0.01)))
        return rows

    def make_arm(name):
        state["built"].append(name)
        return _Closable()

    monkeypatch.setattr(runner, "control_pass", control_pass)
    monkeypatch.setattr(runner, "run_cells", run_cells)
    monkeypatch.setattr(runner, "make_arm", make_arm)
    monkeypatch.setattr(runner, "revision", lambda: {"commit": "c0ffee", "dirty": False})
    paths = {"results": tmp_path / "r.json", "trend": tmp_path / "t.jsonl", "baseline": tmp_path / "b.json"}
    state["paths"] = paths
    state["argv"] = ["--results", str(paths["results"]), "--trend", str(paths["trend"]),
                     "--baseline", str(paths["baseline"])]

    def go(*extra):
        return runner.main(list(extra) + state["argv"])

    state["go"] = go
    state["art"] = lambda: json.loads(paths["results"].read_text())
    return state


def test_a_clean_run_is_ok_and_the_artifact_has_the_contract_fields(bench):
    assert bench["go"]() == 0
    a = bench["art"]()
    assert a["status"] == "ok" and a["run_kind"] == "measure" and a["partial"] is False
    assert a["arm"] == "Z0" and a["tier"] == "store" and a["corpus_seed"] == world.BASELINE_SEED
    assert a["held_out"] is False and a["spec_digest"] and a["revision"] == {"commit": "c0ffee", "dirty": False}
    assert a["instrument"]["ok"] and a["instrument"]["checked"] == 121
    assert a["instrument"]["lab_controls_red"] == f"{a['instrument']['red']}/{a['instrument']['checked']}"
    assert a["teardown"]["proven"] is True and a["hard_violations"] == []
    for c in a["cells"]:                                  # per-cell duration and brain turns, always
        assert {"id", "axis", "verdict", "stage", "expected", "controls", "duration_s", "brain_turns",
                "evidence"} <= set(c)
    auth = a["axes"]["authority"]
    # 66 + the A3 / A8 cells; the scripted arm fails the three known-failing provenance targets, on purpose
    assert (auth["pass"], auth["n"]) == (70, 73) and auth["claimable"] and auth["wilson95"][0] > 0.85
    assert auth["targets_failing"] == ["A3.nightly_digest_rows", "A3.taught_rows", "A3.user_turn_rows_rate"]
    for axis in ("temporal", "recall", "poisoning"):          # every axis of the bench has cells now
        assert a["axes"][axis]["cells"] > 0 and a["axes"][axis]["claimable"], axis
    assert set(a["axes"]) == set(spec.AXES.values())
    trend = [json.loads(x) for x in bench["paths"]["trend"].read_text().splitlines()]
    assert trend[-1]["status"] == "ok" and trend[-1]["axes"]["authority"] == [70, 73]
    assert trend[-1]["axes"]["recall"] == [4, 4] and trend[-1]["axes"]["temporal"] == [10, 12]
    assert trend[-1]["axes"]["poisoning"] == [2, 6]


def test_the_artifact_never_carries_household_text(bench):
    bench["go"]()
    for seed in ("baseline", "fresh", "another"):
        assert bench["go"]("--seed", seed) == 0
        a = bench["art"]()
        names = world.make_world(a["corpus_seed"]).all_strings()
        assert artifact.household_strings_in(a, names) == []
        assert artifact.household_strings_in(a, world.pool_strings()) == []   # not even a pool name


def test_a_control_that_stays_green_refuses_the_run_and_nothing_is_measured(bench):
    green = ["A1.digest.home", "A1.mcp.pet"]
    bench["controls"] = lambda chosen, off: {"off": sorted(off), "checked": 98, "red": 96, "green": green,
                                             "not_run": [], "ok": False, "rows": []}
    assert bench["go"]() == 2
    a = bench["art"]()                                    # a refusal still leaves an artifact
    assert a["status"] == "error" and "instrument not instrumented" in a["refusal"]
    assert "A1.digest.home" in a["refusal"] and a["axes"] is None and a["cells"] == []
    assert a["instrument"]["ok"] is False and a["instrument"]["green"] == green
    assert bench["measured"] == 0 and bench["built"] == []     # the arm under test was never even built
    trend = [json.loads(x) for x in bench["paths"]["trend"].read_text().splitlines()]
    assert trend[-1]["status"] == "error" and trend[-1]["instrument_ok"] is False   # a refusal is on the trend too


def test_a_control_cell_that_could_not_run_is_not_proof_either(bench):
    bench["controls"] = lambda chosen, off: {"off": sorted(off), "checked": 5, "red": 4, "green": [],
                                             "not_run": ["A1.digest.home"], "ok": False, "rows": []}
    assert bench["go"]() == 2 and bench["art"]()["status"] == "error" and bench["measured"] == 0


def test_a_hard_invariant_failure_is_red_with_no_baseline_at_all(bench):
    bench["verdicts"] = {"A1.digest.home": "FAIL"}
    assert bench["go"]() == 1
    a = bench["art"]()
    assert a["status"] == "regression" and a["hard_violations"] == ["A1.digest.home"]
    assert a["axes"]["authority"]["hard_violations"] == ["A1.digest.home"]
    assert a["compare"] is None                                     # no baseline was involved
    bench["verdicts"] = {"A1.digest.home": "ERROR"}                 # a hard cell that errors is not a clean bill
    assert bench["go"]() == 1


def test_a_graded_axis_failure_is_not_a_hard_violation_and_a_target_is_never_red(bench):
    bench["verdicts"] = {"B1.date_day_first": "FAIL"}               # extraction is graded, not an invariant
    assert bench["go"]() == 0 and bench["art"]()["hard_violations"] == []
    a = bench["art"]()
    assert a["axes"]["extraction"]["targets_failing"] == []   # the known target, tracked
    assert a["status"] == "ok"


def test_partial_runs_report_partial_and_never_record(bench):
    assert bench["go"]("--axis", "identity") == 0 and bench["art"]()["status"] == "partial"
    assert bench["art"]()["partial"] is True and bench["art"]()["selection"]["axis"] == "identity"
    assert bench["go"]("--only", "A1.digest.home") == 0 and bench["art"]()["status"] == "partial"
    assert len(bench["art"]()["cells"]) == 1
    assert bench["go"]("--tier", "full") == 0                       # brain-tier cells cannot run in the lab
    a = bench["art"]()
    assert a["status"] == "partial" and a["tier"] == "full"
    assert all(c["verdict"] == "SKIP" and c["reason"] for c in a["cells"] if c["tier"] == "full")
    assert not bench["paths"]["baseline"].exists()


def test_a_run_that_measured_nothing_is_skip_not_ok(bench):
    bench["verdicts"] = {c.id: "SKIP" for c in spec.load_cells()}
    assert bench["go"]("--arm", "H1") == 0
    assert bench["art"]()["status"] == "skip" and bench["built"] == ["H1"]
    assert all(not s["claimable"] for s in bench["art"]()["axes"].values())


@pytest.mark.parametrize("exc", [RuntimeError("arm blew up"), type("LiveStoreViolation", (BaseException,), {})("trip")])
def test_a_crash_or_a_guard_trip_still_leaves_an_artifact_and_no_baseline(bench, exc):
    bench["raise"] = exc
    assert bench["go"]("--record-baseline") == 2
    a = bench["art"]()
    assert a["status"] == "error" and a["run_error"] and not bench["paths"]["baseline"].exists()
    assert bench["paths"]["trend"].exists()


def test_every_exit_path_writes_an_artifact(bench):
    for argv, rc in ((["--tier", "store"], 0), (["--axis", "forgetting"], 0), (["--control", "off"], 0)):
        bench["paths"]["results"].unlink(missing_ok=True)
        assert bench["go"](*argv) == rc and bench["paths"]["results"].exists(), argv
    bench["paths"]["results"].unlink(missing_ok=True)
    assert bench["go"]("--compare-baseline") == 2 and bench["paths"]["results"].exists()   # refused: no baseline


def test_record_then_compare_regress_and_the_refusals(bench):
    assert bench["go"]("--compare-baseline") == 2                    # no baseline: REFUSE, never compare to nothing
    assert "needs a valid baseline" in bench["art"]()["refusal"] and bench["measured"] == 0
    assert bench["go"]("--record-baseline") == 0
    base = json.loads(bench["paths"]["baseline"].read_text())
    assert base["corpus_seed"] == world.BASELINE_SEED and base["cells"]["A1.digest.home"] == "PASS"
    assert base["arm"] == "Z0" and base["spec_digest"] and base["scorer_version"]
    assert bench["go"]("--compare-baseline") == 0 and bench["art"]()["compare"]["regressions"] == []
    bench["verdicts"] = {"B1.date_day_first": "FAIL"}                # a previously PASSING graded cell regresses
    assert bench["go"]("--compare-baseline") == 1
    a = bench["art"]()
    assert a["status"] == "regression" and a["compare"]["regressions"] == ["B1.date_day_first"]
    assert a["hard_violations"] == []                                # red because of the baseline, not an invariant
    # a held-out world sits BESIDE the baseline: not comparable, never red
    assert bench["go"]("--compare-baseline", "--seed", "fresh") == 0
    c = bench["art"]()["compare"]
    assert c["comparable"] is False and c["regressions"] == [] and c["notes"]
    assert json.loads(bench["paths"]["baseline"].read_text()) == base      # no compare ever rewrote the bar


@pytest.mark.parametrize("flags", [
    ["--record-baseline", "--only", "A1.digest.home"],
    ["--record-baseline", "--axis", "authority"],
    ["--record-baseline", "--seed", "fresh"],
    ["--record-baseline", "--arm", "H1"],
    ["--record-baseline", "--control", "off"],
    ["--compare-baseline", "--control", "off"],
    ["--record-baseline", "--compare-baseline"],
])
def test_baseline_flag_combinations_that_must_be_refused(bench, flags):
    with pytest.raises(SystemExit) as ei:
        bench["go"](*flags)
    assert ei.value.code == 2 and bench["measured"] == 0 and not bench["paths"]["baseline"].exists()


@pytest.mark.parametrize("flags", [
    ["--only", "A1.nope"], ["--only", ""], ["--axis", "nonsense"], ["--arm", "mem0"],
    ["--control", "nonsense"], ["--tier", "weird"], ["--only", "A1.digest.home", "--axis", "identity"],
])
def test_unknown_ids_axes_arms_and_controls_exit_2_before_anything_runs(bench, flags):
    with pytest.raises(SystemExit) as ei:
        bench["go"](*flags)
    assert ei.value.code == 2 and bench["measured"] == 0


def test_control_off_runs_only_the_instrument_and_reports_it(bench):
    assert bench["go"]("--control", "off") == 0
    a = bench["art"]()
    assert a["run_kind"] == "control" and a["status"] == "ok" and a["axes"] is None
    assert bench["measured"] == 0 and bench["built"] == []           # the arm under test is not involved
    assert a["instrument"]["controls_off"] == sorted(runner.CONTROLS)
    assert all(c["verdict"] == "FAIL" for c in a["cells"])          # every control cell is RED: that is the report
    bench["controls"] = lambda chosen, off: {"off": sorted(off), "checked": 3, "red": 2, "green": ["X"],
                                             "not_run": [], "ok": False, "rows": []}
    assert bench["go"]("--control", "authority") == 2 and bench["art"]()["status"] == "error"
    assert bench["go"]("--arm", "Z0-off") == 2                       # the negative-control arm IS the control pass


def test_z0_off_arm_is_the_control_run(bench):
    assert bench["go"]("--arm", "Z0-off") == 0
    a = bench["art"]()
    assert a["run_kind"] == "control" and a["arm"] == "Z0-off" and a["instrument"]["controls_off"] == sorted(runner.CONTROLS)


def test_dry_run_and_list_touch_nothing(bench, capsys):
    assert bench["go"]("--dry-run") == 0
    out = capsys.readouterr().out
    assert "plan (no I/O" in out and "(a) authority" in out and "controlled" in out and "authority:" in out
    assert not bench["paths"]["results"].exists() and bench["measured"] == 0 and bench["built"] == []
    assert bench["go"]("--list", "--axis", "identity") == 0
    listing = capsys.readouterr().out.splitlines()
    assert listing and all(line.split("\t")[1] == "identity" for line in listing)
    assert any("\tsanity\t" in line for line in listing)
    assert not bench["paths"]["results"].exists()


def test_the_control_pass_runs_for_the_selected_cells_only(bench):
    seen = {}

    def spy(chosen, off):
        seen["ids"] = [c.id for c in chosen]
        return {"off": sorted(off), "checked": 0, "red": 0, "green": [], "not_run": [], "ok": True, "rows": []}
    bench["controls"] = spy
    bench["go"]("--only", "F1.sweep_keeps_the_rest")
    assert seen["ids"] == ["F1.sweep_keeps_the_rest"]


def test_the_plan_text_names_every_control_and_axis():
    text = runner.plan_text(spec.load_cells(), "store", "zmb-v1")
    for control in runner.CONTROLS:
        assert f"{control}:" in text
    for letter, axis in spec.AXES.items():
        assert f"({letter}) {axis}" in text
    assert "no cells yet" not in text                                # every axis of the bench has cells
    for line in text.splitlines():
        if line.startswith(("  (c) temporal", "  (d) recall", "  (i) poisoning")):
            assert "store-tier" in line


def test_a_poisoning_failure_is_a_hard_invariant_but_a_known_target_is_not(bench):
    """G2: 'poisoning (0 canaries persisted or obeyed)'. A passing poisoning cell that starts failing is red with no
    baseline; the four known-failing targets are tracked, never red."""
    assert bench["go"]() == 0 and bench["art"]()["hard_violations"] == []
    assert bench["art"]()["axes"]["poisoning"]["targets_failing"] == [
        "I1.pasted_email_instruction", "I1b.pasted_email_planted_token",
        "I2.third_party_fragment.panel_unverified", "I2.third_party_fragment.third_party"]
    bench["verdicts"] = {"I3.assistant_text_canary": "FAIL"}
    assert bench["go"]() == 1
    assert bench["art"]()["hard_violations"] == ["I3.assistant_text_canary"]
    bench["verdicts"] = {"A3.typed_turn_rows": "FAIL"}                 # provenance is part of authority (G2: A3)
    assert bench["go"]() == 1 and bench["art"]()["hard_violations"] == ["A3.typed_turn_rows"]
    bench["verdicts"] = {"C1.update_typed": "FAIL"}                    # temporal is graded, like extraction
    assert bench["go"]() == 0 and bench["art"]()["hard_violations"] == []
