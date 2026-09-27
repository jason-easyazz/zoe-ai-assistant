"""The voice probe's VAD stage — a real-model gate on the Silero VAD file.

Incident this closes (2026-09-26): /home/zoe/models/silero_vad.onnx was replaced
with a Silero v6.2.1 export that loaded without error but scored ~0.001 speech
probability on real speech, so barge-in / idle listening were silently off for a
day. The replay probe starts at STT and never ran VAD, so every gate stayed green.

voice_regression_probe.py now runs the service's real voice_vad over the newest
corpus clips and fails the run below a 60% speech-detection floor;
voice_gate_check.py blocks on a failed / missing VAD block. These tests drive the
stage with a FAKE VAD module (no numpy, no onnxruntime, no model) so they run in
the ci_safe lane, and carry the negative control the incident demands: a VAD that
returns 0.001 everywhere must turn the artifact FAIL and the gate BLOCKED.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import time
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("voice_regression_probe", "scripts/maintenance/voice_regression_probe.py")
vgc = _load("voice_gate_check", "scripts/maintenance/voice_gate_check.py")

LOUD, QUIET = 8000, 10   # int16 amplitudes the fake VAD reads as speech / not


# ── fakes ────────────────────────────────────────────────────────────────────
class _FakeStream:
    """Per-clip stream: peak prob follows amplitude (or a fixed value), so the
    stage's selection + counting is what is under test, not a model."""
    def __init__(self, fixed):
        self._fixed = fixed

    def process(self, frame: bytes) -> float:
        if self._fixed is not None:
            return self._fixed
        if len(frame) < 2:
            return 0.0
        amp = abs(int.from_bytes(frame[:2], "little", signed=True))
        return 0.9 if amp >= LOUD else 0.05


class _FakeVadModule:
    def __init__(self, model_path, *, fixed=None, threshold=0.5, create_none=False):
        self._path = str(model_path)
        self._fixed = fixed
        self._threshold = threshold
        self._create_none = create_none

    def _model_path(self):
        return self._path

    def speech_threshold(self):
        return self._threshold

    def create_vad(self):
        return None if self._create_none else _FakeStream(self._fixed)


def _write_wav(path: Path, amp: int, *, rate=16000, mtime=None, secs=0.2):
    n = int(rate * secs)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(amp.to_bytes(2, "little", signed=True) * n)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _corpus(root: Path, amps: list[int]) -> Path:
    """Clips in capture order (oldest first). Names are household-looking on
    purpose so the no-leak assertion below has something to catch."""
    d = root / "corpus"
    d.mkdir(parents=True)
    t0 = 1_700_000_000
    for i, amp in enumerate(amps):
        _write_wav(d / f"{120000 + i:06d}_{i:03d}_kitchen.wav", amp, mtime=t0 + i)
    return d


@pytest.fixture
def model(tmp_path):
    m = tmp_path / "silero_vad.onnx"
    m.write_bytes(b"fake-onnx")
    return m


# ── the stage ────────────────────────────────────────────────────────────────
def test_healthy_vad_passes_with_aggregate_fields(tmp_path, model):
    d = _corpus(tmp_path, [LOUD] * 10)
    block = vrp.run_vad_check("/unused", str(d), 10, vad_mod=_FakeVadModule(model))
    assert block["status"] == "pass"
    assert block["clips"] == 10 and block["speech_detected"] == 10
    assert block["pass_frac"] == 1.0 and block["min_pass_frac"] == 0.60
    assert block["threshold"] == 0.5
    assert block["min_max_prob"] == [0.9, 0.9]
    assert block["model"] == {"path": str(model),
                              "md5": hashlib.md5(b"fake-onnx").hexdigest()}


def test_below_sixty_percent_fails_and_boundary_passes(tmp_path, model):
    fake = _FakeVadModule(model)
    d5 = _corpus(tmp_path / "a", [LOUD] * 5 + [QUIET] * 5)
    b5 = vrp.run_vad_check("/unused", str(d5), 10, vad_mod=fake)
    assert b5["status"] == "fail", b5
    assert (b5["clips"], b5["speech_detected"]) == (10, 5)
    assert "5/10" in b5["reason"] and "60%" in b5["reason"]

    d6 = _corpus(tmp_path / "b", [LOUD] * 6 + [QUIET] * 4)
    b6 = vrp.run_vad_check("/unused", str(d6), 10, vad_mod=fake)
    assert b6["status"] == "pass", b6    # exactly 60% reaches the floor


