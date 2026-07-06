"""Smart Turn v3 wrapper (voice_turn) — ambient V2 endpointing.

Two layers, mirroring test_voice_barge_in's split:
  * contract/degradation tests that need no model (CI-safe);
  * host-only functional tests against the real ONNX model + Jason's saved
    voice corpus (skipped wherever the model/corpus is absent).
"""
import glob
import os
import wave

import numpy as np
import pytest

import voice_turn


@pytest.fixture(autouse=True)
def _reset_singleton():
    voice_turn._singleton = None
    voice_turn._load_failed = False
    yield
    voice_turn._singleton = None
    voice_turn._load_failed = False


# ── degradation (CI-safe) ─────────────────────────────────────────────────────

def test_missing_model_degrades_to_none(monkeypatch):
    monkeypatch.setenv("ZOE_SMART_TURN_MODEL", "/nonexistent/model.onnx")
    assert voice_turn.get_smart_turn() is None
    # and it remembers the failure (no retry storm in the hot path)
    assert voice_turn.get_smart_turn() is None
    assert voice_turn._load_failed is True


# ── functional (host-only) ────────────────────────────────────────────────────

_MODEL = "/home/zoe/models/smart-turn-v3.2-cpu.onnx"
_CORPUS = sorted(glob.glob("/home/zoe/.zoe-voice-samples/*.wav"))

needs_model = pytest.mark.skipif(
    not os.path.exists(_MODEL) or not _CORPUS,
    reason="smart-turn model or voice corpus not on this host",
)


def _load_pcm(path):
    w = wave.open(path)
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm[::2] if w.getnchannels() == 2 else pcm


def _longest_loadable(min_seconds=4.0):
    """Longest cleanly-loadable sample ≥ min_seconds (the corpus has a few
    non-wave-readable files; skip those rather than fail on them)."""
    best, best_pcm = None, None
    for path in _CORPUS:
        try:
            pcm = _load_pcm(path)
        except Exception:
            continue
        if best_pcm is None or len(pcm) > len(best_pcm):
            best, best_pcm = path, pcm
    if best_pcm is None or len(best_pcm) < min_seconds * 16000:
        pytest.skip("no loadable corpus sample long enough")
    return best_pcm


@needs_model
def test_loads_and_scores_real_speech():
    det = voice_turn.get_smart_turn()
    assert det is not None
    pcm = _load_pcm(_CORPUS[-1])
    p = det.end_of_turn_prob(pcm)
    assert 0.0 <= p <= 1.0


def _mid_speech_cut(pcm, lo=0.35, hi=0.75):
    """Index inside [lo, hi] of the clip where a 100ms window has the HIGHEST
    energy — i.e. verifiably mid-speech, never a natural pause. Cutting an
    arbitrary fraction can land between sentences (which IS a complete turn and
    the model rightly scores high); cutting at peak energy cannot."""
    win = 1600  # 100ms @16k
    a, b = int(len(pcm) * lo), int(len(pcm) * hi)
    seg = pcm[a:b].astype(np.float32)
    if len(seg) < 2 * win:
        return None
    e = np.array([float(np.abs(seg[i:i + win]).mean()) for i in range(0, len(seg) - win, win)])
    return a + int(e.argmax()) * win + win // 2


@needs_model
def test_complete_utterances_beat_mid_speech_cuts():
    # The property the model exists for: finished utterances score higher than
    # the same audio chopped mid-speech. Asserted as a mean over the loadable
    # corpus (single clips legitimately vary — a cut can sound complete).
    det = voice_turn.get_smart_turn()
    fulls, cuts = [], []
    for path in _CORPUS:
        try:
            pcm = _load_pcm(path)
        except Exception:
            continue
        if len(pcm) < 3 * 16000:
            continue
        cut_at = _mid_speech_cut(pcm)
        if cut_at is None:
            continue
        fulls.append(det.end_of_turn_prob(pcm))
        cuts.append(det.end_of_turn_prob(pcm[:cut_at]))
        if len(fulls) >= 5:
            break
    if len(fulls) < 3:
        pytest.skip("not enough loadable ≥3s samples")
    mf, mc = float(np.mean(fulls)), float(np.mean(cuts))
    assert mf > mc + 0.1, f"mean complete={mf:.2f} vs mean mid-speech-cut={mc:.2f} ({len(fulls)} samples)"


@needs_model
def test_short_audio_pads_cleanly():
    det = voice_turn.get_smart_turn()
    p = det.end_of_turn_prob(np.zeros(1600, dtype=np.int16))  # 0.1s of silence
    assert 0.0 <= p <= 1.0
