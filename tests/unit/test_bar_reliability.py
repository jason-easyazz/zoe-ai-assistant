"""Pin bar_reliability.py: pass^k / pass@k arithmetic, flake detection, collateral-damage regressions, the
admissibility gate (with its negative control), the JSON shape and k=1 parity with the bar's own compare.

Synthetic results payloads only - NO brain, NO network, NO scratch stack. The night runner is exercised through
--dry-run and with its gates faked; it is never allowed to start a harness here.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


br = _load("bar_reliability", "scripts/perf/bar_reliability.py")
sb = _load("samantha_bar", "scripts/perf/samantha_bar.py")

IDS = ("S1", "S2", "S3", "S4", "S5", "S6")


def run(verdicts: dict[str, str], *, status="ok", proven=True, tree="t0", samples=1, bound=True) -> dict:
    """A results payload shaped like samantha_bar.py --results writes it."""
    return {"harness_version": sb.HARNESS_VERSION, "status": status, "samples": samples,
            "judge_prompt_sha256": sb.JUDGE_PROMPT_SHA256,
            "revision": {"commit": "c" * 40, "tree": tree}, "evidence": {"bound": bound, "tree": tree},
            "scenarios": [{"id": k, "verdict": v} for k, v in verdicts.items()],
            "compare": {}, "teardown": {"proven": proven}}


def arm(rows: list[dict[str, str]], label: str, k: int = 3, **kw) -> dict:
    return br.aggregate([run(r, **kw) for r in rows], k=k, label=label)


def same(v: dict[str, str], n: int = 3) -> list[dict[str, str]]:
    return [dict(v) for _ in range(n)]


ALL_PASS = {s: "PASS" for s in IDS}


# ── the estimators ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,c,k,hat,at", [
    (3, 3, 3, 1.0, 1.0),
    (3, 2, 3, 0.0, 1.0),
    (3, 0, 3, 0.0, 0.0),
    (5, 3, 2, 3 / 10, 1 - 1 / 10),    # C(3,2)/C(5,2); 1 - C(2,2)/C(5,2)
    (4, 2, 1, 0.5, 0.5),               # k=1: both are the plain pass rate
    (1, 1, 1, 1.0, 1.0),
    (1, 0, 1, 0.0, 0.0),
])
def test_pass_hat_k_and_pass_at_k(n, c, k, hat, at):
    assert br.pass_hat_k(n, c, k) == pytest.approx(hat)
    assert br.pass_at_k(n, c, k) == pytest.approx(at)
    assert br.pass_hat_k(n, c, k) <= br.pass_at_k(n, c, k)


def test_estimators_refuse_fewer_runs_than_k():
    assert br.pass_hat_k(2, 2, 3) is None and br.pass_at_k(2, 2, 3) is None


# ── one arm ────────────────────────────────────────────────────────────────

def test_aggregate_labels_flaky_and_stable_scenarios():
    rows = same(ALL_PASS)
    rows[1]["S4"] = "FAIL"                        # S4 flips once: FLAKY
    for r in rows:
        r["S6"] = "SKIP"                          # always SKIP: a stable non-pass, not a flake
    agg = arm(rows, "control")
    assert agg["status"] == "ok" and agg["n_runs"] == 3
    assert agg["verdicts"] == {"S1": "PASS", "S2": "PASS", "S3": "PASS", "S4": "FLAKY", "S5": "PASS", "S6": "SKIP"}
    s4 = agg["scenarios"]["S4"]
    assert (s4["passes"], s4["flaky"], s4["pass_hat_k"], s4["pass_at_k"]) == (2, True, 0.0, 1.0)
    assert agg["summary"]["flaky"] == ["S4"]
    assert agg["summary"]["all_k_pass"] == ["S1", "S2", "S3", "S5"]
    assert agg["summary"]["pass_hat_k"] == pytest.approx(4 / 6, abs=1e-4)
    assert agg["summary"]["pass_at_k"] == pytest.approx(5 / 6, abs=1e-4)
    assert agg["summary"]["single_run_passed"] == 5    # today's number: run 1 alone (S6 SKIP)


def test_error_in_any_run_is_reported_and_never_a_pass():
    rows = same(ALL_PASS)
    rows[2]["S2"] = "ERROR"
    agg = arm(rows, "x")
    assert agg["summary"]["errored"] == ["S2"] and agg["verdicts"]["S2"] == "FLAKY"
    rows = same({**ALL_PASS, "S2": "ERROR"})
    assert arm(rows, "x")["verdicts"]["S2"] == "ERROR"


def test_refused_errored_or_unproven_runs_do_not_count():
    payloads = [run(ALL_PASS), run(ALL_PASS, status="refused"), run(ALL_PASS, proven=False), run(ALL_PASS, status="error")]
    agg = br.aggregate(payloads, k=3, label="x")
    assert agg["n_runs"] == 1 and agg["status"] == "incomplete"
    assert len(agg["rejected_runs"]) == 3
    assert agg["summary"]["pass_hat_k"] is None


def test_runs_of_different_code_are_not_repeats():
    agg = br.aggregate([run(ALL_PASS, tree="a"), run(ALL_PASS, tree="b"), run(ALL_PASS, tree="a")], k=3, label="x")
    assert agg["status"] == "error" and any("code" in p for p in agg["problems"])


def test_runs_with_different_scenario_sets_are_not_repeats():
    short = {k: v for k, v in ALL_PASS.items() if k != "S6"}
    agg = br.aggregate([run(ALL_PASS), run(short), run(ALL_PASS)], k=3, label="x")
    assert agg["status"] == "error"


# ── candidate vs control ───────────────────────────────────────────────────

def test_negative_control_wins_overall_but_breaks_one_scenario_is_not_admissible():
    # control: S1-S4 always pass, S5/S6 always fail -> pass^3 = 4/6
    control = arm(same({**ALL_PASS, "S5": "FAIL", "S6": "FAIL"}), "control", tree="base")
    # candidate: fixes S5 and S6 (+2) but breaks S4 (-1) -> pass^3 = 5/6, a net WIN that is still collateral damage
    candidate = arm(same({**ALL_PASS, "S4": "FAIL"}), "candidate", tree="cand")
    cmp = br.compare(control, candidate)
    assert cmp["net"] == "better" and cmp["pass_hat_k_delta"] > 0
    assert [r["id"] for r in cmp["regressions"]] == ["S4"]
    assert cmp["regressions"][0]["kind"] == "reliability"
    assert [r["id"] for r in cmp["improvements"]] == ["S5", "S6"]
    assert cmp["net_no_collateral"] is False and cmp["admissible"] is False
    assert br.verdict_exit(cmp) == br.EXIT_RED
    ok, reasons = br.admissible(control, candidate)
    assert ok is False and any("collateral" in r for r in reasons)


def test_clean_win_is_admissible_and_net_with_no_collateral():
    control = arm(same({**ALL_PASS, "S6": "FAIL"}), "control", tree="base")
    candidate = arm(same(ALL_PASS), "candidate", tree="cand")
    cmp = br.compare(control, candidate)
    assert cmp["regressions"] == [] and cmp["net"] == "better"
    assert cmp["net_no_collateral"] is True and cmp["admissible"] is True
    assert br.verdict_exit(cmp) == br.EXIT_OK


def test_equal_is_admissible_but_not_a_net_win():
    control = arm(same(ALL_PASS), "control")
    cmp = br.compare(control, arm(same(ALL_PASS), "candidate"))
    assert cmp["net"] == "equal" and cmp["admissible"] is True and cmp["net_no_collateral"] is False


def test_a_new_flake_on_an_always_passing_scenario_is_a_regression():
    control = arm(same(ALL_PASS), "control")
    rows = same(ALL_PASS)
    rows[1]["S3"] = "FAIL"                         # one flip in three: the single-run bar would often miss this
    cmp = br.compare(control, arm(rows, "candidate"))
    assert [(r["id"], r["kind"]) for r in cmp["regressions"]] == [("S3", "reliability")]
    assert cmp["admissible"] is False


def test_losing_a_flaky_pass_entirely_blocks_but_a_smaller_drop_is_only_weakened():
    rows_c = same(ALL_PASS)
    rows_c[0]["S4"] = rows_c[0]["S5"] = "FAIL"     # control: S4 2/3, S5 2/3
    rows_d = same({**ALL_PASS, "S4": "FAIL"})      # candidate: S4 0/3 (lost)
    rows_d[0]["S5"] = rows_d[1]["S5"] = "FAIL"     # candidate: S5 1/3 (weakened)
    cmp = br.compare(arm(rows_c, "control"), arm(rows_d, "candidate"))
    assert [(r["id"], r["kind"]) for r in cmp["regressions"]] == [("S4", "lost")]
    assert [r["id"] for r in cmp["weakened"]] == ["S5"]
    assert cmp["admissible"] is False


def test_worse_pass_hat_k_without_a_regression_is_not_admissible():
    # n=5 runs, k=3: S6 4/5 on control (pass^3 = 0.4) and 3/5 on the candidate (0.1) - never all-pass, never lost,
    # so only "weakened"; the overall pass^k still fell, and that alone refuses the candidate
    rows_c, rows_d = same(ALL_PASS, 5), same(ALL_PASS, 5)
    rows_c[0]["S6"] = "FAIL"
    rows_d[0]["S6"] = rows_d[1]["S6"] = "FAIL"
    cmp = br.compare(arm(rows_c, "control"), arm(rows_d, "candidate"))
    assert cmp["regressions"] == [] and [r["id"] for r in cmp["weakened"]] == ["S6"]
    assert cmp["net"] == "worse" and cmp["measurable"] is True and cmp["admissible"] is False
    assert any("pass^k worse" in r for r in cmp["reasons"])


@pytest.mark.parametrize("mutate,needle", [
    (lambda c, d: d.update(status="incomplete"), "status incomplete"),
    (lambda c, d: d.update(samples=3), "samples"),
    (lambda c, d: d.update(rubric_sha256="other"), "rubric"),
    (lambda c, d: d.update(k=5), "k differs"),
])
def test_incomparable_or_incomplete_arms_are_unmeasured_never_admissible(mutate, needle):
    control, candidate = arm(same(ALL_PASS), "control"), arm(same(ALL_PASS), "candidate")
    mutate(control, candidate)
    cmp = br.compare(control, candidate)
    assert cmp["measurable"] is False and cmp["admissible"] is False
    assert br.verdict_exit(cmp) == br.EXIT_UNMEASURED
    assert any(needle in r for r in cmp["reasons"])


def test_unknown_rubric_on_one_arm_is_not_the_same_rubric():
    control, candidate = arm(same(ALL_PASS), "control"), arm(same(ALL_PASS), "candidate")
    candidate["rubric_sha256"] = None
    cmp = br.compare(control, candidate)
    assert cmp["measurable"] is False and any("rubric" in r for r in cmp["reasons"])


def test_the_real_quiet_hours_gate_ignores_an_inherited_force(monkeypatch):
    hq = br._harness_quiet()
    monkeypatch.setenv("ZOE_HARNESS_FORCE", "1")
    monkeypatch.setenv("ZOE_HARNESS_LANDING", "1")
    noon = br.dt.datetime(2026, 11, 4, 12, 0)
    v = hq.check("bar_reliability", force=False, landing=False, now=noon, panel=lambda: True)
    assert v.allowed is False and v.kind == "hours"      # what step_gate asks: still refused at noon


def test_child_env_strips_the_bypasses():
    env = br.child_env({"ZOE_HARNESS_FORCE": "1", "ZOE_HARNESS_LANDING": "1", "PATH": "/bin"})
    assert env == {"PATH": "/bin", "ZOE_PERF": "1"}


def test_lander_pattern_matches_bash_and_direct_launches():
    import re
    pat = re.compile(br.LANDER_PATTERN)
    assert pat.search("bash /home/zoe/assistant/scripts/landing/land_voice_pr.sh 2278")
    assert pat.search("/home/zoe/.zoe/agent-tools/land_voice_pr.sh 2278")
    assert not pat.search("python3 land_voice_pr.sh.bak")


def test_an_instrument_error_makes_the_comparison_unmeasured():
    rows = same(ALL_PASS)
    rows[0]["S2"] = "ERROR"
    cmp = br.compare(arm(same(ALL_PASS), "control"), arm(rows, "candidate"))
    assert cmp["measurable"] is False and br.verdict_exit(cmp) == br.EXIT_UNMEASURED


def test_scenario_set_mismatch_is_unmeasured():
    short = {k: v for k, v in ALL_PASS.items() if k != "S6"}
    cmp = br.compare(arm(same(ALL_PASS), "control"), arm(same(short), "candidate"))
    assert cmp["measurable"] is False


# ── k=1: today's single-run compare, unchanged ────────────────────────────

@pytest.mark.parametrize("base,cur", [
    ({"S1": "PASS", "S2": "PASS", "S3": "FAIL", "S4": "SKIP"}, {"S1": "PASS", "S2": "FAIL", "S3": "PASS", "S4": "SKIP"}),
    ({"S1": "PASS", "S2": "PASS", "S3": "PASS", "S4": "PASS"}, {"S1": "SKIP", "S2": "PASS", "S3": "PASS", "S4": "PASS"}),
    ({"S1": "FAIL", "S2": "FAIL", "S3": "FAIL", "S4": "FAIL"}, {"S1": "FAIL", "S2": "FAIL", "S3": "FAIL", "S4": "FAIL"}),
])
def test_k1_regressions_match_the_bars_own_compare(base, cur):
    control = br.aggregate([run(base)], k=1, label="control")
    candidate = br.aggregate([run(cur)], k=1, label="candidate")
    assert candidate["verdicts"] == sb.verdict_map(run(cur)["scenarios"])     # k=1 verdicts ARE the run's
    assert candidate["single_run_verdicts"] == candidate["verdicts"]
    cmp = br.compare(control, candidate)
    ours = sorted(r["id"] for r in cmp["regressions"])
    assert ours == sb.compare_baseline(cur, {"scenarios": base})["regressions"]
    assert candidate["summary"]["pass_hat_k"] == candidate["summary"]["pass_at_k"] == candidate["summary"]["pass_rate"]


def test_a_bar_baseline_file_reads_as_one_run(tmp_path):
    results = [{"id": "S1", "verdict": "PASS"}, {"id": "S2", "verdict": "FAIL"}]
    base = sb.make_baseline(results, {"commit": "c" * 40, "tree": "t0"}, 1)
    p = tmp_path / "samantha_bar_baseline.json"
    p.write_text(json.dumps(base))
    agg = br.as_aggregate(p, k=1, label="baseline")
    assert agg["status"] == "ok" and agg["verdicts"] == {"S1": "PASS", "S2": "FAIL"}


def test_day_sim_asks_shape_is_read():
    payload = {"status": "ok", "samples": 1, "criteria_sha256": "x", "teardown": {"proven": True},
               "asks": [{"id": "1a", "verdict": "PASS"}, {"id": "1b", "verdict": "SKIP"}]}
    agg = br.aggregate([payload], k=1, label="d")
    assert agg["harness"] == "day-sim" and agg["verdicts"] == {"1a": "PASS", "1b": "SKIP"}


# ── JSON + CLI ─────────────────────────────────────────────────────────────

AGG_KEYS = {"schema", "label", "harness", "k", "n_runs", "status", "problems", "rejected_runs", "sources", "samples",
            "rubric_sha256", "code", "all_runs_bound", "summary", "single_run_verdicts", "verdicts", "scenarios"}
SUMMARY_KEYS = {"scenarios", "pass_hat_k", "pass_at_k", "pass_rate", "single_run_passed", "all_k_pass", "flaky", "errored"}
SCENARIO_KEYS = {"runs", "passes", "verdicts", "verdict", "flaky", "errors", "pass_hat_k", "pass_at_k", "pass_rate"}
COMPARE_KEYS = {"schema", "control", "candidate", "k", "pass_hat_k_delta", "net", "regressions", "weakened",
                "improvements", "net_no_collateral", "measurable", "admissible", "reasons", "same_code"}


def test_json_schema_is_stable_and_serialisable():
    agg = arm(same(ALL_PASS), "control")
    assert set(agg) == AGG_KEYS and agg["schema"] == br.SCHEMA
    assert set(agg["summary"]) == SUMMARY_KEYS
    assert all(set(v) == SCENARIO_KEYS for v in agg["scenarios"].values())
    cmp = br.compare(agg, arm(same(ALL_PASS), "candidate"))
    assert set(cmp) == COMPARE_KEYS and cmp["schema"] == br.COMPARE_SCHEMA
    json.loads(json.dumps({"arm": agg, "compare": cmp}))


def _write_runs(d: Path, rows: list[dict[str, str]], **kw) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(rows, 1):
        (d / f"run-{i}.json").write_text(json.dumps(run(r, **kw)))
    return d


def test_cli_compare_admissible_exit_codes(tmp_path):
    ctl = _write_runs(tmp_path / "control", same({**ALL_PASS, "S5": "FAIL", "S6": "FAIL"}), tree="base")
    bad = _write_runs(tmp_path / "bad", same({**ALL_PASS, "S4": "FAIL"}), tree="cand")
    good = _write_runs(tmp_path / "good", same({**ALL_PASS, "S6": "FAIL"}), tree="cand")
    out = tmp_path / "cmp.json"
    quiet = lambda *_: None  # noqa: E731
    assert br.main(["compare", "--control", str(ctl), "--candidate", str(bad), "--admissible",
                    "--out", str(out)], log=quiet) == 1
    assert json.loads(out.read_text())["regressions"][0]["id"] == "S4"
    assert br.main(["compare", "--control", str(ctl), "--candidate", str(good), "--admissible"], log=quiet) == 0
    short = _write_runs(tmp_path / "short", same(ALL_PASS)[:2], tree="cand")
    assert br.main(["compare", "--control", str(ctl), "--candidate", str(short), "--admissible"], log=quiet) == 2


def test_cli_score_baseline_record_and_compare(tmp_path):
    base = tmp_path / "rel_baseline.json"
    quiet = lambda *_: None  # noqa: E731
    d = _write_runs(tmp_path / "a", same(ALL_PASS))
    assert br.main(["score", str(d), "--compare-baseline", "--baseline", str(base)], log=quiet) == 2  # none yet
    assert br.main(["score", str(d), "--record-baseline", "--baseline", str(base)], log=quiet) == 0
    assert json.loads(base.read_text())["schema"] == br.SCHEMA
    rows = same(ALL_PASS)
    rows[2]["S1"] = "FAIL"
    e = _write_runs(tmp_path / "b", rows)
    assert br.main(["score", str(e), "--compare-baseline", "--baseline", str(base)], log=quiet) == 1
    assert br.main(["score", str(d), "--compare-baseline", "--baseline", str(base)], log=quiet) == 0


def test_baseline_refuses_unbound_or_errored_runs(tmp_path):
    base = tmp_path / "b.json"
    quiet = lambda *_: None  # noqa: E731
    d = _write_runs(tmp_path / "u", same(ALL_PASS), bound=False)
    assert br.main(["score", str(d), "--record-baseline", "--baseline", str(base)], log=quiet) == 2
    assert not base.exists()


# ── the night runner (never starts a harness here) ────────────────────────

def test_schedule_interleaves_and_alternates():
    assert br.run_schedule(["control", "candidate"], 3) == [
        (1, "control"), (1, "candidate"), (2, "candidate"), (2, "control"), (3, "control"), (3, "candidate")]


def test_run_dry_run_prints_the_plan_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(br.subprocess, "Popen", lambda *a, **k: pytest.fail("dry run started a process"))
    lines: list[str] = []
    rc = br.main(["run", "--out-dir", str(tmp_path), "--arm", f"control={REPO}", "--arm", f"candidate={REPO}",
                  "--k", "2", "--dry-run", "--", "--samples", "3"], log=lines.append)
    assert rc == 0
    steps = [ln for ln in lines if "round" in ln]
    assert len(steps) == 4 and all("--samples 3" in ln and "--results" in ln for ln in steps)
    assert all("samantha_bar.py" in ln for ln in steps)


def test_run_stops_at_a_closed_gate_and_reports_unmeasured(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(br.subprocess, "Popen", lambda *a, **k: pytest.fail("a closed gate started a process"))
    args = br.argparse.Namespace(arm=[f"control={REPO}", f"candidate={REPO}"], harness="bar", k=3, out_dir=tmp_path,
                                 extra=[], dry_run=False, step_min=35)
    lines: list[str] = []
    rc = br.run_arms(args, lines.append, gate=lambda *a, **k: "night_busy.sh voice says pause", hq=object())
    assert rc == br.EXIT_UNMEASURED
    assert any("night_busy.sh voice says pause" in ln for ln in lines)
    assert json.loads((tmp_path / "reliability.json").read_text())["compare"]["measurable"] is False


def test_run_without_zoe_perf_is_a_skip(tmp_path, monkeypatch):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    monkeypatch.setattr(br.subprocess, "Popen", lambda *a, **k: pytest.fail("started without ZOE_PERF"))
    assert br.main(["run", "--out-dir", str(tmp_path), "--arm", f"control={REPO}"], log=lambda *_: None) == 0


def test_step_gate_consults_quiet_hours_night_pause_and_landers():
    class HQ:
        def __init__(self, hours=True, night=True):
            self.h, self.n = hours, night

        def check(self, name, *, force=None, landing=None):
            # the bypasses are pinned OFF: an inherited ZOE_HARNESS_FORCE / _LANDING must not open the hours
            assert force is False and landing is False
            return br.argparse.Namespace(allowed=self.h, reason="quiet hours")

        def night_pause_verdict(self, name, *, budget_min, kind):
            assert kind == "voice" and budget_min == 35
            return br.argparse.Namespace(allowed=self.n, reason="night pause")

    no_lander = lambda *a, **k: br.argparse.Namespace(returncode=1)  # noqa: E731
    lander = lambda *a, **k: br.argparse.Namespace(returncode=0)  # noqa: E731
    assert br.step_gate("x", 35, hq=HQ(hours=False), runner=no_lander) == "quiet hours"
    assert br.step_gate("x", 35, hq=HQ(night=False), runner=no_lander) == "night pause"
    assert "land_voice_pr" in br.step_gate("x", 35, hq=HQ(), runner=lander)
    assert br.step_gate("x", 35, hq=HQ(), runner=no_lander) is None
