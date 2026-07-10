"""Tests for segment-stitched TTS (voice_stitch.py) — pure logic + WAV concat."""
import io
import wave

import pytest

import voice_stitch as vs

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane


def _wav(nframes: int, framerate: int = 24000, channels: int = 1, sampwidth: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(b"\x01\x02" * nframes)  # nframes frames of mono 16-bit
    return buf.getvalue()


def _nframes(blob: bytes) -> int:
    with wave.open(io.BytesIO(blob), "rb") as w:
        return w.getnframes()


# ── number_to_words ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,word", [
    (0, "zero"), (5, "five"), (14, "fourteen"), (20, "twenty"),
    (42, "forty two"), (59, "fifty nine"), (100, "one hundred"),
])
def test_number_to_words(n, word):
    assert vs.number_to_words(n) == word


def test_number_to_words_out_of_range():
    assert vs.number_to_words(-1) is None
    assert vs.number_to_words(101) is None


# ── weather_segments ─────────────────────────────────────────────────────────
def test_weather_segments_valid():
    assert vs.weather_segments(14, "clear", "Geraldton") == \
        ["It's fourteen degrees", "Clear", "in Geraldton"]


def test_weather_segments_unknown_condition_still_stitches():
    # A provider description outside the seed list is passed through (synth-once,
    # then cached) rather than forcing a fallback.
    assert vs.weather_segments(14, "raining frogs", "Geraldton") == \
        ["It's fourteen degrees", "Raining frogs", "in Geraldton"]


def test_weather_segments_empty_condition_omitted():
    assert vs.weather_segments(14, "", "Geraldton") == ["It's fourteen degrees", "in Geraldton"]


def test_weather_segments_out_of_range_temp():
    assert vs.weather_segments(140, "clear", "Geraldton") is None  # temp beyond 0–100 vocab


# ── time_segments ────────────────────────────────────────────────────────────
def test_time_segments_oclock():
    assert vs.time_segments(7, 0) == ["The time is seven o'clock", "AM"]


def test_time_segments_single_digit_minute_says_oh():
    assert vs.time_segments(19, 5) == ["The time is seven", "oh five", "PM"]


def test_time_segments_pm_and_minute():
    assert vs.time_segments(19, 42) == ["The time is seven", "forty two", "PM"]


def test_time_segments_midnight_and_noon():
    assert vs.time_segments(0, 15)[0] == "The time is twelve"   # 12 AM
    assert vs.time_segments(0, 15)[-1] == "AM"
    assert vs.time_segments(12, 15)[-1] == "PM"                  # 12 PM


def test_time_segments_out_of_range():
    assert vs.time_segments(24, 0) is None
    assert vs.time_segments(10, 60) is None


# ── concat_wavs ──────────────────────────────────────────────────────────────
def test_concat_joins_frames_plus_gaps():
    a, b = _wav(1000), _wav(500)
    out = vs.concat_wavs([a, b], gap_ms=0)
    assert _nframes(out) == 1500  # no gap → exact sum


def test_concat_adds_silence_gap():
    a, b = _wav(1000), _wav(1000)
    out = vs.concat_wavs([a, b], gap_ms=70)
    # 70ms @ 24kHz = 1680 frames of silence between the two segments
    assert _nframes(out) == 2000 + 1680


def test_concat_preserves_format():
    out = vs.concat_wavs([_wav(100), _wav(100)])
    with wave.open(io.BytesIO(out), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)


def test_concat_rejects_format_mismatch():
    a = _wav(100, framerate=24000)
    b = _wav(100, framerate=16000)  # differs from canonical → refuse
    assert vs.concat_wavs([a, b]) is None


def test_concat_rejects_uniformly_wrong_format():
    # Both consistent with each other but NOT the canonical 24kHz → still refuse
    # (would otherwise concat cleanly and play at the wrong speed).
    a = _wav(100, framerate=16000)
    b = _wav(100, framerate=16000)
    assert vs.concat_wavs([a, b]) is None


def test_concat_empty_returns_none():
    assert vs.concat_wavs([]) is None


# ── stitch driver ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stitch_synthesizes_and_joins():
    canned = {"It's fourteen degrees": _wav(1000), "Clear": _wav(500), "in Geraldton": _wav(700)}

    async def fake_synth(text):
        return canned.get(text)

    out = await vs.stitch(["It's fourteen degrees", "Clear", "in Geraldton"], fake_synth, gap_ms=0)
    assert _nframes(out) == 2200


@pytest.mark.asyncio
async def test_stitch_missing_segment_falls_back_to_none():
    async def fake_synth(text):
        return _wav(100) if text == "It's fourteen degrees" else None  # a miss

    assert await vs.stitch(["It's fourteen degrees", "Clear"], fake_synth) is None


# ── vocabulary (prewarm set) ─────────────────────────────────────────────────
def test_vocabulary_is_bounded_and_covers_slots():
    vocab = vs.vocabulary("Geraldton")
    assert len(vocab) == len(set(vocab))              # de-duped
    assert 150 < len(vocab) < 260                     # bounded
    assert "It's fourteen degrees" in vocab
    assert "Clear" in vocab
    assert "in Geraldton" in vocab
    assert "The time is seven" in vocab
    assert "forty two" in vocab
    assert "AM" in vocab and "PM" in vocab
