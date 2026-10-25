"""T-4 (deep dive 2, 2026-10-19): the Samantha benches get COMPLETE, CLEAN, commit-bound baselines.

The bar's baseline held S1-S8, so 15 scenarios could not regress (a cell missing from the baseline was vacuously
"not regressed"), and the day-sim and person bench had no baseline at all. Pinned here, with no network:

  * a baseline is recorded only from a full, ok run on a clean verified tree, and names its commit;
  * a comparison REFUSES (before spending a run) when the baseline lacks a cell the run scores;
  * only a cell that PASSED in the baseline can regress, and SKIP / ERROR / INCONCLUSIVE after a PASS do;
  * the day-sim and the person bench now record and compare, with the same rules.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

PERF = Path(__file__).resolve().parents[2] / "scripts" / "perf"
sys.path.insert(0, str(PERF))
import bench_baseline as bb  # noqa: E402
import samantha_bar as sb  # noqa: E402
import samantha_day_sim as ds  # noqa: E402
import samantha_person as sp  # noqa: E402

class _FakeStack:
    """P-2: the run scratch stack, faked (the real one is docker + a second zoe-data; tests/unit/test_harness_scratch.py)."""
    token, dsn, base_url, tag = "t", "postgresql://scratch:x@127.0.0.1:1/scratch", "http://127.0.0.1:1", "fake"

    def public(self):
        return {"tag": "fake", "ram": {"total_mb": 1.0}}


CLEAN = {"commit": "c0ffee" * 6 + "abcd", "tree": "7ree", "dirty": False, "clean_verified": True}


# ── the shared rules ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("rev,status,partial,verdicts,needle", [
    (CLEAN, "ok", True, {"A": "PASS", "B": "PASS"}, "partial"),
    (CLEAN, "error", False, {"A": "PASS", "B": "PASS"}, "status"),
    (None, "ok", False, {"A": "PASS", "B": "PASS"}, "unknown"),
    (dict(CLEAN, clean_verified=False), "ok", False, {"A": "PASS", "B": "PASS"}, "could not be verified"),
    (dict(CLEAN, dirty=True), "ok", False, {"A": "PASS", "B": "PASS"}, "DIRTY"),
    (CLEAN, "ok", False, {"A": "PASS"}, "missing B"),
])
def test_record_problem(rev, status, partial, verdicts, needle):
    why = bb.record_problem(rev, status=status, partial=partial, verdicts=verdicts, cells=("A", "B"))
    assert why and needle in why


def test_record_problem_accepts_a_full_clean_run():
    assert bb.record_problem(CLEAN, status="ok", partial=False, verdicts={"A": "FAIL", "B": "SKIP"},
                             cells=("A", "B")) is None


def test_compare_refuses_missing_cells_and_only_a_pass_can_regress():
    base = bb.make("h", "0.1", {"A": "PASS", "B": "FAIL", "C": "PASS"}, CLEAN)
    assert base["commit"] == CLEAN["commit"] and base["revision"] == CLEAN
    cmp = bb.compare({"A": "SKIP", "B": "PASS", "C": "PASS", "D": "PASS"}, base, ("A", "B", "C", "D"))
    assert cmp["regressions"] == ["A"] and cmp["improvements"] == ["B"]
    assert cmp["missing_in_baseline"] == ["D"] and cmp["refused"] and cmp["red"]
    assert not bb.compare({"A": "PASS", "C": "PASS", "B": "FAIL"}, base, ("A", "B", "C"))["red"]


def test_load_names_each_problem(tmp_path):
    p = tmp_path / "b.json"
    assert "no baseline" in bb.load(p, ("PASS",))[1]
    p.write_text("{x")
    assert "unreadable" in bb.load(p, ("PASS",))[1]
    p.write_text(json.dumps({"cells": {}}))
    assert "no 'cells'" in bb.load(p, ("PASS",))[1]
    p.write_text(json.dumps({"cells": {"A": "MAYBE"}}))
    assert "unrecognised" in bb.load(p, ("PASS", "FAIL"))[1]
    bb.write(p, {"cells": {"A": "PASS"}})
    assert bb.load(p, ("PASS",)) == ({"cells": {"A": "PASS"}}, None)


# ── the bar: a partial baseline can no longer judge a full run ───────────────
def _bar_gates(monkeypatch, tmp_path, rev=CLEAN):
    monkeypatch.setattr(sb, "scratch_stack_for", lambda service_dir, name, ctx, log: (_FakeStack(), None))
    monkeypatch.setenv("ZOE_PERF", "1")
    for name, fn in (("_acquire_lock", lambda: None), ("in_nightly_window", lambda now: False),
                     ("deploy_in_progress", lambda: (False, "0")), ("_readyz", lambda: (True, "ok")),
                     ("mem_available_mb", lambda: 4096), ("service_revision", lambda d: rev),
                     ("env_file_value", lambda d, k: "x")):
        monkeypatch.setattr(sb, name, fn)
    monkeypatch.setattr(sb.Live, "forget_ok", lambda self: True)
    monkeypatch.setattr(sb.os, "nice", lambda n: 5)
    calls = []
    monkeypatch.setattr(sb, "run_scenarios", lambda *a, **k: calls.append("run") or [
        {"id": s, "verdict": "PASS", "evidence": {}} for s in sb.SCENARIO_IDS])
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    return calls, ["--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
                   "--baseline", str(tmp_path / "b.json"), "--pending", str(tmp_path / "p.json")]


def test_bar_compare_refuses_the_s1_to_s8_baseline_before_running(monkeypatch, tmp_path):
    """The live baseline shape (2026-09-30): S1-S8 only. 21 of the 29 scored cells could never go red."""
    calls, args = _bar_gates(monkeypatch, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({"scenarios": {f"S{i}": "PASS" for i in range(1, 9)},
                                                 "samples": 3, "judge_prompt_sha256": sb.JUDGE_PROMPT_SHA256}))
    assert sb.main(args + ["--compare-baseline"]) == 2 and calls == []
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "refused" and "lacks 21 of the 29" in res["reason"] and "S33" in res["reason"]


def test_bar_compare_baseline_is_red_on_unmeasured_cells_even_if_called_directly():
    cmp = sb.compare_baseline({"S1": "PASS", "S2": "PASS"}, {"scenarios": {"S1": "PASS"}})
    assert cmp["red"] and cmp["new"] == ["S2"] and any("lacks S2" in n for n in cmp["notes"])


def test_bar_record_refuses_a_dirty_tree_before_running(monkeypatch, tmp_path):
    calls, args = _bar_gates(monkeypatch, tmp_path, rev=dict(CLEAN, dirty=True))
    assert sb.main(args + ["--record-baseline", "--samples", "3"]) == 2 and calls == []
    assert not (tmp_path / "b.json").exists()
    assert "DIRTY" in json.loads((tmp_path / "r.json").read_text())["reason"]


def test_bar_records_every_scenario_with_its_commit(monkeypatch, tmp_path):
    calls, args = _bar_gates(monkeypatch, tmp_path)
    assert sb.main(args + ["--record-baseline", "--samples", "3"]) == 0 and calls == ["run"]
    base = json.loads((tmp_path / "b.json").read_text())
    assert set(base["scenarios"]) == set(sb.SCENARIO_IDS) and base["commit"] == CLEAN["commit"]
    assert base["samples"] == 3
    # ...and that baseline judges the next full run
    assert sb.main(args + ["--compare-baseline"]) == 0


# ── the day-sim ──────────────────────────────────────────────────────────────
def _day_gates(monkeypatch, tmp_path, verdicts, rev=CLEAN):
    monkeypatch.setattr(sb, "scratch_stack_for", lambda service_dir, name, ctx, log: (_FakeStack(), None))
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sb, "service_revision", lambda d: rev)
    monkeypatch.setattr(sb, "_acquire_lock", lambda: None)
    monkeypatch.setattr(sb, "load_source_fallback_markers", lambda d: [])
    monkeypatch.setattr(sb.os, "nice", lambda n: 5)
    monkeypatch.setattr(ds.uma, "_gates", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_pending", lambda *a, **k: None)
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    calls = []

    def week(*a, **k):
        calls.append("run")
        return {"asks": [{"id": a_, "verdict": verdicts.get(a_, "PASS"), "title": a_} for a_ in ds.ASK_IDS]}
    monkeypatch.setattr(ds, "run_week", week)
    return calls, ["--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
                   "--pending", str(tmp_path / "p.json"), "--baseline", str(tmp_path / "db.json")]


def test_day_sim_records_then_compares(monkeypatch, tmp_path):
    first = ds.ASK_IDS[0]
    calls, args = _day_gates(monkeypatch, tmp_path, {ds.ASK_IDS[-1]: "SKIP"})
    assert ds.main(args + ["--record-baseline"]) == 0
    base = json.loads((tmp_path / "db.json").read_text())
    assert set(base["cells"]) == set(ds.ASK_IDS) and base["mode"] == "default" and base["commit"] == CLEAN["commit"]
    assert base["criteria_sha256"] == ds.CRITERIA_SHA256
    # no regression: exit 0 (the SKIP in the baseline cannot regress, and is reported as uncovered)
    assert ds.main(args + ["--compare-baseline"]) == 0
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["compare"]["regressions"] == [] and res["compare"]["uncovered"] == [ds.ASK_IDS[-1]]
    # an ask that PASSED now FAILS: status regression, exit 1
    _day_gates(monkeypatch, tmp_path, {first: "FAIL", ds.ASK_IDS[-1]: "SKIP"})
    assert ds.main(args + ["--compare-baseline"]) == 1
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "regression" and res["compare"]["regressions"] == [first]


def test_day_sim_partial_compare_is_a_regression_when_the_selected_ask_goes_red(monkeypatch, tmp_path):
    """--only keeps status `partial` for a clean run, but a red comparison must still exit 1."""
    calls, args = _day_gates(monkeypatch, tmp_path, {})
    assert ds.main(args + ["--record-baseline"]) == 0
    verdicts = {"S9a": "PASS"}

    def only_run(live, user, only, samples, log):
        return {"asks": [{"id": a_, "verdict": verdicts[a_], "title": a_} for a_ in sorted(only)]}
    monkeypatch.setattr(ds, "run_only", only_run)
    cmp_args = args + ["--only", "9a", "--compare-baseline"]
    assert ds.main(cmp_args) == 0
    assert json.loads((tmp_path / "r.json").read_text())["status"] == "partial"
    verdicts["S9a"] = "FAIL"            # negative control: a PASS-in-baseline ask now FAILS
    assert ds.main(cmp_args) == 1
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "regression" and res["compare"]["regressions"] == ["S9a"]


def test_day_sim_compare_refuses_a_baseline_for_another_mode_or_missing_asks(monkeypatch, tmp_path):
    calls, args = _day_gates(monkeypatch, tmp_path, {})
    bb.write(tmp_path / "db.json", bb.make("samantha_day_sim", "0.1", {a: "PASS" for a in ds.ASK_IDS}, CLEAN,
                                           mode="allowlisted", samples=1, criteria_sha256=ds.CRITERIA_SHA256))
    assert ds.main(args + ["--compare-baseline"]) == 2 and calls == []
    assert "mode" in json.loads((tmp_path / "r.json").read_text())["reason"]
    bb.write(tmp_path / "db.json", bb.make("samantha_day_sim", "0.1", {ds.ASK_IDS[0]: "PASS"}, CLEAN,
                                           mode="default", samples=1, criteria_sha256=ds.CRITERIA_SHA256))
    assert ds.main(args + ["--compare-baseline"]) == 2 and calls == []
    assert "lacks ask(s)" in json.loads((tmp_path / "r.json").read_text())["reason"]


def test_day_sim_never_records_a_dirty_or_errored_run(monkeypatch, tmp_path):
    calls, args = _day_gates(monkeypatch, tmp_path, {}, rev=dict(CLEAN, dirty=True))
    assert ds.main(args + ["--record-baseline"]) == 2 and calls == [] and not (tmp_path / "db.json").exists()
    calls, args = _day_gates(monkeypatch, tmp_path, {ds.ASK_IDS[2]: "ERROR"})
    ds.main(args + ["--record-baseline"])
    assert calls == ["run"] and not (tmp_path / "db.json").exists()
    with pytest.raises(SystemExit):
        ds.main(args + ["--record-baseline", "--only", "S9a"])


# ── the person bench ─────────────────────────────────────────────────────────
def _arm(verdicts: dict[str, str]) -> dict:
    halves = [{"id": f"{c}.x", "cell": c, "gate": True, "expected": "PASS", "verdict": v, "n": 5, "not_exercised": 0}
              for c, v in verdicts.items()]
    return {"halves": halves, "cells": {c: {"verdict": v, "halves": [f"{c}.x"]} for c, v in verdicts.items()},
            "tier1": {"count": 0, "by_class": {}}, "counts": {}}


def _person_gates(monkeypatch, tmp_path, verdicts, rev=CLEAN):
    monkeypatch.setattr(sb, "scratch_stack_for", lambda service_dir, name, ctx, log: (_FakeStack(), None))
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sb, "service_revision", lambda d: rev)
    monkeypatch.setattr(sb, "_acquire_lock", lambda: None)
    monkeypatch.setattr(sb, "load_source_fallback_markers", lambda d: [])
    monkeypatch.setattr(sb.os, "nice", lambda n: 5)
    monkeypatch.setattr(sp.uma, "_gates", lambda *a, **k: None)
    monkeypatch.setattr(sp.ds, "_pending", lambda *a, **k: None)
    monkeypatch.setattr(sp, "read_flags", lambda d: {})
    monkeypatch.setattr(sp, "render_markdown", lambda a, r: "")
    monkeypatch.setattr(sb, "teardown", lambda *a: {"proven": True, "problems": []})
    calls = []
    monkeypatch.setattr(sp, "run_family", lambda *a, **k: calls.append("run") or {"arms": {"none": _arm(verdicts)}})
    return calls, ["--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
                   "--pending", str(tmp_path / "p.json"), "--baseline", str(tmp_path / "pb.json")]


def test_person_bench_records_then_compares(monkeypatch, tmp_path):
    cells = {c: "PASS" for c in sp.CELLS}
    cells["P11"] = "REPORT"
    calls, args = _person_gates(monkeypatch, tmp_path, cells)
    assert sp.main(args + ["--record-baseline"]) == 0
    base = json.loads((tmp_path / "pb.json").read_text())
    assert set(base["cells"]) == set(sp.CELLS) and base["arm"] == "none" and base["commit"] == CLEAN["commit"]
    assert base["prereg_sha256"] == sp.PREREG_SHA256 and base["n_cap"] is None
    assert sp.main(args + ["--compare-baseline"]) == 0
    # a PASS cell now INCONCLUSIVE has stopped proving something: a regression
    _person_gates(monkeypatch, tmp_path, dict(cells, P6="INCONCLUSIVE"))
    assert sp.main(args + ["--compare-baseline"]) == 1
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "regression" and res["compare"]["regressions"] == ["P6"]


def test_person_compare_treats_a_stable_known_failure_as_green(monkeypatch, tmp_path):
    """A cell FAIL in the baseline and still FAIL is not a regression (status stays ok); exit_code used to return 1 for the
    failed half anyway, so a known failure failed the gate (the bar and day-sim return 0). A newly failing cell still exits 1."""
    cells = {c: "PASS" for c in sp.CELLS}
    cells["P6"] = "FAIL"
    calls, args = _person_gates(monkeypatch, tmp_path, cells)
    assert sp.main(args + ["--record-baseline"]) == 1                       # recording reports the run itself: P6 failed
    assert json.loads((tmp_path / "pb.json").read_text())["cells"]["P6"] == "FAIL"
    assert sp.main(args + ["--compare-baseline"]) == 0
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["status"] == "ok" and res["summary"]["failed_halves"] == ["P6.x"]
    _person_gates(monkeypatch, tmp_path, dict(cells, P7="FAIL"))                 # a cell that PASSED in the baseline now fails
    assert sp.main(args + ["--compare-baseline"]) == 1


def test_person_bench_refuses_a_capped_comparison_or_a_dirty_record(monkeypatch, tmp_path):
    cells = {c: "PASS" for c in sp.CELLS}
    calls, args = _person_gates(monkeypatch, tmp_path, cells)
    assert sp.main(args + ["--record-baseline"]) == 0
    assert sp.main(args + ["--compare-baseline", "--n-cap", "4"]) == 2
    assert "--n-cap" in json.loads((tmp_path / "r.json").read_text())["reason"]
    with pytest.raises(SystemExit):
        sp.main(args + ["--record-baseline", "--only", "P2"])
    calls, args = _person_gates(monkeypatch, tmp_path, cells, rev=dict(CLEAN, dirty=True))
    (tmp_path / "pb.json").unlink()
    assert sp.main(args + ["--record-baseline"]) == 2 and calls == [] and not (tmp_path / "pb.json").exists()
