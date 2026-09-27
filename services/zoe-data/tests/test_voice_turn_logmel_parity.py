"""Parity: ``voice_turn.log_mel_features`` vs transformers' WhisperFeatureExtractor.

Smart Turn v3 was trained on ``WhisperFeatureExtractor(chunk_length=8)`` log-mels
(called with ``do_normalize=True``). ``voice_turn`` now computes them in pure
numpy so the endpointer no longer drags torch into zoe-data (+~360 MB). These
tests pin that the replacement is the same function:

* BIT-identical to transformers' own numpy path (``_np_extract_fbank_features``
  after its zero-mean/unit-variance step) and to its mel filter bank;
* within float32 STFT rounding of the full ``__call__`` — which, where torch is
  installed (the Jetson), is the torch path zoe-data used before.

Bit-identity is the bar, not "close": the model is sharply sensitive to 1-ULP
feature changes on some turns (a float64 clamp moved one corpus clip's
end-of-turn probability from 0.44 to 0.19).

Host-only: needs numpy + transformers (and imports torch where installed, so
it is deliberately NOT ``ci_safe``). The slim CI lane skips it; the
no-torch contract itself is pinned CI-side by ``test_voice_turn_no_torch.py``.
"""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("transformers")

from transformers.audio_utils import mel_filter_bank  # noqa: E402
from transformers.models.whisper.feature_extraction_whisper import (  # noqa: E402
    WhisperFeatureExtractor,
)

import voice_turn  # noqa: E402

_N = 8 * 16000


def _signals():
    rng = np.random.default_rng(1234)
    t = np.arange(_N) / 16000.0
    tone_noise_silence = np.concatenate(
        [
            0.3 * np.sin(2 * np.pi * 220.0 * t[:48000]),
            0.05 * rng.standard_normal(40000),
            np.zeros(40000),
        ]
    ).astype(np.float32)
    # int16-quantised, amplitude-modulated noise — speech-like dynamics.
    speechlike = (
        np.clip(rng.standard_normal(_N) * 3000 * np.sin(2 * np.pi * 3 * t) ** 2, -32768, 32767)
        .astype(np.int16)
        .astype(np.float32)
        / 32768.0
    )
    # A short turn front-padded with zeros, exactly as end_of_turn_prob pads it.
    short = np.pad(
        (0.2 * np.sin(2 * np.pi * 440.0 * t[:20000])).astype(np.float32), (_N - 20000, 0)
    )
    silence = np.zeros(_N, dtype=np.float32)
    return {
        "tone+noise+silence": tone_noise_silence,
        "speechlike_int16": speechlike,
        "front_padded_short": short,
        "all_silence": silence,
    }


SIGNALS = _signals()


@pytest.fixture(scope="module")
def extractor():
    return WhisperFeatureExtractor(chunk_length=8)


def test_mel_filter_bank_is_bit_identical():
    ref = mel_filter_bank(
        num_frequency_bins=201,
        num_mel_filters=80,
        min_frequency=0.0,
        max_frequency=8000.0,
        sampling_rate=16000,
        norm="slaney",
        mel_scale="slaney",
    )
    assert np.array_equal(voice_turn._mel_filters(), ref)


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_bit_identical_to_transformers_numpy_path(extractor, name):
    audio = SIGNALS[name]
    normed = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
    ref = extractor._np_extract_fbank_features(normed[None], "cpu").astype(np.float32)
    got = voice_turn.log_mel_features(audio)
    assert got.dtype == np.float32
    assert got.shape == (1, 80, 800) == ref.shape
    assert np.array_equal(got, ref), f"max |diff| {np.abs(got - ref).max():.3e}"


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_matches_extractor_call(extractor, name):
    # The exact call voice_turn used to make. Where torch is installed this is
    # the torch.stft path (float32 throughout): measured max |diff| ~2.4e-5 on
    # features spanning ~[-1.5, 1.5]. Without torch it is the numpy path -> exact.
    audio = SIGNALS[name]
    ref = extractor(
        audio,
        sampling_rate=16000,
        return_tensors="np",
        padding="max_length",
        max_length=_N,
        truncation=True,
        do_normalize=True,
    ).input_features.astype(np.float32)
    got = voice_turn.log_mel_features(audio)
    assert got.shape == ref.shape == (1, 80, 800)
    np.testing.assert_allclose(got, ref, rtol=0, atol=1e-4)


def test_rejects_wrong_window_length():
    with pytest.raises(ValueError):
        voice_turn.log_mel_features(np.zeros(_N - 1, dtype=np.float32))
