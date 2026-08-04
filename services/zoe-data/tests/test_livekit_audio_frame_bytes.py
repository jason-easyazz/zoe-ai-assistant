"""Regression: _AudioStream must emit VALID samples, never the padded plane.

FFmpeg over-allocates each audio plane to its SIMD alignment, so
``bytes(frame.planes[0])`` returns the whole padded buffer rather than the
valid audio. Measured on av 16.1.0 with this module's exact resampler config
(48 kHz stereo s16 -> 16 kHz mono s16): 160 samples yields a 448-byte plane for
320 valid bytes — 128 bytes (28.6%) of trailing junk on every frame.

The padding is not zero-filled. The resampler recycles its buffer pool, so it
carries stale PCM from earlier frames; feeding pure silence after loud audio
leaves near-full-scale samples sitting in the padding. Appending that to each
frame both corrupts the signal and time-stretches it (448 bytes claimed for
320 bytes of real audio), degrading the energy VAD and STT on the LiveKit path.

``livekit_aiortc`` imports av / aiortc / livekit.protocol at module top, so
these are Jetson-only (the slim GitHub runner lacks those wheels) — guarded
with ``importorskip`` so collection skips cleanly off-Jetson.
"""
import asyncio

import pytest

pytest.importorskip("av")
pytest.importorskip("aiortc")
np = pytest.importorskip("numpy")

import av  # noqa: E402

import livekit_aiortc as la  # noqa: E402

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

SRC_RATE = 48000
OUT_RATE = 16000


def _stereo_frame(nb_samples, amplitude, freq=440.0, rate=SRC_RATE):
    """Synthetic 48 kHz stereo s16 frame — what aiortc hands us off a WebRTC track."""
    t = np.arange(nb_samples, dtype=np.float64) / rate
    tone = (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.int16)
    interleaved = np.empty(nb_samples * 2, dtype=np.int16)
    interleaved[0::2] = tone
    interleaved[1::2] = tone
    frame = av.AudioFrame.from_ndarray(
        interleaved.reshape(1, -1), format="s16", layout="stereo"
    )
    frame.sample_rate = rate
    return frame


class _FakeTrack:
    """Minimal stand-in for _RemoteAudioTrack feeding a scripted frame list."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def _recv(self):
        if not self._frames:
            raise RuntimeError("exhausted")
        return self._frames.pop(0)


def _drain(frames):
    """Run _AudioStream over `frames`, returning the emitted PCM payloads."""
    stream = la._AudioStream(_FakeTrack(frames), sample_rate=OUT_RATE, num_channels=1)

    async def _go():
        out = []
        while True:
            try:
                out.append(await stream.__anext__())
            except (StopAsyncIteration, RuntimeError):
                return out

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_go())
    finally:
        loop.close()


def _blocks(count=8, amplitude=20000, nb=480):
    return [_stereo_frame(nb, amplitude, freq=440.0 + i * 30) for i in range(count)]


def test_plane_is_actually_padded_the_bug_premise():
    """Negative control: prove the padding exists, else this suite tests nothing."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OUT_RATE)
    padded_seen = False
    pts = 0
    for frame in _blocks(4):
        frame.pts = pts
        pts += frame.samples
        for out in resampler.resample(frame):
            valid = out.samples * 2  # mono s16 -> 2 bytes/sample
            plane_len = len(bytes(out.planes[0]))
            assert plane_len >= valid
            if plane_len > valid:
                padded_seen = True
    assert padded_seen, (
        "no padded plane produced — av may have changed its allocator; "
        "this regression test would silently stop proving anything"
    )


def test_emitted_frame_length_matches_sample_count():
    """The payload must be exactly samples*2 bytes, with no plane padding."""
    events = _drain(_blocks(8))
    assert events, "no frames emitted"
    for event in events:
        data = event.frame.data
        assert len(data) % 2 == 0, "not whole int16 samples"
        # 16 kHz mono s16 from a 480-sample 48 kHz block is 160 samples = 320 bytes.
        # A padded plane would be 448 bytes here.
        assert len(data) in (288, 320), (
            f"emitted {len(data)} bytes — expected an unpadded 16 kHz mono s16 "
            f"payload (plane padding regression?)"
        )


def test_silence_stays_silent_no_stale_pool_audio():
    """Loud audio then silence: no recycled-buffer PCM may leak into the output.

    This is the bug's real damage — `bytes(planes[0])` appended stale samples
    from the resampler's buffer pool, so silent input emitted near-full-scale
    audio in the padded tail.
    """
    loud = _blocks(8, amplitude=20000)
    silent = [_stereo_frame(480, 0) for _ in range(8)]
    events = _drain(loud + silent)
    assert len(events) >= 12, "expected both phases to emit"

    tail = events[-6:]  # well inside the silent phase
    for event in tail:
        samples = np.frombuffer(event.frame.data, dtype=np.int16)
        assert samples.size, "empty payload"
        peak = int(np.abs(samples).max())
        assert peak == 0, (
            f"silent input produced peak |sample|={peak} — stale buffer-pool "
            f"audio leaked into the emitted frame"
        )


def test_output_is_deterministic_across_runs():
    """Identical input must yield byte-identical output; padding made it vary."""
    first = [e.frame.data for e in _drain(_blocks(8))]
    second = [e.frame.data for e in _drain(_blocks(8))]
    assert first == second, "emitted PCM differed between identical runs"


def test_emitted_bytes_equal_valid_prefix_of_plane():
    """to_ndarray() must equal exactly the valid region of the raw plane."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OUT_RATE)
    checked = 0
    pts = 0
    for frame in _blocks(4):
        frame.pts = pts
        pts += frame.samples
        for out in resampler.resample(frame):
            valid = out.samples * 2
            assert out.to_ndarray().shape == (1, out.samples)
            assert out.to_ndarray().tobytes() == bytes(out.planes[0])[:valid]
            checked += 1
    assert checked, "no frames checked"