def test_negative_control_dead_model_fails(tmp_path, model):
    """THE INCIDENT: a model that loads fine and returns ~0.001 on everything."""
    d = _corpus(tmp_path, [LOUD] * 12)
    block = vrp.run_vad_check("/unused", str(d), 12, vad_mod=_FakeVadModule(model, fixed=0.001))
    assert block["status"] == "fail"
    assert block["speech_detected"] == 0 and block["clips"] == 12
    assert block["min_max_prob"] == [0.001, 0.001]
    assert "not detecting real speech" in block["reason"]


def test_model_absent_skips_with_a_reason(tmp_path):
    d = _corpus(tmp_path, [LOUD] * 10)
    missing = tmp_path / "nope" / "silero_vad.onnx"
    block = vrp.run_vad_check("/unused", str(d), 10, vad_mod=_FakeVadModule(missing))
    assert block["status"] == "skip"
    assert "not present" in block["reason"] and str(missing) in block["reason"]
    assert block["clips"] == 0


def test_model_present_but_create_vad_none_fails(tmp_path, model):
    """The service's graceful degradation (None -> RMS fallback) is exactly the
    silent-off failure; with the file present it is a FAIL, not a skip."""
    d = _corpus(tmp_path, [LOUD] * 10)
    block = vrp.run_vad_check("/unused", str(d), 10,
                              vad_mod=_FakeVadModule(model, create_none=True))
    assert block["status"] == "fail"
    assert "create_vad() returned None" in block["reason"]


def test_newest_clips_are_selected_and_off_format_skipped(tmp_path, model):
    """Newest-by-capture-time, top-level only, off-format members skipped: 10
    OLD quiet clips, then 8 NEW loud ones, then a 24 kHz resample and a non-RIFF
    file newest of all, plus a newer quarantined clip — the stage must score
    exactly the 8 new loud clips."""
    d = _corpus(tmp_path, [QUIET] * 10 + [LOUD] * 8)
    _write_wav(d / "235959_999.wav", QUIET, rate=24000, mtime=1_800_000_000)
    bad = d / "235959_998.wav"
    bad.write_bytes(b"not a riff file")
    os.utime(bad, (1_800_000_001, 1_800_000_001))
    q = d / "quarantine-x"
    q.mkdir()
    _write_wav(q / "999999_000.wav", QUIET, mtime=1_900_000_000)
    block = vrp.run_vad_check("/unused", str(d), 8, vad_mod=_FakeVadModule(model))
    assert (block["status"], block["clips"], block["speech_detected"]) == ("pass", 8, 8)


def test_thin_corpus_skips(tmp_path, model):
    d = _corpus(tmp_path, [LOUD] * 3)
    block = vrp.run_vad_check("/unused", str(d), 24, vad_mod=_FakeVadModule(model))
    assert block["status"] == "skip" and "too thin" in block["reason"]


def test_block_carries_no_household_data(tmp_path, model):
    d = _corpus(tmp_path, [LOUD] * 5 + [QUIET] * 5)
    for fake in (_FakeVadModule(model), _FakeVadModule(model, fixed=0.001)):
        blob = json.dumps(vrp.run_vad_check("/unused", str(d), 10, vad_mod=fake))
        assert "kitchen" not in blob and ".wav" not in blob and str(d) not in blob


def test_service_voice_vad_import_failure_is_an_error(tmp_path):
    block = vrp.run_vad_check(str(tmp_path / "no-service"), str(tmp_path), 10)
    assert block["status"] == "error" and "voice_vad" in block["reason"]


# ── status folding: the VAD verdict only ever tightens ──────────────────────
@pytest.mark.parametrize("status,vad,expected", [
    ("pass", {"status": "pass"}, "pass"),
    ("pass", {"status": "skip"}, "pass"),
    ("pass", {"status": "fail"}, "fail"),
    ("pass", {"status": "error"}, "error"),
    ("skip", {"status": "fail"}, "fail"),     # a known failure beats "did not look"
    ("skip", {"status": "pass"}, "skip"),     # a VAD pass never upgrades a skip
    ("fail", {"status": "pass"}, "fail"),     # ...or a replay fail
    ("error", {"status": "fail"}, "error"),
])
def test_fold_vad_status(status, vad, expected):
    assert vrp.fold_vad_status(status, vad) == expected


