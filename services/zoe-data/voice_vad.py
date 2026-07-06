"""voice_vad.py — Silero VAD (ONNX) wrapper for the LiveKit voice agent.

Wraps the Silero VAD v5 ONNX model with a small streaming API:

    vad = voice_vad.create_vad()          # None → model unavailable, use RMS
    if vad is not None:
        prob = vad.process(frame_bytes)   # int16-LE PCM @16k, any frame size

The model consumes fixed 512-sample hops (32ms @16kHz); ``process`` buffers
arbitrary-size frames internally and runs inference for every completed hop.
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
# Silero v5 consumes fixed 512-sample windows at 16kHz → 32ms per hop.
HOP_SAMPLES = 512
HOP_MS = 32.0

_session = None
_session_failed = False
_warned = False
_session_lock = threading.Lock()


def _model_path() -> str:
    return os.environ.get("ZOE_SILERO_VAD_MODEL", "").strip() or _DEFAULT_MODEL_PATH


def speech_threshold() -> float:
    """Speech probability threshold (env-tunable; measured speech ~0.79, noise ~0.25)."""
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
    """One per audio stream — carries the model's recurrent state across calls."""

    def __init__(self, session) -> None:
        self._session = session
        # sr must be a 0-d int64 array — a bare Python int is rejected by the model.
        self._sr = np.array(_SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self.last_prob = 0.0

    def reset(self) -> None:
        """Clear recurrent state + sample buffer (new utterance / new stream)."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
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
            try:
                out = self._session.run(
                    None,
                    {
                        "input": hop.reshape(1, HOP_SAMPLES),
                        "state": self._state,
                        "sr": self._sr,
                    },
                )
            except Exception as exc:  # never raise into the agent frame loop
                logger.debug("Silero VAD inference failed (non-fatal): %s", exc)
                break
            probs.append(float(np.asarray(out[0]).reshape(-1)[0]))
            self._state = np.asarray(out[1], dtype=np.float32)
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
