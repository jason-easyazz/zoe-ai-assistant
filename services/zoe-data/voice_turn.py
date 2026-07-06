"""Smart Turn v3 end-of-turn detection for the voice pipeline (ambient V2).

Replaces the fixed silence-window endpoint heuristic with a small on-device
ONNX classifier (pipecat-ai's smart-turn-v3.2-cpu, BSD-2-Clause) that reads the
*intonation* of the last ≤8s of a turn and scores whether the speaker is
actually finished (1.0) or just pausing mid-thought (0.0). Measured on Jason's
real saved voice on this box: a complete utterance scored 0.90 while the same
clip truncated mid-sentence scored 0.02 — the discrimination a silence timer
cannot make. Inference ≈200 ms on one CPU thread; it runs once per candidate
endpoint (when the silence window elapses), never per frame.

Design mirrors ``voice_vad``: lazy singleton, graceful degradation. If the
model file, transformers, or soxr are unavailable the factory returns ``None``
and callers keep today's fixed-silence behaviour — this module must never take
down the voice loop.

The heavy import (``transformers.WhisperFeatureExtractor``) happens inside the
factory, not at module import, so merely importing this module stays cheap.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH_ENV = "ZOE_SMART_TURN_MODEL"
_DEFAULT_MODEL_PATH = "/home/zoe/models/smart-turn-v3.2-cpu.onnx"
# Model contract (verified on-box): input_features float32 [1, 80, 800]
# (Whisper log-mel of exactly 8 s @ 16 kHz), output sigmoid probability [1, 1].
_SAMPLE_RATE = 16000
_WINDOW_SAMPLES = 8 * _SAMPLE_RATE

_singleton: Optional["SmartTurnDetector"] = None
_singleton_lock = threading.Lock()
_load_failed = False


class SmartTurnDetector:
    """End-of-turn scorer over int16 mono 16 kHz PCM."""

    def __init__(self, model_path: str):
        import onnxruntime as ort
        from transformers import WhisperFeatureExtractor

        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = int(os.environ.get("ZOE_SMART_TURN_THREADS", "1"))
        self._session = ort.InferenceSession(
            model_path, sess_options=so, providers=["CPUExecutionProvider"]
        )
        self._extractor = WhisperFeatureExtractor(chunk_length=8)

    def end_of_turn_prob(self, pcm16: np.ndarray) -> float:
        """Probability the utterance in ``pcm16`` is complete (speaker done).

        Uses the last 8 s (padded at the front if shorter), matching the
        model's training window.
        """
        audio = pcm16.astype(np.float32) / 32768.0
        if len(audio) > _WINDOW_SAMPLES:
            audio = audio[-_WINDOW_SAMPLES:]
        elif len(audio) < _WINDOW_SAMPLES:
            audio = np.pad(audio, (_WINDOW_SAMPLES - len(audio), 0))
        feats = self._extractor(
            audio,
            sampling_rate=_SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=_WINDOW_SAMPLES,
            truncation=True,
            do_normalize=True,
        ).input_features.astype(np.float32)
        out = self._session.run(None, {"input_features": feats})
        return float(np.asarray(out[0]).reshape(-1)[0])


def get_smart_turn() -> Optional[SmartTurnDetector]:
    """Lazy singleton. Returns None (and logs once) when unavailable —
    callers must treat None as "keep the legacy fixed-silence endpoint"."""
    global _singleton, _load_failed
    if _singleton is not None:
        return _singleton
    if _load_failed:
        return None
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        if _load_failed:
            return None
        path = os.environ.get(_MODEL_PATH_ENV, _DEFAULT_MODEL_PATH)
        try:
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            _singleton = SmartTurnDetector(path)
            logger.info("voice_turn: smart-turn-v3 loaded from %s", path)
        except Exception as exc:
            _load_failed = True
            logger.warning(
                "voice_turn: smart-turn unavailable (%s) — using fixed-silence endpointing",
                exc,
            )
            return None
    return _singleton
