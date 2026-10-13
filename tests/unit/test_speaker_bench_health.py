"""Speaker bake-off harness - benchmark health sampling, reuse safety and summaries (no ORT, no audio of value).

``scripts/perf/speaker_bakeoff.py`` times embedders on the panel Pi, which sits close to its firmware soft
temperature limit.  A pass the firmware throttled must never reach a summary, a failed embedding must never
look like a fast one, ``embed --reuse`` must never stand FP32 rows in for an INT8 variant, and a summary
must not merge runs with different thread counts.  The embedder and the Pi sensors are faked.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SR = 16000
_had_cuda = "CUDA_VISIBLE_DEVICES" in os.environ  # the harness hides CUDA at import; do not leak that


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bake = _load("speaker_bakeoff_health", "scripts/perf/speaker_bakeoff.py")
if not _had_cuda:
    os.environ.pop("CUDA_VISIBLE_DEVICES", None)


def _bench_args(**kw):
    return argparse.Namespace(**{"threads": 2, "max_temp_c": 0, "cool_wait_s": 0, "taint_temp_c": 82.0, **kw})


def _fake_model(monkeypatch, *, fail_on=None):
    """load_model -> a fake embedder: returns a vector, or None on call number ``fail_on``."""
    calls = {"n": 0}

    class F:
        last = [0.0, 0.0]

        def prep(self, w):
            return w

        def __call__(self, w):
            calls["n"] += 1
            self.last = [1.0, 2.0]
            return None if fail_on is not None and calls["n"] == fail_on else np.ones(4, np.float32)

    monkeypatch.setattr(bake, "load_model", lambda spec, threads, models_dir, scratch: (F(), {"runtime": "fake", "model_file": "x.onnx"}))
    return calls


def _clips(n=12):
    rng = np.random.default_rng(3)
    return [rng.standard_normal(int(SR * (1.0 + 0.2 * i))).astype(np.float32) for i in range(n)]


def _quiet_pi(monkeypatch, temp=60.0, bits=0):
    monkeypatch.setattr(bake, "throttled_now", lambda: bits)
    monkeypatch.setattr(bake, "read_temp_c", lambda: temp)


def test_a_mid_pass_throttle_taints_the_pass_even_when_the_end_snapshot_is_clean(monkeypatch, tmp_path):
    """NEGATIVE CONTROL for the throttle sampling: a one-sample throttle (the 4th health sample of 13, between
    the old every-tenth-clip checks and gone by the end) must taint the pass."""
    _fake_model(monkeypatch)
    state = {"k": 0}

    def throttled():
        state["k"] += 1
        return 0x8 if state["k"] == 4 else 0

    monkeypatch.setattr(bake, "throttled_now", throttled)
    monkeypatch.setattr(bake, "read_temp_c", lambda: 70.0)
    row = bake._bench_once("wespeaker_resnet34", _bench_args(), tmp_path, _clips(), 1)
    assert row["tainted"] is True and row["throttle_bits"] == 0x8
    _quiet_pi(monkeypatch, temp=70.0)
    assert bake._bench_once("wespeaker_resnet34", _bench_args(), tmp_path, _clips(), 1)["tainted"] is False


def test_reaching_the_soft_temperature_limit_taints_even_with_clean_bits(monkeypatch, tmp_path):
    _fake_model(monkeypatch)
    temps = iter([70.0] * 6 + [82.4] + [70.0] * 40)
    monkeypatch.setattr(bake, "throttled_now", lambda: 0)
    monkeypatch.setattr(bake, "read_temp_c", lambda: next(temps))
    row = bake._bench_once("wespeaker_resnet34", _bench_args(), tmp_path, _clips(), 1)
    assert row["tainted"] is True and row["temp_c_max"] >= 82.0
    assert bake.pass_tainted(0, 81.9) is False and bake.pass_tainted(0, 82.0) is True and bake.pass_tainted(0x4, None) is True


def test_a_failed_embedding_is_an_error_not_a_fast_timing(monkeypatch, tmp_path):
    _fake_model(monkeypatch, fail_on=8)  # the first clip and two warm-ups take calls 1 - 3, so call 8 is a timed clip
    _quiet_pi(monkeypatch)
    with pytest.raises(RuntimeError, match="embedding failed"):
        bake._bench_once("wespeaker_resnet34", _bench_args(), tmp_path, _clips(), 1)


def test_bench_row_records_the_effective_thread_count(monkeypatch, tmp_path):
    _fake_model(monkeypatch)
    _quiet_pi(monkeypatch)
    r1 = bake._bench_once("wespeaker_resnet34@intra=1", _bench_args(), tmp_path, _clips(), 1)
    r2 = bake._bench_once("wespeaker_resnet34", _bench_args(), tmp_path, _clips(), 1)
    assert (r1["threads"], r1["threads_global"], r2["threads"]) == (1, 2, 2)


def test_bench_summ_does_not_merge_runs_with_different_thread_counts(tmp_path, capsys):
    f = tmp_path / "b.jsonl"
    base = {"model": "m", "p50_ms": 100.0, "p95_ms": 200.0, "load_s": 0.1, "first_call_ms": 50.0, "rss_after_load_mb": 1.0, "peak_rss_mb": 2.0}
    rows = [{**base, "threads": 1, "p50_ms": 300.0}, {**base, "threads": 2}, {**base, "threads": 2, "p50_ms": 110.0}, {**base, "threads": 4, "tainted": True}]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    bake.cmd_bench_summ(argparse.Namespace(files=[str(f)]))
    out = capsys.readouterr().out
    assert "m [1 thr]" in out and "m [2 thr]" in out and "[4 thr]" not in out  # the tainted pass is dropped, nothing merged
    assert "| 300 |" in out and "| 105 |" in out


def test_embed_reuse_only_from_the_same_model_file_without_preprocessing():
    info = {"model_file": "rn34_int8.onnx"}
    ref = {"model": "wespeaker_resnet34@file=rn34_int8.onnx", "model_file": "rn34_int8.onnx"}
    assert bake.reuse_compatible(ref, info, "wespeaker_resnet34@file=rn34_int8.onnx,cap=3") == (True, "")
    ok, why = bake.reuse_compatible({"model": "wespeaker_resnet34", "model_file": "fp32.onnx"}, info, "wespeaker_resnet34@file=rn34_int8.onnx")
    assert not ok and "differs" in why  # FP32 rows must never stand in for an INT8 variant
    assert not bake.reuse_compatible({"model": "wespeaker_resnet34"}, info, "wespeaker_resnet34")[0]  # unrecorded file => refuse
    assert not bake.reuse_compatible({**ref, "model": "wespeaker_campp"}, info, "wespeaker_resnet34@file=rn34_int8.onnx")[0]
    ok, why = bake.reuse_compatible({**ref, "model": "wespeaker_resnet34@file=rn34_int8.onnx,cap=3"}, info, "wespeaker_resnet34@file=rn34_int8.onnx")
    assert not ok and "pre-processed" in why
