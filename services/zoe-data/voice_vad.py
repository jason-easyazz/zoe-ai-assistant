"""voice_vad.py — Silero VAD (ONNX) wrapper for the LiveKit voice agent.

Wraps the Silero VAD v6 ONNX model (v6.0 is the live file) with a small
streaming API:

    vad = voice_vad.create_vad()          # None → model unavailable, use RMS
    if vad is not None:
        prob = vad.process(frame_bytes)   # int16-LE PCM @16k, any frame size

The model consumes fixed 512-sample hops (32ms @16kHz); ``process`` buffers
arbitrary-size frames internally and runs inference for every completed hop.

CONTEXT (load-bearing): every inference call is fed the LAST 64 SAMPLES OF THE
PREVIOUS HOP prepended to the new 512-sample hop — a 576-sample model input —
exactly as upstream ``silero_vad.utils_vad.OnnxWrapper.__call__`` does
(silero-vad 6.2.1, ``src/silero_vad/utils_vad.py`` L66-87: ``context_size = 64``
at 16 kHz, ``x = torch.cat([self._context, x], dim=1)``, then
``self._context = x[..., -context_size:]`` after the run; the context starts as
zeros and ``reset_states()`` clears it). The v6 models are calibrated for that
input. Until 2026-09-27 this wrapper fed bare 512-sample hops. Measured on a
48-clip stride of the corpus: v6.0 tolerated it badly (median speech-hop fraction
0.09 vs 0.36 with context; median detection lag after the energy onset 128 ms vs
0 ms) and v6.2.1 detected nothing (0/44 clips >= 0.5 vs 42/44 with context) —
which is why v6.2.1 was wrongly quarantined as "incompatible" on 2026-09-26. The
loader was the bug. v6.0 stays the live file until an echo/noise false-trigger
A/B against v6.2.x is done.

The ONNX session is a lazy module-level singleton (the model is stateless —
per-stream recurrence lives in each ``SileroVAD`` instance), so many
participants share one ~2.3MB model.

Graceful degradation is a hard contract: if the model file is missing or the
session fails to load, ``create_vad()`` logs a warning ONCE and returns None —
callers fall back to the legacy RMS energy VAD. Nothing here ever raises into
the agent loop.

Only needs onnxruntime + numpy (already in the production env).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_PATH = "/home/zoe/models/silero_vad.onnx"
_SAMPLE_RATE = 16000
# Silero v6 consumes fixed 512-sample windows at 16kHz → 32ms per hop, each
# preceded by 64 samples of context from the previous hop (upstream OnnxWrapper
# ``context_size = 64 if sr == 16000``), so the model input is 576 samples.
HOP_SAMPLES = 512
HOP_MS = 32.0
CONTEXT_SAMPLES = 64
MODEL_INPUT_SAMPLES = HOP_SAMPLES + CONTEXT_SAMPLES  # 576

_session = None
_session_failed = False
_warned = False
_session_lock = threading.Lock()


def _model_path() -> str:
    return os.environ.get("ZOE_SILERO_VAD_MODEL", "").strip() or _DEFAULT_MODEL_PATH


def speech_threshold() -> float:
    """Speech probability threshold for IDLE/LISTENING (ZOE_VAD_SPEECH_THRESHOLD,
    default 0.5 = upstream silero-vad's own default).

    Calibration, re-measured 2026-09-27 WITH the 64-sample context on the live
    v6.0 file (the old "speech ~0.79, noise ~0.25" note was measured on the
    context-less loader and is void):
      - whole corpus (1171 usable clips): 95.9% peak > 0.5, median peak 0.967
        (was 94.0% / 0.833 without context);
      - seeded gaussian noise (int16 sigma 80, 30 seeds): worst peak 0.114
        (was 0.434) — the noise floor dropped, so 0.5 gained margin;
      - quiet recent panel captures (rms <= 0.017; 5 of the newest 24) now peak
        0.08-0.47 where the context-less loader gave 0.36-0.79. Lowering the
        threshold to catch them is NOT justified yet: the fixed loader already
        puts 8/50 of the curator's "non-speech" quarantine at >= 0.5 and 20/50
        at >= 0.3, so 0.5 stays until the echo/noise false-trigger A/B
        (docs/knowledge/voice-pipeline.md → The VAD stage).
    Barge-in counts hops at its OWN lower threshold (ZOE_BARGE_SPEECH_THRESHOLD,
    0.30, routers/voice_livekit.py) — that is where the path fails toward
    detecting speech, and it is unchanged here."""
    try:
        return float(os.environ.get("ZOE_VAD_SPEECH_THRESHOLD", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _get_session():
    """Lazy singleton ONNX session, or None when the model is unavailable."""
    global _session, _session_failed, _warned
    if _session is not None:
        return _session
    if _session_failed:
        return None
    with _session_lock:
        if _session is not None or _session_failed:
            return _session
        path = _model_path()
        try:
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Silero VAD model not found: {path}")
            import onnxruntime as ort

            _session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            logger.info("Silero VAD loaded from %s", path)
        except Exception as exc:
            _session_failed = True
            if not _warned:
                _warned = True
                logger.warning(
                    "Silero VAD unavailable (%s) — falling back to RMS energy VAD: %s",
                    path, exc,
                )
        return _session


class SileroVAD:
    """One per audio stream — carries the model's recurrent state AND the
    64-sample context across calls (both per-stream, both cleared by reset)."""

    def __init__(self, session) -> None:
        self._session = session
        # sr must be a 0-d int64 array — a bare Python int is rejected by the model.
        self._sr = np.array(_SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self.last_prob = 0.0

    def reset(self) -> None:
        """Clear recurrent state, context + sample buffer (new utterance / new
        stream) — upstream ``reset_states()`` semantics."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self.last_prob = 0.0

    def process_hops(self, frame_bytes: bytes) -> list:
        """Feed an int16-LE PCM frame; return one speech probability per
        completed 512-sample hop (possibly empty). Never raises."""
        if not frame_bytes:
            return []
        usable = len(frame_bytes) // 2 * 2  # guard against a truncated last sample
        if not usable:
            return []
        samples = (
            np.frombuffer(frame_bytes[:usable], dtype=np.int16).astype(np.float32)
            / 32768.0
        )
        if self._pending.size:
            self._pending = np.concatenate([self._pending, samples])
        else:
            self._pending = samples
        probs: list = []
        while self._pending.size >= HOP_SAMPLES:
            hop = self._pending[:HOP_SAMPLES]
            self._pending = self._pending[HOP_SAMPLES:]
            # [previous 64 samples | this hop] — the 576-sample input the v6
            # models are calibrated for (upstream OnnxWrapper semantics).
            x = np.concatenate([self._context, hop]).reshape(1, MODEL_INPUT_SAMPLES)
            try:
                out = self._session.run(
                    None,
                    {
                        "input": x,
                        "state": self._state,
                        "sr": self._sr,
                    },
                )
            except Exception as exc:  # never raise into the agent frame loop
                logger.debug("Silero VAD inference failed (non-fatal): %s", exc)
                break
            probs.append(float(np.asarray(out[0]).reshape(-1)[0]))
            self._state = np.asarray(out[1], dtype=np.float32)
            # Upstream updates the context only after a successful run.
            self._context = x[0, -CONTEXT_SAMPLES:].copy()
        if probs:
            self.last_prob = probs[-1]
        return probs

    def process(self, frame_bytes: bytes) -> float:
        """Feed a frame; return the max probability of hops completed by this
        call, or 0.0 when no full hop completed."""
        probs = self.process_hops(frame_bytes)
        return max(probs) if probs else 0.0


def create_vad() -> Optional[SileroVAD]:
    """Return a fresh per-stream ``SileroVAD``, or None when the model can't load."""
    session = _get_session()
    if session is None:
        return None
    return SileroVAD(session)
