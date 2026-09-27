"""Smart Turn v3 end-of-turn detection for the voice pipeline (ambient V2).

Replaces the fixed silence-window endpoint heuristic with a small on-device
ONNX classifier (pipecat-ai's smart-turn-v3.2-cpu, BSD-2-Clause) that reads the
*intonation* of the last ≤8s of a turn and scores whether the speaker is
actually finished (1.0) or just pausing mid-thought (0.0). Measured on Jason's
real saved voice on this box: a complete utterance scored 0.90 while the same
clip truncated mid-sentence scored 0.02 — the discrimination a silence timer
cannot make. Inference ≈200 ms on one CPU thread; it runs once per candidate
endpoint (when the silence window elapses), never per frame.

Design mirrors ``voice_vad`` (same degradation contract): lazy singleton,
graceful degradation. If the model file or onnxruntime is unavailable the
factory returns ``None`` and callers keep today's fixed-silence behaviour —
this module must never take down the voice loop.

Features are a pure-numpy Whisper log-mel (``log_mel_features``), numerically
equivalent to ``transformers.WhisperFeatureExtractor(chunk_length=8)`` called
with ``do_normalize=True`` — the preprocessing the model was trained with. It
replaced that extractor because in transformers 5.x importing it drags in torch
unconditionally (+~360 MB resident for the life of zoe-data, from the first
LiveKit turn) just to compute an 80x800 spectrogram. Parity with the
transformers output (its torch and numpy paths) is pinned by
``tests/test_voice_turn_logmel_parity.py``; that nothing here imports torch or
transformers is pinned by ``tests/test_voice_turn_no_torch.py``.
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

# Whisper log-mel parameters (transformers WhisperFeatureExtractor defaults:
# feature_size=80, n_fft=400, hop_length=160, slaney mel scale + slaney norm
# over 0-8 kHz, periodic Hann, centred reflect-padded STFT, power 2, log10
# floored at 1e-10, dynamic range clamped to max-8, then (x + 4) / 4).
_N_FFT = 400
_HOP = 160
_N_MELS = 80
_MEL_FLOOR = 1e-10

_mel_filters_cache: Optional[np.ndarray] = None
_hann_cache: Optional[np.ndarray] = None


def _hz_to_mel_slaney(freq: np.ndarray) -> np.ndarray:
    # Same arithmetic, in the same order, as transformers.audio_utils.hertz_to_mel(mel_scale="slaney").
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = 27.0 / np.log(6.4)
    mels = 3.0 * freq / 200.0
    log_region = freq >= min_log_hertz
    mels[log_region] = min_log_mel + np.log(freq[log_region] / min_log_hertz) * logstep
    return mels


def _mel_to_hz_slaney(mels: np.ndarray) -> np.ndarray:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = np.log(6.4) / 27.0
    freq = 200.0 * mels / 3.0
    log_region = mels >= min_log_mel
    freq[log_region] = min_log_hertz * np.exp(logstep * (mels[log_region] - min_log_mel))
    return freq


def _mel_filters() -> np.ndarray:
    """(201, 80) slaney-scale, slaney-normalised triangular filter bank.

    Mirrors ``transformers.audio_utils.mel_filter_bank(num_frequency_bins=201,
    num_mel_filters=80, min_frequency=0, max_frequency=8000, sampling_rate=16000,
    norm="slaney", mel_scale="slaney")``.
    """
    global _mel_filters_cache
    if _mel_filters_cache is None:
        n_bins = 1 + _N_FFT // 2
        mel_edges = _hz_to_mel_slaney(np.array([0.0, 8000.0]))
        mel_freqs = np.linspace(mel_edges[0], mel_edges[1], _N_MELS + 2)
        filter_freqs = _mel_to_hz_slaney(mel_freqs)
        fft_freqs = np.linspace(0, _SAMPLE_RATE // 2, n_bins)
        filter_diff = np.diff(filter_freqs)
        slopes = np.expand_dims(filter_freqs, 0) - np.expand_dims(fft_freqs, 1)
        down_slopes = -slopes[:, :-2] / filter_diff[:-1]
        up_slopes = slopes[:, 2:] / filter_diff[1:]
        filters = np.maximum(np.zeros(1), np.minimum(down_slopes, up_slopes))
        enorm = 2.0 / (filter_freqs[2 : _N_MELS + 2] - filter_freqs[:_N_MELS])
        filters *= np.expand_dims(enorm, 0)
        _mel_filters_cache = filters
    return _mel_filters_cache


def _hann() -> np.ndarray:
    global _hann_cache
    if _hann_cache is None:
        _hann_cache = np.hanning(_N_FFT + 1)[:-1]  # periodic Hann, as torch.hann_window
    return _hann_cache


def log_mel_features(audio: np.ndarray) -> np.ndarray:
    """Smart Turn input features for exactly 8 s of float32 mono 16 kHz audio.

    Returns float32 ``[1, 80, 800]`` — what ``WhisperFeatureExtractor(
    chunk_length=8)(audio, sampling_rate=16000, padding="max_length",
    max_length=128000, truncation=True, do_normalize=True, return_tensors="np")``
    produced for the same input.
    """
    x = np.asarray(audio, dtype=np.float32)
    if x.shape != (_WINDOW_SAMPLES,):
        raise ValueError(f"expected {_WINDOW_SAMPLES} samples, got shape {x.shape}")
    # do_normalize: zero-mean / unit-variance over the (unpadded) window, in float32.
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
    # Centred STFT: reflect-pad n_fft/2 each side, 801 frames of 400 at hop 160.
    padded = np.pad(x, _N_FFT // 2, mode="reflect").astype(np.float64)
    frames = np.lib.stride_tricks.sliding_window_view(padded, _N_FFT)[::_HOP]
    # Precision is deliberately the reference's, not "better": transformers
    # stores the STFT as complex64, takes |.|^2 in float64, and casts log10 to
    # float32 before the clamp and rescale. Matching that makes these features
    # BIT-identical to its numpy path. It matters: the model is sharply
    # sensitive to 1-ULP changes on some turns (measured on a corpus clip: a
    # float64 clamp moved the end-of-turn probability from 0.44 to 0.19).
    spec = np.fft.rfft(frames * _hann(), axis=-1).astype(np.complex64)
    power = np.abs(spec, dtype=np.float64) ** 2  # (801, 201)
    power = power[:-1]  # Whisper drops the last STFT frame
    mel = np.maximum(_MEL_FLOOR, np.dot(_mel_filters().T, power.T))  # (80, 800)
    log_spec = np.log10(mel).astype(np.float32)
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec[np.newaxis]

_singleton: Optional["SmartTurnDetector"] = None
_singleton_lock = threading.Lock()
_load_failed = False


class SmartTurnDetector:
    """End-of-turn scorer over int16 mono 16 kHz PCM."""

    def __init__(self, model_path: str):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = int(os.environ.get("ZOE_SMART_TURN_THREADS", "1"))
        self._session = ort.InferenceSession(
            model_path, sess_options=so, providers=["CPUExecutionProvider"]
        )

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
        feats = log_mel_features(audio)
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
