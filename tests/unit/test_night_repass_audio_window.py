"""The 12B's OWN audio as a re-pass backend (``gemma4-12b-audio``): the window keeps ``--mmproj`` (CPU) only for it, reserves its RAM, and runs the re-pass INSIDE the 12B stage after the load.
Host-seam doubles from test_night_window.py; nothing real is touched.
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
from test_night_window import PARKED, _private_locks, make  # noqa: E402,F401

PRE = ["--pre-jobs", "omi_repass"]


def stand_in(tmp_path):
    p = tmp_path / "zoe-omi-repass.py"
    p.write_text("# stand-in")
    return {"omi_repass_script": p}


def test_the_mmproj_is_added_only_for_the_audio_backend_and_stays_on_the_cpu():
    lv = nw.Levers("qat", 8192, "q8_0")
    off = nw.llm_spec(PARKED, nw.NightCfg(), lv)["argv"]
    assert "--mmproj" not in off and "--no-mmproj-offload" not in off                  # control: the 12B command is exactly what it was
    on = nw.llm_spec(PARKED, nw.NightCfg(audio_mmproj=True), lv)["argv"]
    assert on[on.index("--mmproj") + 1].endswith("mmproj-gemma-4-12b-it-qat-q4_0.gguf") and "--no-mmproj-offload" in on and "--jinja" in on
    # thinking off on the SERVER command line (the per-request kwargs alone returned only thought markers on b11194, trial 2026-11-09), after the build translation
    assert on[on.index("--chat-template-kwargs") + 1] == '{"enable_thinking":false}'
    assert "--chat-template-kwargs" not in off
    parked_b = nw.llm_spec(PARKED, nw.NightCfg(audio_mmproj=True, binary=None), lv)["argv"]
    assert parked_b.count("--chat-template-kwargs") == 1                                  # the parked (b9733) build keeps its own single copy
    q = nw.llm_spec(PARKED, nw.NightCfg(audio_mmproj=True), nw.Levers("q4km", 8192, "q8_0"))["argv"]
    assert q[q.index("--mmproj") + 1].endswith("mmproj-gemma-4-12B-it-Q8_0.gguf")


def test_the_lever_arithmetic_reserves_the_mmproj_and_the_worker_and_the_measured_night_still_fits():
    base, aud = nw.NightCfg(), nw.NightCfg(audio_mmproj=True)
    assert base.reserve_mib == base.job_reserve_mib and aud.reserve_mib == base.job_reserve_mib + aud.audio_extra_mib
    lv = nw.Levers("qat", 8192, "q8_0")
    size = 6_970_000_000
    n_base = nw.need_mib(size, lv, 1200.0, base.reserve_mib)["need_jobs"]
    n_aud = nw.need_mib(size, lv, 1200.0, aud.reserve_mib)["need_jobs"]
    assert n_aud - n_base == aud.audio_extra_mib
    assert 12637.0 - n_aud > 0, "the 2026-11-08 measured MemAvailable (12,637 MiB) still clears the 8k / q8_0 12B plus the audio reserve"


def test_the_repass_runs_inside_the_12b_stage_first_and_not_as_a_pre_job(tmp_path):
    w, host, cfg = make(tmp_path, argv=PRE + ["--audio-mmproj"], cfg_kw=stand_in(tmp_path))
    for k in ("qat", "q4km"):
        host.present.add(cfg.mmproj_path(k))
    assert cfg.audio_mmproj and nw.build_pre_jobs(cfg) == []
    assert nw.build_jobs(cfg, 8192, 6.2, 150.0)[0].name == "omi_repass"
    assert w.run() == nw.EXIT_OK, w.outcome
    ran = [j["name"] for j in host.jobs_run]
    assert ran[0] == "zoe-omi-repass.py" and ran.index("zoe-omi-repass.py") < ran.index("zoe-nightly-dreaming.py")
    assert host.idx("--ctx-size") < host.idx("zoe-omi-repass.py")                          # AFTER the 12B was started
    j = host.jobs_run[0]
    assert j["argv"][2:4] == ["--model-url", "http://127.0.0.1:11500/v1"] and "--budget-s" in j["argv"] and j["env"]["GEMMA_SERVER_URL"].endswith(":11500/v1")
    assert any("--mmproj" in c and "--no-mmproj-offload" in c for c in host.joined())
    rec = next(r for r in w.jobs if r["name"] == "omi_repass")
    assert rec["backend"] == "12B" and rec["best_effort"]


def test_control_without_the_audio_backend_the_repass_still_runs_before_the_12b(tmp_path):
    w, host, cfg = make(tmp_path, argv=PRE, cfg_kw=stand_in(tmp_path))
    assert not cfg.audio_mmproj and w.run() == nw.EXIT_OK
    assert host.idx("zoe-omi-repass.py") < host.idx("--ctx-size") and not any("--mmproj" in c for c in host.joined())


def test_a_missing_mmproj_file_refuses_before_anything_is_stopped(tmp_path):
    w, host, cfg = make(tmp_path, argv=PRE + ["--audio-mmproj"], cfg_kw=stand_in(tmp_path))
    assert w.run() == nw.EXIT_REFUSED                                                     # the double has no mmproj on disk
    assert not any(j["name"] == "zoe-omi-repass.py" for j in host.jobs_run) and host.count("--user stop") == 0      # nothing was stopped, nothing ran


def test_the_audio_mmproj_is_an_explicit_switch_and_the_window_reads_no_zoe_flag(tmp_path, monkeypatch):
    assert make(tmp_path, argv=PRE)[2].audio_mmproj is False                              # default: the 12B command is untouched
    assert make(tmp_path, argv=PRE + ["--audio-mmproj"])[2].audio_mmproj is True
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR", nw.AUDIO_ASR)
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "on")
    assert make(tmp_path, argv=PRE)[2].audio_mmproj is False                              # a zoe-data flag alone changes nothing here: the operator sets the window's switch too


def test_the_window_and_the_service_agree_on_the_backend_id():
    sys.path.insert(0, str(REPO / "services" / "zoe-data"))
    import omi_repass
    assert omi_repass.AUDIO_ASR == nw.AUDIO_ASR and omi_repass.BACKENDS[nw.AUDIO_ASR]["runs_on"] == "12b"
