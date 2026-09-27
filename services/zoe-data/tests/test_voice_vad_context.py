"""Silero VAD input contract: 512-sample hop + 64 samples of context = 576.

Upstream ``silero_vad.utils_vad.OnnxWrapper.__call__`` (silero-vad 6.2.1,
``src/silero_vad/utils_vad.py`` L66-87) prepends the previous call's last 64
samples to every 512-sample hop at 16 kHz and feeds the model 576 samples; the
context starts as zeros and ``reset_states()`` clears it. The v6 models are
calibrated for that input. Until 2026-09-27 ``voice_vad.py`` fed bare 512-sample
hops: v6.0 degraded (corpus speech-hop fraction 0.09 vs 0.36 with context,
~128 ms median onset lag vs 0) and v6.2.1 scored ~0.002 on real speech — the
"v6.2.1 is incompatible" verdict of 2026-09-26 was this loader bug.

Two layers:

1. ``ci_safe`` — a FAKE ONNX session records exactly what the wrapper feeds the
   model and pins it against a numpy port of the upstream algorithm (shape,
   context carry-over across arbitrary frame sizes, reset, per-stream isolation).
   numpy only; no model, no network.
2. Host-only (skipped when /home/zoe/models/silero_vad.onnx is absent, e.g. on
   GitHub runners) — the REAL model on a deterministic synthetic voiced clip must
   detect speech from its first voiced hop and hold it; an in-suite negative
   control strips the context BY CONSTRUCTION (a session wrapper that drops the
   first 64 input samples = the pre-fix input) and requires the same assertions
   to fail, so the test cannot decay into always-green.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

import voice_vad

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: validate.yml's `-m ci_safe` lane

_MODEL_PATH = "/home/zoe/models/silero_vad.onnx"


# ── 1. Input contract against a fake session (ci_safe) ───────────────────────

class _RecordingSession:
    """Stands in for the ONNX session; records every feed dict it is given."""

    def __init__(self, prob: float = 0.1) -> None:
        self.inputs: list[np.ndarray] = []
        self.prob = prob

    def run(self, _outputs, feeds):
        self.inputs.append(np.array(feeds["input"], copy=True))
        assert feeds["state"].shape == (2, 1, 128)
        return [np.array([[self.prob]], dtype=np.float32), feeds["state"] + 1.0]


def _upstream_inputs(samples: np.ndarray) -> list[np.ndarray]:
    """numpy port of upstream OnnxWrapper.__call__'s input assembly at 16 kHz:
    context = zeros(64); per 512-sample chunk x = cat(context, chunk);
    context = x[..., -64:]."""
    context = np.zeros((1, 64), dtype=np.float32)
    out = []
    for i in range(0, len(samples) - 511, 512):
        x = np.concatenate([context, samples[i:i + 512].reshape(1, 512)], axis=1)
        out.append(x)
        context = x[..., -64:]
    return out


def _ramp_pcm(n_samples: int, seed: int = 0) -> tuple[bytes, np.ndarray]:
    """Distinct int16 samples (so a wrong slice can't coincidentally match)."""
    rng = np.random.default_rng(seed)
    pcm = rng.integers(-20000, 20000, size=n_samples, dtype=np.int16)
    return pcm.tobytes(), pcm.astype(np.float32) / 32768.0


def _feed(vad, raw: bytes, frame_bytes: int) -> list:
    probs = []
    for i in range(0, len(raw), frame_bytes):
        probs += vad.process_hops(raw[i:i + frame_bytes])
    return probs


def test_constants_pin_576_sample_model_input():
    assert voice_vad.HOP_SAMPLES == 512
    assert voice_vad.CONTEXT_SAMPLES == 64
    assert voice_vad.MODEL_INPUT_SAMPLES == 576


@pytest.mark.parametrize("frame_bytes", [640, 1024, 274, 4096])
def test_model_input_matches_upstream_onnxwrapper(frame_bytes):
    """Every call feeds (1, 576) float32 = [previous hop's last 64 | this hop],
    identical to upstream's assembly, whatever the incoming frame size."""
    raw, samples = _ramp_pcm(512 * 5 + 100)
    sess = _RecordingSession()
    vad = voice_vad.SileroVAD(sess)

    probs = _feed(vad, raw, frame_bytes)

    expected = _upstream_inputs(samples)
    assert len(probs) == len(expected) == 5
    assert len(sess.inputs) == 5
    for got, want in zip(sess.inputs, expected):
        assert got.shape == (1, 576)
        assert got.dtype == np.float32
        np.testing.assert_array_equal(got, want)
    # First call's context is zeros; later calls carry the previous hop's tail.
    assert not sess.inputs[0][0, :64].any()
    np.testing.assert_array_equal(sess.inputs[2][0, :64], samples[1024 - 64:1024])


def test_reset_clears_context_and_state():
    raw, samples = _ramp_pcm(512 * 2)
    sess = _RecordingSession()
    vad = voice_vad.SileroVAD(sess)
    _feed(vad, raw, 640)
    assert sess.inputs[1][0, :64].any()

    vad.reset()
    _feed(vad, raw[: 512 * 2], 1024)
    after_reset = sess.inputs[2]
    assert not after_reset[0, :64].any(), "reset() must zero the 64-sample context"
    np.testing.assert_array_equal(after_reset[0, 64:], samples[:512])


def test_context_is_per_stream():
    """Two streams sharing one session must not leak context into each other."""
    raw_a, sam_a = _ramp_pcm(512 * 2, seed=1)
    raw_b, sam_b = _ramp_pcm(512 * 2, seed=2)
    sess = _RecordingSession()
    a = voice_vad.SileroVAD(sess)
    b = voice_vad.SileroVAD(sess)

    a.process_hops(raw_a[:1024])   # a hop 1
    b.process_hops(raw_b[:1024])   # b hop 1
    a.process_hops(raw_a[1024:])   # a hop 2 — context must be a's hop-1 tail
    b.process_hops(raw_b[1024:])   # b hop 2 — context must be b's hop-1 tail

    np.testing.assert_array_equal(sess.inputs[2][0, :64], sam_a[512 - 64:512])
    np.testing.assert_array_equal(sess.inputs[3][0, :64], sam_b[512 - 64:512])


# ── 2. Real model on a synthetic voiced clip (host-only) ─────────────────────

_needs_model = pytest.mark.skipif(
    not os.path.isfile(_MODEL_PATH), reason=f"Silero model not present at {_MODEL_PATH}"
)

_LEAD_HOPS = 10   # 320 ms of near-silence before the voice starts (hop-aligned)
_VOICED_HOPS = 40  # 1.28 s voiced


def _synthetic_vowel(f0: float, seed: int = 0) -> bytes:
    """Deterministic /a/-like vowel: harmonics of a vibrato f0 shaped by three
    formant bumps (730/1090/2440 Hz), a 4 Hz syllabic envelope, framed by
    near-silence. numpy only (no scipy), int16 @ 16 kHz."""
    sr = 16000
    t = np.arange(_VOICED_HOPS * 512) / sr
    f = f0 * (1 + 0.03 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(f) / sr
    y = np.zeros_like(t)
    for k in range(1, int(4000 / f0)):
        h = k * f0
        gain = sum(np.exp(-0.5 * ((h - fc) / bw) ** 2)
                   for fc, bw in ((730, 120), (1090, 150), (2440, 220)))
        y += (gain + 0.02) / k * np.sin(k * phase)
    y *= 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t - np.pi / 2)
    y = y / np.abs(y).max() * 0.3
    total = (_LEAD_HOPS + _VOICED_HOPS + 10) * 512
    a = np.zeros(total)
    a[_LEAD_HOPS * 512:(_LEAD_HOPS + _VOICED_HOPS) * 512] = y
    a += np.random.default_rng(seed).standard_normal(total) * 0.002
    return (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes()


class _StripContext:
    """Negative control: hands the model the PRE-FIX input (bare 512-sample hop)
    by dropping the 64 context samples the wrapper prepended."""

    def __init__(self, session) -> None:
        self._s = session

    def run(self, outputs, feeds):
        feeds = dict(feeds)
        feeds["input"] = feeds["input"][:, voice_vad.CONTEXT_SAMPLES:]
        return self._s.run(outputs, feeds)


@pytest.fixture
def real_session(monkeypatch):
    monkeypatch.delenv("ZOE_SILERO_VAD_MODEL", raising=False)
    monkeypatch.setattr(voice_vad, "_session", None)
    monkeypatch.setattr(voice_vad, "_session_failed", False)
    monkeypatch.setattr(voice_vad, "_warned", False)
    sess = voice_vad._get_session()
    assert sess is not None
    return sess


def _synthetic_verdict(session, f0: float) -> tuple[bool, str]:
    """(ok, detail) for the contract the fixed loader must meet on the vowel:
    detection within 2 hops (64 ms) of the voiced onset, >= 90% of voiced hops
    at the 0.5 speech threshold, and the lead-in silence below 0.2."""
    vad = voice_vad.SileroVAD(session)
    probs = np.array(_feed(vad, _synthetic_vowel(f0), 640))
    hits = np.nonzero(probs >= 0.5)[0]
    onset = int(hits[0]) if hits.size else None
    voiced = probs[_LEAD_HOPS:_LEAD_HOPS + _VOICED_HOPS]
    frac = float(np.mean(voiced >= 0.5))
    lead = float(probs[:_LEAD_HOPS].max())
    ok = (onset is not None and onset <= _LEAD_HOPS + 2 and frac >= 0.9 and lead < 0.2)
    return ok, f"f0={f0} onset_hop={onset} (voice starts {_LEAD_HOPS}) voiced_frac={frac:.2f} lead_max={lead:.2f}"


# Measured 2026-09-27 on the live v6.0 file (md5 00bdd414…): with context every
# f0 gives onset hop 10 (0 ms lag), voiced_frac 1.00, lead 0.04-0.06; the bare
# 512-sample input gives onset hop 15-16, voiced_frac 0.33-0.57, lead 0.25-0.36.
_F0S = (130.0, 180.0, 220.0)


@_needs_model
@pytest.mark.parametrize("f0", _F0S)
def test_real_model_detects_synthetic_voice_from_first_hops(real_session, f0):
    ok, detail = _synthetic_verdict(real_session, f0)
    assert ok, detail


@_needs_model
@pytest.mark.parametrize("f0", _F0S)
def test_negative_control_bare_hops_fail_the_same_contract(real_session, f0):
    """The pre-fix input (no context) must FAIL the contract above — otherwise
    the positive test proves nothing about the context."""
    ok, detail = _synthetic_verdict(_StripContext(real_session), f0)
    assert not ok, f"bare-hop control unexpectedly met the contract: {detail}"
