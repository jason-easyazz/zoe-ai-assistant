"""The night window's PRE-JOB phase (the pendant night re-pass, docs/knowledge/omi-night-repass.md): where it runs, how it is budgeted, and that the restore
still happens on every exit path. Uses the Host-seam doubles of test_night_window.py; the real step logic runs, nothing real is touched.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))

import night_window as nw  # noqa: E402
from test_night_window import _private_locks, at, make, wake_order  # noqa: E402,F401 - the window's own doubles and autouse lock fixture

PRE = ["--pre-jobs", "omi_repass"]


def stand_in(tmp_path):
    p = tmp_path / "zoe-omi-repass.py"
    p.write_text("# stand-in")
    return {"omi_repass_script": p}


def test_the_repass_runs_after_the_stops_and_before_the_12b_loads(tmp_path):
    w, host, _ = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path))
    assert w.run() == nw.EXIT_OK, w.outcome
    ran = [j["name"] for j in host.jobs_run]
    assert ran[0] == "zoe-omi-repass.py" and ran.index("zoe-omi-repass.py") < ran.index("zoe-nightly-dreaming.py")
    assert host.idx("--user stop") < host.idx("zoe-omi-repass.py") < host.idx("--ctx-size")           # asleep first, the 12B only after
    job_at = host.idx("zoe-omi-repass.py")
    assert host.idx("compact_memory", job_at) < host.idx("--ctx-size")                                  # a compaction AFTER the worker exited and BEFORE the 12B loads (resettle)
    rec = next(j for j in w.jobs if j["name"] == "omi_repass")
    assert rec["backend"] == "pre" and rec["status"] == "ok" and rec["best_effort"]


def test_control_without_pre_jobs_nothing_runs_before_the_12b(tmp_path):
    w, host, _ = make(tmp_path, cfg_kw=stand_in(tmp_path))
    assert w.run() == nw.EXIT_OK
    assert not any(j["name"] == "zoe-omi-repass.py" for j in host.jobs_run)


def test_the_repass_is_budgeted_and_leaves_the_12b_stage_its_share_of_the_window(tmp_path):
    w, host, cfg = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path))
    assert w.run() == nw.EXIT_OK
    j = host.jobs_run[0]
    budget = float(j["argv"][j["argv"].index("--budget-s") + 1])
    left_at_start = (cfg.cap_min - cfg.reserve_min) * 60.0
    assert j["timeout"] <= left_at_start - cfg.pre_keep_min * 60.0 + 1 and j["timeout"] <= 2400.0         # clipped so the 12B stage keeps its minutes
    assert budget == pytest.approx(max(30.0, j["timeout"] - nw.BUDGET_MARGIN_S), abs=1.0)


def test_a_slow_repass_that_times_out_never_fails_the_night_and_everything_is_restored(tmp_path):
    w, host, _ = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path), job_rc={"zoe-omi-repass.py": 124})
    assert w.run() == nw.EXIT_OK, w.outcome                                  # best effort: the unfinished recordings carry to tomorrow
    assert any(j["name"] == "omi_repass" and j["status"] == "timeout" for j in w.jobs)
    assert wake_order(host) == list(nw.RESTORE_ORDER)                         # restore ran, in order
    assert any(j["name"] == "digest" for j in w.jobs)                         # and the 12B stage still ran


def test_a_missing_repass_runtime_is_loud_but_still_restores(tmp_path):
    w, host, _ = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path), job_rc={"zoe-omi-repass.py": 3})
    rc = w.run()
    assert rc == nw.EXIT_JOBS_FAILED and wake_order(host) == list(nw.RESTORE_ORDER)


def test_a_killed_window_during_the_repass_still_restores(tmp_path):
    w, host, _ = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path))
    orig = host.run_watched

    def boom(argv, timeout, env, tick, log_path, interval=5.0):
        if "zoe-omi-repass.py" in " ".join(argv):
            raise nw.Aborted("signal 15")
        return orig(argv, timeout, env, tick, log_path, interval)

    host.run_watched = boom
    assert w.run() == nw.EXIT_ABORTED
    assert wake_order(host) == list(nw.RESTORE_ORDER)
    assert not any(j["backend"] == "12B" for j in w.jobs)             # the 12B stage never ran (a 4B fallback after the restore is the window's normal recovery)


def test_the_box_is_compacted_and_measured_again_after_a_long_repass(tmp_path):
    w, host, _ = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path))
    assert w.run() == nw.EXIT_OK
    assert any("pre-jobs done" in e["msg"] for e in w.rec["events"])


def test_an_unknown_pre_job_is_refused_at_configuration(tmp_path):
    with pytest.raises(nw.Refused):
        make(tmp_path, argv=["--pre-jobs", "nonsense"])


def test_the_dry_run_names_the_prejob(tmp_path):
    w, host, cfg = make(tmp_path, argv=PRE + ["--dry-run"], cfg_kw=stand_in(tmp_path))
    lines: list = []
    w.log = lines.append
    w.dry_plan()
    assert any("pre-job omi_repass" in line and "BEFORE the 12B loads" in line for line in lines)
