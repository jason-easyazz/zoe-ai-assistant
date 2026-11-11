"""The round-2 winners in the seam (qwen3-asr default, parakeet-tdt fallback), the GPU recipe, the low-memory refusal and the daytime REHEARSAL (writes nothing).
Synthetic data only; the worker is a fake runner / fake model objects.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import importlib.util
import json
from pathlib import Path

import numpy as np

import omi_repass as rp
from test_omi_recordings import env  # noqa: F401
from test_omi_repass import fake_runner, first_pass, renv, result_for, rows, run, transcribe  # noqa: F401

WORKER = Path(__file__).resolve().parents[3] / "scripts/night/jobs/omi_repass_worker.py"


def _worker():
    spec = importlib.util.spec_from_file_location("omi_repass_worker_gpu", WORKER)
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    return w


def test_the_default_is_qwen3_with_parakeet_as_the_budget_fallback_and_the_measured_budget_is_declared():
    assert rp.DEFAULT_ASR == "qwen3-asr" and rp.BACKENDS["qwen3-asr"]["fallback"] == "parakeet-tdt"
    q, p = rp.BACKENDS["qwen3-asr"], rp.BACKENDS["parakeet-tdt"]
    assert (q["gpu_rtf"], q["peak_rss_mb"], q["gpu_peak_mb"]) == (0.42, 8700, 4000) and (p["gpu_rtf"], p["peak_rss_mb"]) == (0.08, 5700)
    assert rp.BACKENDS["faster-whisper:large-v3"]["device"] == "cpu"                               # whisper is the CPU last resort only
    src = WORKER.read_text()
    for name in rp.BACKENDS:
        assert f'"{name}"' in src, name


def test_the_ram_check_refuses_a_pass_the_box_cannot_hold_above_the_floor(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    out = run(rp.run_night(budget_s=600, mem_available=lambda: 3000.0))                          # the real worker would be spawned if it passed: it does not
    assert out["status"] == "low_mem" and out["need_mb"] == rp.need_mb("qwen3-asr") == 8700 + 400 + 1200 and out["have_mb"] == 3000
    assert rows(renv)[0]["repass_state"] == "pending" and rows(renv)[0]["repass_attempts"] == 0   # carried untouched
    ok = run(rp.run_night(budget_s=600, runner=fake_runner(), mem_available=lambda: 3000.0))      # control: an injected runner skips the check; with RAM the real path proceeds
    assert ok["status"] == "ok"


def test_the_gpu_recipe_compacts_immediately_before_the_worker_starts_and_uses_the_default_allocator(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(rp, "gpu_prep", lambda: calls.append("prep"))

    class P:
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(("run", kw["env"].get("PYTORCH_CUDA_ALLOC_CONF"), "systemd-run" in cmd[0]))
        return P()

    monkeypatch.setattr(rp.subprocess, "run", fake_run)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "backend:cudaMallocAsync")
    assert rp.spawn_worker(tmp_path / "m.json", tmp_path / "o.jsonl", 60.0, mem_max="11G", threads=2) == 0
    assert calls == ["prep", ("run", None, True)]


def test_gpu_prep_is_the_drop_caches_plus_compact_command(monkeypatch):
    seen: list = []
    monkeypatch.setattr(rp.subprocess, "run", lambda cmd, **kw: seen.append(cmd))
    rp.gpu_prep()
    assert seen[0][:3] == ["sudo", "-n", "sh"] and "echo 3 > /proc/sys/vm/drop_caches" in seen[0][4] and "echo 1 > /proc/sys/vm/compact_memory" in seen[0][4]


def test_parakeet_decodes_the_output_objects_sequences_and_qwen_strips_the_prompt():
    w = _worker()

    class Inp(dict):
        def to(self, *a, **k):
            return self

    class Out:
        sequences = "SEQ"

    class Proc:
        def __call__(self, x, **k):
            return Inp(input_values=1)

        def batch_decode(self, seq, **k):
            assert seq == "SEQ"                                         # generate() returned an OBJECT: its .sequences are decoded
            return [" hello there "]

    class Model:
        def generate(self, **k):
            return Out()

    pk = w.ParakeetTdt.__new__(w.ParakeetTdt)
    pk.p, pk.m, pk.dev = Proc(), Model(), "cpu"

    class T:
        def inference_mode(self):
            import contextlib
            return contextlib.nullcontext()

    pk.torch = T()
    assert pk.run_chunk(np.zeros(16000, dtype="float32")) == "hello there"


def test_transformers_engines_are_not_prompt_biased_and_words_are_spread_over_each_chunk(monkeypatch):
    w = _worker()
    monkeypatch.setattr(w, "vad_chunks", lambda x: [(1.0, 3.0), (5.0, 9.0)])
    got_hints: list = []

    class Fake(w.ChunkedAsr):
        def run_chunk(self, x):
            return "one  two" if len(x) == 32000 else "a b c d"

        def transcribe(self, x, hint, deadline):
            got_hints.append(hint)
            return super().transcribe(x, hint, deadline)

    be = Fake(4, True)
    words, late = be.transcribe(np.zeros(16000 * 10, dtype="float32"), "People who may be mentioned: Billie.", None)
    assert not late and [x[0].strip() for x in words] == ["one", "two", "a", "b", "c", "d"] and all(1.0 <= x[1] < x[2] <= 9.0 for x in words)
    assert w.ChunkedAsr.run_chunk.__code__.co_varnames[:2] == ("self", "x")                       # the engine call takes audio only: no hint reaches it
    assert be.transcribe(np.zeros(16000 * 10, dtype="float32"), "", 1.0) == ([], True)


def test_billy_is_normalised_to_the_household_spelling_after_the_engine(renv, monkeypatch):
    assert rp.normalise_names("Billy said hello to Zoe", ["Billie", "Zoe"]) == "Billie said hello to Zoe"
    assert rp.normalise_names("Billy said hello", []) == "Billy said hello"                      # no such person in the lexicon: untouched


# ── the daytime rehearsal ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _kept_recording(renv, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO", "1")
    r = transcribe(first_pass(renv) and renv, monkeypatch, "first words")
    assert r["audio_path"] and r["repass_state"] == ""
    return r


def test_the_rehearsal_runs_the_real_path_on_one_recording_with_the_flag_off_and_writes_nothing(renv, monkeypatch, tmp_path):
    r0 = _kept_recording(renv, monkeypatch)
    seen: list = []
    out = run(rp.run_night(budget_s=600, runner=fake_runner(seen=seen), rehearse=True))
    assert out["status"] == "rehearsal_ok" and out["rehearse"] and out["words"] == 3 and out["model"] == "qwen3-asr" and out["worker_rtf"] == 0.5
    assert len(seen[0]["items"]) == 1 and seen[0]["asr"] == "qwen3-asr" and seen[0]["gpu"] is True and seen[0]["models_dir"]
    after = rows(renv)[0]                                                                         # NOTHING changed: text, state, audio, segments, state file, shadow log
    assert after["transcript"] == "first words" and after["repass_state"] == "" and after["repass_attempts"] == 0 and Path(after["audio_path"]).exists()
    assert renv.db.rows("SELECT COUNT(*) AS n FROM omi_segments")[0]["n"] == 0 and renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples")[0]["n"] == 0
    assert not (tmp_path / "repass" / "state.json").exists() and not (tmp_path / "repass" / "shadow.jsonl").exists()
    assert "Hello" not in json.dumps(out)                                                         # counts only, never a word


def test_the_rehearsal_picks_one_recording_and_reports_a_failed_worker_loudly(renv, monkeypatch):
    _kept_recording(renv, monkeypatch)
    rid = rows(renv)[0]["id"]
    bad = {rid: {"id": rid, "ok": False, "why": "error:ValueError"}}
    out = run(rp.run_night(budget_s=600, runner=fake_runner(bad), rehearse=True))
    assert out["status"] == "rehearsal_failed" and out["why"] == "error:ValueError"
    assert rows(renv)[0]["repass_attempts"] == 0                                                  # a rehearsal never counts an attempt against a recording
    assert rp.pick_rehearsal([{"id": "a", "duration_s": 5, "started_at": "1"}, {"id": "b", "duration_s": 60, "started_at": "2"}, {"id": "c", "duration_s": 90, "started_at": "3"}])[0]["id"] == "c"
    assert rp.pick_rehearsal([{"id": "a", "duration_s": 5, "started_at": "1"}], "")[0]["id"] == "a" and rp.pick_rehearsal([{"id": "a"}], "zzz") == []


def test_control_the_real_run_with_the_flag_off_does_nothing_but_the_rehearsal_does_not_need_the_flag(renv, monkeypatch):
    _kept_recording(renv, monkeypatch)
    assert run(rp.run_night(budget_s=600, runner=fake_runner()))["status"] == "flag_off"
    assert run(rp.run_night(budget_s=600, runner=fake_runner(), rehearse=True))["status"] == "rehearsal_ok"