# ── end to end through main(): the artifact + the gate ──────────────────────
def _drive_main(tmp_path, monkeypatch, fake, *, avail_mb=10_000, extra=()):
    monkeypatch.setattr(vrp, "_acquire_harness_lock", lambda: None)
    monkeypatch.setattr(vrp, "mem_available_mb", lambda: avail_mb)
    monkeypatch.setattr(vrp, "cleanup_replay_artifacts", lambda *a, **k: True)
    monkeypatch.setattr(vrp, "service_revision", lambda *_: None)
    report = {"n_samples": 4, "verdicts": {"OK": 4},
              "aggregate_ms": {"stt_ms": {"median": 100}, "brain_ms": {"median": 200},
                               "e2e_ms": {"median": 300}}}
    monkeypatch.setattr(vrp, "run_measure", lambda *a, **k: report)
    monkeypatch.setattr(vrp, "_load_service_vad", lambda _sd: fake)
    monkeypatch.delenv("ZOE_VOICE_PROBE_MIN_MEM_MB", raising=False)
    corpus = _corpus(tmp_path, [LOUD] * 12)
    results = tmp_path / "last.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"created_at": "2026-09-01T00:00:00Z",
                                    "summary": vrp.summarize(report)}))
    monkeypatch.setattr(sys, "argv", [
        "probe", "--results", str(results), "--baseline", str(baseline),
        "--trend", str(tmp_path / "trend.jsonl"), "--service-dir", str(tmp_path),
        "--sample-dir", str(corpus), "--vad-clips", "12", "--no-cleanup", *extra])
    rc = vrp.main()
    return rc, json.loads(results.read_text()), json.loads(baseline.read_text())


def _gate(payload, baseline):
    return vgc.evaluate(payload, now_epoch=time.time(), max_age_s=24 * 3600, baseline=baseline)


def test_main_healthy_vad_artifact_passes_the_gate(tmp_path, monkeypatch, model):
    rc, art, base = _drive_main(tmp_path, monkeypatch, _FakeVadModule(model))
    assert rc == 0 and art["status"] == "pass"
    assert art["vad_stage"] is True and art["vad"]["status"] == "pass"
    ok, why = _gate(art, base)
    assert ok, why
    assert "VAD 12/12" in why


def test_main_dead_vad_turns_the_artifact_fail_and_blocks_the_gate(tmp_path, monkeypatch, model):
    """NEGATIVE CONTROL end to end: the replay is perfect, the VAD returns 0.001
    everywhere -> the run is FAIL (exit 1), the reason names the VAD, and the
    deploy/PR gate blocks."""
    rc, art, base = _drive_main(tmp_path, monkeypatch, _FakeVadModule(model, fixed=0.001))
    assert rc == 1
    assert art["status"] == "fail"
    assert "VAD FAIL" in art["reason"]
    assert art["summary"]["ok_rate"] == 1.0          # the replay itself was clean
    ok, why = _gate(art, base)
    assert ok is False and "NOT a pass" in why


def test_main_dead_vad_fails_even_the_memory_skip_path(tmp_path, monkeypatch, model):
    """A box too tight for the replay (how the incident stayed invisible) must
    still report a dead VAD: status fail, not skip."""
    rc, art, _ = _drive_main(tmp_path, monkeypatch, _FakeVadModule(model, fixed=0.001),
                             avail_mb=500)
    assert rc == 1 and art["status"] == "fail" and art["vad"]["status"] == "fail"


def test_main_memory_skip_with_healthy_vad_stays_skip(tmp_path, monkeypatch, model):
    rc, art, _ = _drive_main(tmp_path, monkeypatch, _FakeVadModule(model), avail_mb=500)
    assert rc == 0 and art["status"] == "skip" and art["vad"]["status"] == "pass"


def test_main_model_absent_skips_the_stage_not_the_run(tmp_path, monkeypatch):
    rc, art, base = _drive_main(tmp_path, monkeypatch,
                                _FakeVadModule(tmp_path / "missing.onnx"))
    assert rc == 0 and art["status"] == "pass"
    assert art["vad"]["status"] == "skip" and "not present" in art["vad"]["reason"]
    ok, why = _gate(art, base)
    assert ok and "VAD stage skipped" in why


def test_main_no_vad_check_records_a_disabled_skip(tmp_path, monkeypatch, model):
    rc, art, _ = _drive_main(tmp_path, monkeypatch, _FakeVadModule(model, fixed=0.001),
                             extra=("--no-vad-check",))
    assert rc == 0 and art["status"] == "pass"
    assert art["vad"]["status"] == "skip" and "disabled" in art["vad"]["reason"]


def test_probe_chain_still_does_not_name_the_webrtc_lane():
    """The VAD stage runs voice_vad, NOT the WebRTC ingest code; the ingest-lane
    evidence statement in voice_gate_check.py depends on the probe source not
    naming that lane (test_voice_gate_check pins it; re-asserted here so a VAD
    edit that trips it fails next to its cause)."""
    src = (REPO / "scripts/maintenance/voice_regression_probe.py").read_text().lower()
    assert not any(tok in src for tok in ("livekit", "webrtc", "aiortc"))
