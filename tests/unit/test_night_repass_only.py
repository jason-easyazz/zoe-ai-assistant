"""``night_window.sh --repass-only``: the REAL pendant re-pass by day (ZOE_OMI_NIGHT_REPASS honoured), under the night's stops / compaction / restore order, with NO 12B load and no 12B job.
Host-seam doubles from test_night_window.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))

import night_window as nw  # noqa: E402
from test_night_repass_rehearsal import idle_box, stand_in  # noqa: E402
from test_night_window import _private_locks, at, make, wake_order  # noqa: E402,F401

ARGV = ["--repass-only"]


def test_repass_only_runs_the_real_job_never_loads_the_12b_and_restores_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "shadow")
    w, host, cfg = make(tmp_path, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0))
    idle_box(host)
    loaded = []
    monkeypatch.setattr(w, "load_12b_with_retry", lambda pick: loaded.append(pick))
    assert cfg.repass_only and not cfg.rehearse and cfg.exploratory and cfg.pre_jobs == ("omi_repass",) and cfg.cap_min <= 25.0 and not cfg.jobs and not cfg.fallback_4b
    assert w.run() == nw.EXIT_OK, w.outcome
    assert loaded == [], "--repass-only must never reach load_12b_with_retry"
    assert not any("--ctx-size" in c for c in host.joined())                       # no llama-server for the 12B
    j = host.jobs_run[0]
    assert [x["name"] for x in host.jobs_run] == ["zoe-omi-repass.py"] and "--rehearse" not in j["argv"] and "--budget-s" in j["argv"]   # REAL: no --rehearse
    assert host.idx("--user stop") < host.idx("zoe-omi-repass.py")
    assert wake_order(host) == list(nw.RESTORE_ORDER)


def test_repass_only_refuses_the_night_hours_and_any_busy_gate_before_anything_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "shadow")
    for hh, mm in ((1, 50), (3, 0), (4, 54)):
        w, host, cfg = make(tmp_path / f"h{hh}{mm}", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(hh, mm))
        idle_box(host)
        assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0
    for kind in ("voice", "deploy", "test"):           # each gate alone refuses
        w, host, cfg = make(tmp_path / kind, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0))
        orig = host.run

        def run(argv, timeout=60.0, mutating=True, env=None, _k=kind, _o=orig, _h=host):
            if argv and str(argv[0]).endswith("night_busy.sh"):
                return nw.bk.Result(0 if argv[1] == _k else 1, "")
            return _o(argv, timeout, mutating, env)
        host.run = run
        assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0, kind


def test_repass_only_with_the_flag_off_is_refused_and_it_is_its_own_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    w, host, cfg = make(tmp_path, argv=ARGV, cfg_kw={**stand_in(tmp_path), "repass_preflight": True}, start=at(11, 0))
    idle_box(host)
    assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0
    with pytest.raises(nw.Refused):
        nw.configure(nw.build_parser().parse_args(["--repass-only", "--trial"]))
