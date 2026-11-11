"""The DAYTIME REHEARSAL of the pendant re-pass (``night_window.sh --rehearse-repass``): the real stops, compaction and restore order with the real job on ONE recording, writing nothing.
Owner rule 2026-11-09: no night job's first run is unattended at night. Host-seam doubles from test_night_window.py.
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
from test_night_window import _private_locks, at, make, wake_order  # noqa: E402,F401

ARGV = ["--rehearse-repass"]


def stand_in(tmp_path):
    p = tmp_path / "zoe-omi-repass.py"
    p.write_text("# stand-in")
    return {"omi_repass_script": p}


def idle_box(host, busy=False):
    """The double answers night_busy.sh: rc 0 = voice work must pause."""
    orig = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        if argv and str(argv[0]).endswith("night_busy.sh"):
            host.cmds.append((list(argv), mutating))
            return nw.bk.Result(0 if busy else 1, "")
        return orig(argv, timeout, mutating, env)
    host.run = run


def test_the_rehearsal_stops_the_stack_runs_the_job_with_rehearse_on_one_recording_and_restores_in_order(tmp_path):
    w, host, cfg = make(tmp_path, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0))
    idle_box(host)
    assert cfg.rehearse and cfg.pre_jobs == ("omi_repass",) and cfg.cap_min <= 25.0 and not cfg.jobs and not cfg.fallback_4b
    assert w.run() == nw.EXIT_OK, w.outcome
    assert [j["name"] for j in host.jobs_run] == ["zoe-omi-repass.py"]                              # the one job; no 12B, no digest, no dreaming, no 4B fallback
    j = host.jobs_run[0]
    assert "--rehearse" in j["argv"] and "--budget-s" in j["argv"]
    assert host.idx("--user stop") < host.idx("zoe-omi-repass.py")                                  # asleep first (zoe-data, router, Kokoro, 4B)
    assert not any("--ctx-size" in c for c in host.joined())                                        # the 12B was never started
    assert wake_order(host) == list(nw.RESTORE_ORDER)                                              # the night's restore order
    assert any("pre-jobs done" in e["msg"] for e in w.rec["events"])                                # compacted and re-measured, like the night
    rec = next(r for r in w.jobs if r["name"] == "omi_repass")
    assert rec["backend"] == "pre" and rec["best_effort"] is False


def test_a_rehearsal_that_does_not_pass_fails_the_run_loudly_and_still_restores(tmp_path):
    w, host, cfg = make(tmp_path, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0), job_rc={"zoe-omi-repass.py": 4})
    idle_box(host)
    assert w.run() == nw.EXIT_JOBS_FAILED
    assert wake_order(host) == list(nw.RESTORE_ORDER)


def test_the_rehearsal_refuses_the_night_hours_and_a_busy_voice_gate_before_anything_is_stopped(tmp_path):
    for hh, mm in ((1, 50), (3, 0), (4, 54)):
        w, host, cfg = make(tmp_path / f"h{hh}{mm}", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(hh, mm))
        idle_box(host)
        assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0, (hh, mm)
    w, host, cfg = make(tmp_path / "busy", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0))
    idle_box(host, busy=True)
    assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0
    assert any("voice-day-ok" in e["msg"] for e in w.rec["events"])                                  # the refusal names the owner's switch
    (tmp_path / "ok").mkdir()
    w2, host2, _ = make(tmp_path / "ok", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(14, 0))        # control: the same box in the afternoon is allowed
    idle_box(host2)
    assert w2.run() == nw.EXIT_OK, w2.outcome


def test_without_the_switch_nothing_is_a_rehearsal(tmp_path):
    w, host, cfg = make(tmp_path, cfg_kw=stand_in(tmp_path))
    assert not cfg.rehearse and nw.build_pre_jobs(cfg) == [] and "--rehearse" not in " ".join(sum((j.argv for j in nw.build_pre_jobs(cfg)), []))
