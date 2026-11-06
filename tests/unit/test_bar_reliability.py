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
    # Reuse a module another test file already imported: replacing sys.modules["samantha_bar"] would leave modules bound
    # to the OLD copy (samantha_day_sim's ``sb``) while later tests monkeypatch the NEW one - their patches then miss.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


br = _load("bar_reliability", "scripts/perf/bar_reliability.py")
sb = _load("samantha_bar", "scripts/perf/samantha_bar.py")

IDS = ("S1", "S2", "S3", "S4", "S5", "S6")


def run(verdicts: dict[str, str], *, status="ok", proven=True, tree="t0", samples=1, bound=True,
        partial: bool | None = False, backdate: bool | None = True) -> dict:
    """A results payload shaped like samantha_bar.py --results writes it (``partial=None``: a writer without it)."""
    extra = {} if partial is None else {"partial": partial}
    if backdate is not None:
        extra["backdate"] = backdate
    return {"harness_version": sb.HARNESS_VERSION, "status": status, "samples": samples, **extra,
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

AGG_KEYS = {"schema", "label", "harness", "k", "n_runs", "status", "coverage", "backdate", "problems", "rejected_runs", "sources",
            "samples", "arm_env",
            "rubric_sha256", "code", "all_runs_bound", "summary", "single_run_verdicts", "verdicts", "scenarios"}
SUMMARY_KEYS = {"scenarios", "pass_hat_k", "pass_at_k", "pass_rate", "single_run_passed", "all_k_pass", "flaky", "errored"}
SCENARIO_KEYS = {"runs", "passes", "verdicts", "verdict", "flaky", "errors", "pass_hat_k", "pass_at_k", "pass_rate"}
COMPARE_KEYS = {"schema", "control", "candidate", "k", "pass_hat_k_delta", "net", "regressions", "weakened",
                "improvements", "net_no_collateral", "measurable", "admissible", "reasons", "same_code", "arm_env", "arm_env_differs"}


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


def test_one_run_referenced_k_times_is_one_run_not_k(tmp_path):
    """A directory plus one of its runs, a repeated path, or a symlink must not count the same measurement twice."""
    base = tmp_path / "b.json"
    quiet = lambda *_: None  # noqa: E731
    one = _write_runs(tmp_path / "one", same(ALL_PASS, 1)) / "run-1.json"
    (tmp_path / "link.json").symlink_to(one)
    assert len(br.expand_inputs([one, one, tmp_path / "link.json", one.parent])) == 1
    assert br.main(["score", str(one), str(one), str(tmp_path / "link.json"), "--record-baseline",
                    "--baseline", str(base)], log=quiet) == 2
    assert not base.exists()
    d = _write_runs(tmp_path / "three", same(ALL_PASS))
    assert len(br.expand_inputs([d, d / "run-1.json"])) == 3


def test_baseline_refuses_unbound_or_errored_runs(tmp_path):
    base = tmp_path / "b.json"
    quiet = lambda *_: None  # noqa: E731
    d = _write_runs(tmp_path / "u", same(ALL_PASS), bound=False)
    assert br.main(["score", str(d), "--record-baseline", "--baseline", str(base)], log=quiet) == 2
    assert not base.exists()


# ── the night runner (never starts a harness here) ────────────────────────

def _only_vcs(real, why: str):
    """A Popen that allows only the read-only arm-identity probe (``git -C <checkout> ...``) - never a harness."""
    def popen(cmd, *a, **k):
        if not (isinstance(cmd, list) and cmd[:2] == ["git", "-C"]):
            pytest.fail(why)
        return real(cmd, *a, **k)
    return popen


def test_schedule_interleaves_and_alternates():
    assert br.run_schedule(["control", "candidate"], 3) == [
        (1, "control"), (1, "candidate"), (2, "candidate"), (2, "control"), (3, "control"), (3, "candidate")]


def test_run_dry_run_prints_the_plan_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(br.subprocess, "Popen", _only_vcs(br.subprocess.Popen, "dry run started a process"))
    lines: list[str] = []
    rc = br.main(["run", "--out-dir", str(tmp_path), "--arm", f"control={REPO}", "--arm", f"candidate={REPO}",
                  "--k", "2", "--dry-run", "--", "--samples", "3"], log=lines.append)
    assert rc == 0
    steps = [ln for ln in lines if "round" in ln]
    assert len(steps) == 4 and all("--samples 3" in ln and "--results" in ln for ln in steps)
    assert all("samantha_bar.py" in ln for ln in steps)


def test_run_stops_at_a_closed_gate_and_reports_unmeasured(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(br.subprocess, "Popen", _only_vcs(br.subprocess.Popen, "a closed gate started a process"))
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


# ── coverage: a --only / --axis run is never a complete arm (codex review, #2278) ──

S1_ONLY = {"S1": "PASS"}


def test_repeated_partial_runs_are_not_promoted_to_a_complete_arm(tmp_path):
    part = arm(same(S1_ONLY), "control", status="partial", partial=True)
    assert part["status"] == "partial" and part["coverage"] == "partial" and part["n_runs"] == 3
    cand = arm(same(S1_ONLY), "candidate", status="partial", partial=True)
    cmp = br.compare(part, cand)
    assert cmp["measurable"] is False and cmp["admissible"] is False
    assert any("coverage 'partial'" in r for r in cmp["reasons"])
    assert "coverage" in br.baseline_record_problem(part)
    base = tmp_path / "b.json"
    d = _write_runs(tmp_path / "p", same(S1_ONLY), status="partial", partial=True)
    assert br.main(["score", str(d), "--record-baseline", "--baseline", str(base)], log=lambda *_: None) == 2
    assert not base.exists()
    # negative control: the SAME one-scenario verdicts from full-coverage runs are a complete, admissible arm
    full_c, full_d = arm(same(S1_ONLY), "control"), arm(same(S1_ONLY), "candidate")
    assert full_c["status"] == "ok" and full_c["coverage"] == "full"
    assert br.compare(full_c, full_d)["admissible"] is True and br.baseline_record_problem(full_c) is None


def test_a_red_partial_run_stays_partial_and_unknown_coverage_is_not_full():
    red = arm(same(S1_ONLY), "x", status="regression", partial=True)        # the bar's red --only run
    assert red["coverage"] == "partial" and red["status"] == "partial"
    unknown = arm(same(ALL_PASS), "x", status="regression", partial=None)   # a writer that does not say
    assert unknown["coverage"] == "unknown" and unknown["status"] == "partial"
    assert arm(same(ALL_PASS), "x", status="regression")["status"] == "ok"  # a full red run is a full run
    mixed = br.aggregate([run(ALL_PASS), run(ALL_PASS), run(ALL_PASS, status="partial", partial=True)], k=3)
    assert mixed["status"] == "error" and any("coverage" in p for p in mixed["problems"])


def test_a_bar_baseline_reads_as_full_coverage(tmp_path):
    f = tmp_path / "samantha_bar_baseline.json"
    f.write_text(json.dumps({"scenarios": dict(ALL_PASS)}))
    assert br.as_aggregate(f, k=1, label="baseline")["coverage"] == "full"


# ── resume: a cached run is reused only for the SAME arm identity (codex review, #2278) ──

def _vcs(path: Path, *cmd: str) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@e", *cmd],
                   check=True, capture_output=True)


def _checkout(path: Path) -> Path:
    (path / "scripts" / "perf").mkdir(parents=True)
    (path / "scripts" / "perf" / "samantha_bar.py").write_text("# stand-in\n")
    _vcs(path, "init", "-q")
    _vcs(path, "add", "-A")
    _vcs(path, "commit", "-qm", "base")
    return path


def _commit(path: Path, text: str) -> None:
    (path / "scripts" / "perf" / "samantha_bar.py").write_text(text)
    _vcs(path, "add", "-A")
    _vcs(path, "commit", "-qm", "change")


class _HQ:
    def lock_wait_s(self, *a, **k):
        return 0

    def deadline_s(self):
        return 100


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """run_arms with every gate open and a fake step that writes an all-PASS run; returns (invoke, steps)."""
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(br, "LOCK", str(tmp_path / "harness.lock"))
    steps: list[list[str]] = []

    def fake_step(cmd, deadline, env, log):
        steps.append(cmd)
        Path(cmd[cmd.index("--results") + 1]).write_text(json.dumps(run(ALL_PASS)))
        return 0
    monkeypatch.setattr(br, "_run_step", fake_step)

    def invoke(arms: list[str], *, k=2, extra=(), gate=lambda *a, **kw: None) -> int:
        args = br.argparse.Namespace(arm=arms, harness="bar", k=k, out_dir=tmp_path / "out", extra=list(extra),
                                     dry_run=False, step_min=35)
        return br.run_arms(args, lambda *_: None, gate=gate, hq=_HQ())
    return invoke, steps


def test_resume_reuses_a_cached_run_only_for_the_same_checkout_and_args(tmp_path, runner):
    invoke, steps = runner
    ck = _checkout(tmp_path / "ck")
    assert invoke([f"control={ck}"]) == br.EXIT_OK and len(steps) == 2
    assert invoke([f"control={ck}"]) == br.EXIT_OK and len(steps) == 2          # negative control: same arm = reused
    _commit(ck, "# the next candidate\n")
    assert invoke([f"control={ck}"]) == br.EXIT_OK and len(steps) == 4          # a changed checkout re-runs
    rel = json.loads((tmp_path / "out" / "reliability.json").read_text())
    assert rel["arms"]["control"]["arm_identity"]["commit"] == br._git(ck, "rev-parse", "HEAD")
    assert invoke([f"control={ck}"], extra=["--samples", "3"]) == br.EXIT_OK and len(steps) == 6   # other args re-run
    assert all("--samples" in s for s in steps[4:])


def test_stale_cached_runs_are_rejected_not_reported_as_the_new_candidate(tmp_path, runner):
    invoke, steps = runner
    ctl, cand = _checkout(tmp_path / "ctl"), _checkout(tmp_path / "cand")
    assert invoke([f"control={ctl}", f"candidate={cand}"]) == br.EXIT_OK and len(steps) == 4   # admissible
    _commit(cand, "# a different candidate, never measured\n")
    closed = lambda *a, **kw: "night_busy.sh voice says pause"  # noqa: E731
    assert invoke([f"control={ctl}", f"candidate={cand}"], gate=closed) == br.EXIT_UNMEASURED and len(steps) == 4
    rel = json.loads((tmp_path / "out" / "reliability.json").read_text())
    assert rel["compare"]["admissible"] is False
    assert rel["arms"]["candidate"]["n_runs"] == 0
    assert all("different arm identity" in r["reason"] for r in rel["arms"]["candidate"]["rejected_runs"])
    assert rel["arms"]["control"]["status"] == "ok"                              # the unchanged arm keeps its runs


def test_a_checkout_that_changes_during_a_step_discards_that_run(tmp_path, runner, monkeypatch):
    invoke, steps = runner
    ck = _checkout(tmp_path / "ck")

    def step_then_move(cmd, deadline, env, log):
        steps.append(cmd)
        Path(cmd[cmd.index("--results") + 1]).write_text(json.dumps(run(ALL_PASS)))
        _commit(ck, f"# moved during step {len(steps)}\n")
        return 0
    monkeypatch.setattr(br, "_run_step", step_then_move)
    assert invoke([f"control={ck}"]) == br.EXIT_UNMEASURED and len(steps) == 1
    assert not br.run_file(tmp_path / "out", "control", 1).exists()


def test_an_unverifiable_checkout_never_reuses_a_cached_run(tmp_path):
    plain = tmp_path / "not-a-checkout"
    plain.mkdir()
    a = br.arm_identity(str(plain), "bar", [])
    b = br.arm_identity(str(plain), "bar", [])
    assert a["verified"] is False and a != b                                     # a nonce: nothing can match


# ── backdate: a --no-backdate diagnostic run never records or compares as the bar (codex review, #2278) ──

def test_no_backdate_and_unknown_backdate_runs_never_record_a_baseline(tmp_path):
    for bd, want in ((False, "no"), (None, "unknown")):
        a = arm(same(ALL_PASS), "control", backdate=bd)
        assert a["backdate"] == want and a["status"] == "ok"
        assert "backdate" in br.baseline_record_problem(a)
        base = tmp_path / f"b{want}.json"
        d = _write_runs(tmp_path / f"r{want}", same(ALL_PASS), backdate=bd)
        assert br.main(["score", str(d), "--record-baseline", "--baseline", str(base)], log=lambda *_: None) == 2
        assert not base.exists()
    # negative control: the same runs with the backdate recorded are accepted
    ok = arm(same(ALL_PASS), "control")
    assert ok["backdate"] == "yes" and br.baseline_record_problem(ok) is None


def test_no_backdate_arms_are_not_comparable_and_never_admissible():
    normal = arm(same(ALL_PASS), "control")
    diag = arm(same(ALL_PASS), "candidate", backdate=False)
    for c, d in ((normal, diag), (diag, normal), (diag, arm(same(ALL_PASS), "x", backdate=False))):
        cmp = br.compare(c, d)
        assert cmp["measurable"] is False and cmp["admissible"] is False
        assert any("backdate" in r for r in cmp["reasons"])
    assert br.compare(normal, arm(same(ALL_PASS), "candidate"))["admissible"] is True


def test_mixed_backdate_runs_in_one_arm_are_an_error():
    mixed = br.aggregate([run(ALL_PASS), run(ALL_PASS, backdate=False), run(ALL_PASS)], k=3, label="m")
    assert mixed["status"] == "error" and any("backdate" in p for p in mixed["problems"])


# ── per-arm flag overrides (--arm-env) ───────────────────────────────────────────────────────────────────────────────

FLAG = {"ZOE_RECALL_EVERY_TURN": "on", "ZOE_RECALL_EVERY_TURN_HARNESS": "1"}
ARM_ENV_SPEC = "candidate:ZOE_RECALL_EVERY_TURN=on,ZOE_RECALL_EVERY_TURN_HARNESS=1"


def env_run(arm_env: dict[str, str] | None, verdicts=ALL_PASS) -> dict:
    """A run as the scratch stack records it: ``scratch.arm_env`` (public() of the stack)."""
    return {**run(verdicts), "scratch": {"tag": "t", "arm_env": dict(arm_env or {})}}


def env_arm(label: str, arm_env: dict[str, str] | None, n: int = 3) -> dict:
    return br.aggregate([env_run(arm_env) for _ in range(n)], k=3, label=label)


def test_parse_arm_env_builds_per_arm_overrides():
    assert br.parse_arm_env([ARM_ENV_SPEC], ["control", "candidate"]) == {"candidate": FLAG}
    assert br.parse_arm_env([], ["control"]) == {}
    assert br.parse_arm_env(["control:ZOE_A=1", "candidate:ZOE_A=2"], ["control", "candidate"]) == {
        "control": {"ZOE_A": "1"}, "candidate": {"ZOE_A": "2"}}


@pytest.mark.parametrize("spec", [
    "candidate:PATH=/bin",                      # not ZOE_*
    "candidate:ZOE_HARNESS_FORCE=1",            # the bypasses
    "candidate:ZOE_HARNESS_LANDING=1",
    "candidate:ZOE_WEBHOOK_SECRET=x",           # secret-looking name
    "candidate:ZOE_LOG_DIR=/tmp/x",             # location
    "candidate:ZOE_X=http://h/x",               # URL value
    "ghost:ZOE_X=1",                            # undeclared arm
    "candidate",                                # no overrides
    "candidate:ZOE_X",                          # no value
    "candidate:ZOE_X=1,ZOE_X=2",                # duplicate
])
def test_parse_arm_env_refuses_bad_input_without_echoing_a_value(spec):
    with pytest.raises(ValueError) as exc:
        br.parse_arm_env([spec], ["control", "candidate"])
    assert "/tmp/x" not in str(exc.value) and "http://h" not in str(exc.value)


def test_the_cli_refuses_a_forbidden_override_before_anything_runs(tmp_path):
    for spec in ("candidate:ZOE_HARNESS_FORCE=1", "candidate:HOME=/x"):
        with pytest.raises(SystemExit):
            br.main(["run", "--out-dir", str(tmp_path), "--arm", f"control={REPO}", "--arm", f"candidate={REPO}",
                     "--arm-env", spec, "--dry-run"], log=lambda *_: None)


def test_child_env_carries_the_override_to_its_arm_only_and_drops_an_inherited_one():
    var = br._hs().ARM_ENV_VAR
    base = {"PATH": "/bin", "ZOE_HARNESS_FORCE": "1", var: json.dumps({"ZOE_LEAKED": "1"})}
    ctl, cand = br.child_env(base), br.child_env(base, FLAG)
    assert var not in ctl and "ZOE_HARNESS_FORCE" not in ctl and ctl["ZOE_PERF"] == "1"     # nothing inherited reaches control
    assert json.loads(cand[var]) == FLAG and "ZOE_HARNESS_FORCE" not in cand


def test_dry_run_shows_the_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(br.subprocess, "Popen", _only_vcs(br.subprocess.Popen, "dry run started a process"))
    lines: list[str] = []
    rc = br.main(["run", "--out-dir", str(tmp_path), "--arm", f"control={REPO}", "--arm", f"candidate={REPO}",
                  "--arm-env", ARM_ENV_SPEC, "--k", "1", "--dry-run"], log=lines.append)
    text = "\n".join(lines)
    assert rc == 0
    assert "arm candidate overrides" in text and "ZOE_RECALL_EVERY_TURN=on" in text
    assert [ln for ln in lines if "round 1 control" in ln and "env[" in ln] == []            # control carries none
    assert [ln for ln in lines if "round 1 candidate" in ln and "env[ZOE_RECALL_EVERY_TURN" in ln]


def test_the_dry_run_never_prints_a_long_or_token_shaped_value(tmp_path):
    secret = "Mixed_Case_Value_" + "x" * 15      # 32 chars, mixed-case + underscore: masked by show_value
    lines: list[str] = []
    br.main(["run", "--out-dir", str(tmp_path), "--arm", f"control={REPO}", "--arm", f"candidate={REPO}",
             "--arm-env", f"candidate:ZOE_SOME_FLAG={secret}", "--dry-run"], log=lines.append)
    assert secret not in "\n".join(lines) and "ZOE_SOME_FLAG=<32 chars>" in "\n".join(lines)


def test_aggregate_records_the_overrides_the_runs_measured():
    assert env_arm("candidate", FLAG)["arm_env"] == FLAG
    assert env_arm("control", None)["arm_env"] == {}
    assert arm([ALL_PASS] * 3, "legacy")["arm_env"] == {}                                    # a writer that predates them


def test_runs_with_different_overrides_are_not_repeats_of_one_arm():
    mixed = br.aggregate([env_run(FLAG), env_run(FLAG), env_run(None)], k=3, label="candidate")
    assert mixed["status"] == "error" and any("arm env override" in p for p in mixed["problems"])


def test_differing_overrides_are_the_declared_difference_not_a_comparability_problem():
    ctl, cand = env_arm("control", None), env_arm("candidate", FLAG)
    cmp = br.compare(ctl, cand, declared={"control": {}, "candidate": FLAG})
    assert cmp["measurable"] and cmp["admissible"]
    assert cmp["arm_env"] == {"control": {}, "candidate": FLAG} and cmp["arm_env_differs"] is True
    assert br.compare(ctl, env_arm("candidate", None))["arm_env_differs"] is False          # same flags: no declared difference


def test_a_run_that_recorded_other_than_the_declared_override_is_not_comparable():
    ctl = env_arm("control", None)
    # the candidate was declared with the flag but its runs recorded none (the override never reached the stack)
    cmp = br.compare(ctl, env_arm("candidate", None), declared={"control": {}, "candidate": FLAG})
    assert cmp["measurable"] is False and not cmp["admissible"]
    assert any("not the declared" in r for r in cmp["reasons"])
    # ... or recorded a different value / an extra flag
    for got in ({"ZOE_RECALL_EVERY_TURN": "shadow", "ZOE_RECALL_EVERY_TURN_HARNESS": "1"}, {**FLAG, "ZOE_EXTRA": "1"}):
        assert br.compare(ctl, env_arm("candidate", got), declared={"control": {}, "candidate": FLAG})["measurable"] is False
    # ... and the control may not carry an undeclared one either
    assert br.compare(env_arm("control", FLAG), env_arm("candidate", FLAG),
                      declared={"control": {}, "candidate": FLAG})["measurable"] is False
    # negative control: exactly the declared override is comparable
    assert br.compare(ctl, env_arm("candidate", FLAG), declared={"control": {}, "candidate": FLAG})["measurable"] is True


def test_other_differences_between_arms_stay_not_comparable_with_an_override_declared():
    ctl = env_arm("control", None)
    cand = br.aggregate([{**env_run(FLAG), "samples": 5} for _ in range(3)], k=3, label="candidate")
    cmp = br.compare(ctl, cand, declared={"control": {}, "candidate": FLAG})
    assert cmp["measurable"] is False and any("samples" in r for r in cmp["reasons"])


def test_an_overridden_arm_never_records_the_bar():
    agg = br.aggregate([env_run(FLAG) for _ in range(3)], k=3, label="run")
    assert agg["status"] == "ok" and "arm env override" in (br.baseline_record_problem(agg) or "")


@pytest.fixture
def env_runner(tmp_path, monkeypatch):
    """run_arms with gates open and a fake step that records, like the scratch stack, the override it was handed."""
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(br, "LOCK", str(tmp_path / "harness.lock"))
    seen: list[tuple[list[str], dict]] = []

    def fake_step(cmd, deadline, env, log):
        got = json.loads(env[br._hs().ARM_ENV_VAR]) if br._hs().ARM_ENV_VAR in env else {}
        seen.append((cmd, got))
        Path(cmd[cmd.index("--results") + 1]).write_text(json.dumps(env_run(got)))
        return 0
    monkeypatch.setattr(br, "_run_step", fake_step)

    def invoke(arm_env: list[str], *, k=2) -> int:
        args = br.argparse.Namespace(arm=[f"control={REPO}", f"candidate={REPO}"], arm_env=arm_env, harness="bar", k=k,
                                     out_dir=tmp_path / "out", extra=[], dry_run=False, step_min=35)
        return br.run_arms(args, lambda *_: None, gate=lambda *a, **kw: None, hq=_HQ())
    return invoke, seen, tmp_path / "out"


def test_the_override_reaches_only_its_arms_steps_and_is_in_each_runs_json(env_runner):
    invoke, seen, out = env_runner
    assert invoke([ARM_ENV_SPEC]) == br.EXIT_OK
    assert len(seen) == 4
    for cmd, got in seen:
        arm_name = Path(cmd[cmd.index("--results") + 1]).parent.name
        assert got == (FLAG if arm_name == "candidate" else {}), arm_name
    for arm_name, want in (("control", {}), ("candidate", FLAG)):
        for i in (1, 2):
            assert json.loads((out / arm_name / f"run-{i}.json").read_text())["scratch"]["arm_env"] == want
    rel = json.loads((out / "reliability.json").read_text())
    assert rel["declared_arm_env"] == {"control": {}, "candidate": FLAG}
    assert rel["compare"]["measurable"] and rel["compare"]["arm_env_differs"] is True


def test_a_cached_run_is_not_reused_for_a_different_override(env_runner):
    invoke, seen, _ = env_runner
    assert invoke([ARM_ENV_SPEC]) == br.EXIT_OK and len(seen) == 4
    assert invoke([ARM_ENV_SPEC]) == br.EXIT_OK and len(seen) == 4                  # same override: reused
    assert invoke(["candidate:ZOE_RECALL_EVERY_TURN=shadow"]) == br.EXIT_OK and len(seen) == 6   # only the candidate re-runs


def test_an_override_the_stack_did_not_record_makes_the_run_unmeasured(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(br, "LOCK", str(tmp_path / "harness.lock"))

    def fake_step(cmd, deadline, env, log):        # a harness that ignored the override (records none)
        Path(cmd[cmd.index("--results") + 1]).write_text(json.dumps(env_run(None)))
        return 0
    monkeypatch.setattr(br, "_run_step", fake_step)
    args = br.argparse.Namespace(arm=[f"control={REPO}", f"candidate={REPO}"], arm_env=[ARM_ENV_SPEC], harness="bar", k=2,
                                 out_dir=tmp_path / "out", extra=[], dry_run=False, step_min=35)
    assert br.run_arms(args, lambda *_: None, gate=lambda *a, **kw: None, hq=_HQ()) == br.EXIT_UNMEASURED


@pytest.mark.parametrize("value", ["ghp_ABC12345", "AKIAIOSFODNN", "Sup3rSecret", "abcdef012345", "x" * 40])
def test_the_dry_run_shows_only_plain_words_and_numbers(value):
    assert br.show_value(value) == f"<{len(value)} chars>"
    for plain in ("on", "shadow", "1", "0.5", "enforce"):
        assert br.show_value(plain) == plain


def test_a_recorded_override_is_compared_as_recorded_never_coerced():
    declared = {"control": {}, "candidate": {"ZOE_X": "1"}}
    ctl = env_arm("control", None)
    good = br.aggregate([{**env_run(None), "scratch": {"arm_env": {"ZOE_X": "1"}}} for _ in range(3)], k=3, label="candidate")
    assert br.compare(ctl, good, declared=declared)["measurable"] is True
    for forged in ({"ZOE_X": 1}, {"ZOE_X": True}, ["ZOE_X"], "ZOE_X=1"):
        bad = br.aggregate([{**env_run(None), "scratch": {"arm_env": forged}} for _ in range(3)], k=3, label="candidate")
        assert br.compare(ctl, bad, declared=declared)["measurable"] is False, forged


def test_offline_compare_enforces_the_declared_override(tmp_path):
    """Review P1: `compare --admissible` on saved dirs must hold each arm to the override its identity files declared."""
    def arm_dir(name: str, recorded: dict, declared: dict) -> Path:
        d = tmp_path / name
        d.mkdir()
        for i in (1, 2, 3):
            (d / f"run-{i}.json").write_text(json.dumps(env_run(recorded)))
            (d / f"identity-{i}.json").write_text(json.dumps({"arm_env": declared}))
        return d

    quiet = lambda *_: None  # noqa: E731
    ctl = arm_dir("ctl", {}, {})
    ok = arm_dir("ok", FLAG, FLAG)
    ignored = arm_dir("ignored", {}, FLAG)          # asked for the flag, the checkout ignored it
    undeclared = arm_dir("undeclared", FLAG, {})    # recorded an override nobody declared
    assert br.main(["compare", "--control", str(ctl), "--candidate", str(ok), "--admissible"], log=quiet) == 0
    for bad in (ignored, undeclared):
        assert br.main(["compare", "--control", str(ctl), "--candidate", str(bad), "--admissible"], log=quiet) == 2


def test_offline_compare_of_saved_aggregates_with_custom_labels_still_enforces_the_declared_override(tmp_path):
    """Codex #2285: a saved aggregate keeps the label it was scored under (`score --label flag_on`, default `run`); the declared
    override is looked up by that label, so a custom label must not fall through to "nothing declared"."""
    quiet = lambda *_: None  # noqa: E731

    def saved(name: str, label: str, recorded: dict, declared: dict) -> Path:
        agg = br.aggregate([{**env_run(None), "scratch": {"arm_env": recorded}} for _ in range(3)], k=3, label=label)
        agg["arm_identity"] = {"arm_env": declared}
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(agg))
        return path

    ctl = saved("ctl", "baseline", {}, {})
    for label in ("flag_on", "run", "baseline"):          # "baseline" = the same label as the control's
        ok = saved(f"ok-{label}", label, FLAG, FLAG)
        ignored = saved(f"ignored-{label}", label, {}, FLAG)             # declared the flag, the run recorded none
        undeclared = saved(f"undeclared-{label}", label, FLAG, {})       # recorded an override nobody declared
        assert br.main(["compare", "--control", str(ctl), "--candidate", str(ok), "--admissible"], log=quiet) == 0, label
        for bad in (ignored, undeclared):
            assert br.main(["compare", "--control", str(ctl), "--candidate", str(bad), "--admissible"], log=quiet) == 2, label


def test_score_of_a_directory_carries_the_declaration_into_the_saved_aggregate(tmp_path):
    """Codex #2314 P1: `score DIR --out agg.json` used to drop the identity files, so comparing the saved aggregates inferred the
    declaration from the observation and passed an override the harness never applied."""
    quiet = lambda *_: None  # noqa: E731

    def arm_dir(name: str, recorded: dict, declared: dict) -> Path:
        d = tmp_path / name
        d.mkdir()
        for i in (1, 2, 3):
            (d / f"run-{i}.json").write_text(json.dumps(env_run(recorded)))
            (d / f"identity-{i}.json").write_text(json.dumps({"arm_env": declared}))
        return d

    def scored(d: Path, label: str) -> Path:
        out = tmp_path / f"{d.name}.agg.json"
        br.main(["score", str(d), "--label", label, "--out", str(out)], log=quiet)
        return out

    ctl = scored(arm_dir("ctl", {}, {}), "control")
    ok = scored(arm_dir("ok", FLAG, FLAG), "candidate")
    ignored = scored(arm_dir("ignored", {}, FLAG), "candidate")          # asked for the flag, the harness never applied it
    undeclared = scored(arm_dir("undeclared", FLAG, {}), "candidate")    # recorded an override nobody declared
    assert json.loads(ignored.read_text())["declared_arm_env"] == FLAG
    assert br.main(["compare", "--control", str(ctl), "--candidate", str(ok), "--admissible"], log=quiet) == 0
    for bad in (ignored, undeclared):
        assert br.main(["compare", "--control", str(ctl), "--candidate", str(bad), "--admissible"], log=quiet) == 2
