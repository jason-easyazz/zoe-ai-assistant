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
import os
import wave

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


# ═══════════════════════════════════════════════════════════════════════════
# Ingest FIDELITY: a whole recording through _AudioStream, end to end.
#
# The tests above prove each emitted frame is the right LENGTH. These prove the
# stream is still the same AUDIO: drive a full utterance in at 48 kHz stereo
# (what aiortc hands us off a WebRTC track), drain _AudioStream, and compare the
# emitted 16 kHz mono int16-LE PCM against the source it was built from.
#
# Source: a REAL recording from Jason's regression corpus when the box has one,
# a speech-shaped synthetic otherwise. CI has no corpus, so every assertion here
# must hold both ways — the corpus arm skips, the synthetic arm always runs.
# Following #1642: the corpus is LIVE, GROWING and UNCURATED, so never bind to
# one arbitrary member (`sorted(...)[0]` broke exactly that way); take the first
# STRIDED member that is genuinely 16 kHz mono s16, long enough, and not a
# near-silent false wake.
#
# `_drain_padded` reproduces the pre-fix `bytes(planes[0])` read and is the
# suite's permanent negative control: it must FAIL every assertion the real path
# passes, or these thresholds are proving nothing.
# ═══════════════════════════════════════════════════════════════════════════

_SAMPLES_DIR = "/home/zoe/.zoe-voice-samples"
_CORPUS_STRIDE = 37              # a stable spread, not one end of the corpus
_CORPUS_MIN_SAMPLES = 16000      # ≥1s — a shorter clip has no envelope to match
_CORPUS_MIN_PEAK = 500           # reject near-silent captures (false wakes)

# Measured 2026-08-04, av 16.1.0, real corpus + synthetic, this module's config:
#   envelope correlation  0.9999999 (real) — the padded read collapses it to 0.012
#   duration             16 samples (1ms) short = the resampler's own latency
#   loud→silence tail    peak 0     — the padded read leaks peak 31472
_MIN_ENVELOPE_CORR = 0.99
_MAX_DURATION_SLACK = 64         # samples (4ms) — 4x the measured 16
_ENVELOPE_WINDOW = 160           # 10ms at 16 kHz


def _synth_voice_16k(seconds=3.0):
    """Speech-SHAPED 16 kHz mono s16: a drifting harmonic stack under a ~4 Hz
    syllabic envelope with real gaps. Deterministic (no RNG), band-limited well
    under Nyquist so the 16k→48k→16k trip is limited by the resampler, not by
    aliasing in the fixture. This is the CI stand-in for a corpus recording."""
    n = int(seconds * OUT_RATE)
    t = np.arange(n, dtype=np.float64) / OUT_RATE
    f0 = 110.0 + 15.0 * np.sin(2 * np.pi * 0.7 * t)          # pitch drift
    phase = 2 * np.pi * np.cumsum(f0) / OUT_RATE
    sig = np.zeros(n, dtype=np.float64)
    for harmonic, amp in ((1, 1.0), (2, 0.6), (3, 0.35), (5, 0.2), (8, 0.1), (13, 0.05)):
        sig += amp * np.sin(harmonic * phase)
    sig /= np.abs(sig).max()
    # Syllables: ~4/s bursts with genuine silence between them.
    env = np.clip(np.sin(2 * np.pi * 4.0 * t), 0.0, None) ** 2
    return (sig * env * 18000.0).astype(np.int16)


_CORPUS_SLICE = 4                # members evaluated — a population, not a pick


def _corpus_mono16k_slice(limit=_CORPUS_SLICE):
    """Up to `limit` (name, pcm) pairs of real 16 kHz mono audio; [] off-box.

    A deterministic SLICE, never a single chosen file. Striding and then taking
    the first qualifying member is still a per-file assertion: the corpus grows,
    filenames sort by TIME OF DAY rather than date, and a newly captured clip can
    silently become that member — which is how `test_voice_barge_in.py` stayed
    red for weeks with the code and the model untouched. Off-format members
    (24 kHz resamples, one non-RIFF file) and near-silent false wakes are known
    corpus facts and are skipped, not failed on. See ../tests/AGENTS.md.
    """
    if not os.path.isdir(_SAMPLES_DIR):
        return []
    try:
        names = sorted(f for f in os.listdir(_SAMPLES_DIR) if f.endswith(".wav"))
    except OSError:
        return []
    picked = []
    for name in names[::_CORPUS_STRIDE]:
        if len(picked) >= limit:
            break
        try:
            with wave.open(os.path.join(_SAMPLES_DIR, name), "rb") as handle:
                if (handle.getframerate(), handle.getnchannels(),
                        handle.getsampwidth()) != (OUT_RATE, 1, 2):
                    continue
                if handle.getnframes() < _CORPUS_MIN_SAMPLES:
                    continue
                pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
        except Exception:
            continue
        if pcm.size and int(np.abs(pcm).max()) >= _CORPUS_MIN_PEAK:
            picked.append((name, pcm))
    return picked


# Resolved once at collection: on the Jetson EVERY sampled member becomes its own
# test case, so the suite reports a corpus-level result rather than one clip's
# score. In CI the list is empty and only the synthetic arm exists — no skip
# noise, and no arm that silently never ran.
_CORPUS_MEMBERS = _corpus_mono16k_slice()


@pytest.fixture(params=["synthetic"] + [name for name, _ in _CORPUS_MEMBERS])
def source_mono16k(request):
    """The reference signal: always synthetic, plus every sampled real recording.

    Identical assertions run for each — that is the point of keeping a CI arm.
    These are structural signal properties (envelope, duration, silent tail), so
    EVERY member must hold rather than a majority; a noisy or background-TV clip
    still round-trips 48k→16k exactly, which is all that is being measured.
    """
    if request.param == "synthetic":
        return "synthetic", _synth_voice_16k()
    for name, pcm in _CORPUS_MEMBERS:
        if name == request.param:
            return name, pcm
    pytest.skip(f"{request.param} vanished from {_SAMPLES_DIR} between collection and run")


def test_corpus_slice_is_a_population_not_a_pick():
    """CONTROL: on the box the fidelity arms must cover more than one recording.

    If the slice collapses to a single member, every corpus assertion above
    quietly degenerates into the per-file binding this replaced — still green,
    just as fragile. CI has no corpus, so there is nothing to check there.
    """
    if not _CORPUS_MEMBERS:
        pytest.skip(f"no corpus at {_SAMPLES_DIR} (CI has none) — nothing to sample")
    assert len({name for name, _ in _CORPUS_MEMBERS}) == len(_CORPUS_MEMBERS), \
        "duplicate members sampled"
    assert len(_CORPUS_MEMBERS) >= 3, (
        f"only {len(_CORPUS_MEMBERS)} usable recording(s) found by a "
        f"stride-{_CORPUS_STRIDE} walk — the corpus arms need a population. "
        f"Either the corpus shrank or the format filter now rejects too much."
    )


def _to_48k_stereo(mono16k):
    """Build the WEBRTC-SHAPED source: 48 kHz stereo interleaved s16, i.e. what
    a browser track actually delivers. Upsampling here (not in the code under
    test) is deliberate — the assertion is that _AudioStream's 48k→16k leg
    returns us to the signal we started from."""
    up = av.AudioResampler(format="s16", layout="stereo", rate=SRC_RATE)
    chunks = []
    pts = 0
    for start in range(0, mono16k.size, 320):        # 20ms at 16 kHz
        block = mono16k[start:start + 320]
        if not block.size:
            break
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(block).reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = OUT_RATE
        frame.pts = pts
        pts += block.size
        for out in up.resample(frame):
            chunks.append(out.to_ndarray().reshape(-1))
    for out in up.resample(None):                    # flush
        chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)


def _frames_from_48k_stereo(interleaved, nb=480):
    """Slice interleaved 48 kHz stereo into 10ms av.AudioFrames."""
    frames = []
    pts = 0
    for start in range(0, interleaved.size - nb * 2 + 1, nb * 2):
        block = interleaved[start:start + nb * 2]
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(block).reshape(1, -1), format="s16", layout="stereo"
        )
        frame.sample_rate = SRC_RATE
        frame.pts = pts
        pts += nb
        frames.append(frame)
    return frames


def _drain_pcm(frames):
    """Full 16 kHz mono int16-LE stream emitted by _AudioStream for `frames`.

    Read back as an EXPLICIT little-endian view, so a byte-order regression in
    the emitted payload fails here rather than passing on a little-endian host.
    """
    events = _drain(frames)
    if not events:
        return np.zeros(0, dtype="<i2"), events
    pcm = np.concatenate([np.frombuffer(e.frame.data, dtype="<i2") for e in events])
    return pcm, events


def _drain_padded(frames):
    """NEGATIVE CONTROL — the pre-#1636 code path, byte for byte.

    Same resampler config as _AudioStream, but reading `bytes(planes[0])` (the
    whole SIMD-padded buffer) instead of `to_ndarray()`. Every fidelity check
    below is asserted to reject this, so a loosened threshold cannot go unnoticed.
    """
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OUT_RATE)
    out = []
    for frame in frames:
        for resampled in resampler.resample(frame):
            out.append(np.frombuffer(bytes(resampled.planes[0]), dtype="<i2"))
            break                                    # _AudioStream returns the first
    return np.concatenate(out) if out else np.zeros(0, dtype="<i2")


def _envelope(pcm, window=_ENVELOPE_WINDOW):
    """Per-window RMS — the amplitude contour the energy VAD and STT react to."""
    count = pcm.size // window
    if count < 2:
        return np.zeros(0)
    block = pcm[:count * window].astype(np.float64).reshape(count, window)
    return np.sqrt((block ** 2).mean(axis=1))


def _envelope_corr(pcm_a, pcm_b):
    length = min(pcm_a.size, pcm_b.size)
    env_a, env_b = _envelope(pcm_a[:length]), _envelope(pcm_b[:length])
    span = min(env_a.size, env_b.size)
    assert span >= 8, "too little audio to correlate — fixture is broken"
    env_a, env_b = env_a[:span], env_b[:span]
    if env_a.std() == 0 or env_b.std() == 0:
        return 0.0
    return float(np.corrcoef(env_a, env_b)[0, 1])


def test_round_trip_preserves_amplitude_envelope(source_mono16k):
    """48k stereo in → 16k mono out must still be the SAME utterance.

    The padded read did not merely add junk: it interleaved stale pool audio
    into every frame, so the amplitude contour the VAD endpoints on — and that
    STT transcribes — no longer tracked the speaker at all.
    """
    label, source = source_mono16k
    frames = _frames_from_48k_stereo(_to_48k_stereo(source))
    assert frames, f"[{label}] no input frames built"

    emitted, events = _drain_pcm(frames)
    assert len(events) == len(frames), (
        f"[{label}] {len(frames)} frames in but {len(events)} out — _AudioStream "
        f"returns only the FIRST resampled frame per input, so a config change "
        f"that makes the resampler emit more than one silently DROPS audio"
    )

    corr = _envelope_corr(emitted, source)
    assert corr >= _MIN_ENVELOPE_CORR, (
        f"[{label}] envelope correlation {corr:.6f} < {_MIN_ENVELOPE_CORR} — the "
        f"emitted PCM is no longer the source signal"
    )


def test_round_trip_preserves_duration(source_mono16k):
    """Sample-count arithmetic: 48k→16k is exactly 1/3, modulo resampler latency.

    The padded read appended 128 bytes of junk per 320-byte frame, claiming 1.4x
    the real duration — it TIME-STRETCHED the utterance handed to STT.
    """
    label, source = source_mono16k
    interleaved = _to_48k_stereo(source)
    frames = _frames_from_48k_stereo(interleaved)
    emitted, _ = _drain_pcm(frames)

    fed_per_channel = len(frames) * 480          # 48 kHz samples actually fed in
    expected = fed_per_channel // 3              # 48k → 16k
    assert abs(emitted.size - expected) <= _MAX_DURATION_SLACK, (
        f"[{label}] emitted {emitted.size} samples, expected ~{expected} "
        f"(+/-{_MAX_DURATION_SLACK} for resampler latency) — duration not preserved"
    )
    # And in wall-clock terms, which is what a time-stretch actually breaks.
    in_ms = fed_per_channel * 1000.0 / SRC_RATE
    out_ms = emitted.size * 1000.0 / OUT_RATE
    assert abs(out_ms - in_ms) <= 5.0, (
        f"[{label}] {in_ms:.1f}ms in vs {out_ms:.1f}ms out"
    )


def test_round_trip_silence_after_speech_stays_silent(source_mono16k):
    """The #1636 bug class, on a whole recording: speech, then silence.

    Appending the resampler's recycled buffer meant a SILENT input still emitted
    near-full-scale PCM — the experiment that upgraded the bug's severity. The
    silent region must be exactly zero, not merely quiet.
    """
    label, source = source_mono16k
    speech = _to_48k_stereo(source)
    silence = np.zeros(SRC_RATE * 2, dtype=np.int16)     # 1s of 48k stereo silence
    frames = _frames_from_48k_stereo(np.concatenate([speech, silence]))
    events = _drain(frames)
    assert len(events) > 20, f"[{label}] expected both phases to emit"

    # Well inside the silent phase: the last 50 frames are 500ms of pure silence.
    for event in events[-50:]:
        samples = np.frombuffer(event.frame.data, dtype="<i2")
        assert samples.size, f"[{label}] empty payload"
        peak = int(np.abs(samples).max())
        assert peak == 0, (
            f"[{label}] silent input produced peak |sample|={peak} — stale "
            f"buffer-pool audio leaked into the emitted frame"
        )


def test_padded_plane_read_fails_every_fidelity_check(source_mono16k):
    """NEGATIVE CONTROL: the three tests above must REJECT the original bug.

    Without this, a loosened threshold (or an av release that stopped padding)
    would leave the suite green while proving nothing.
    """
    label, source = source_mono16k
    speech = _to_48k_stereo(source)
    frames = _frames_from_48k_stereo(speech)

    bad = _drain_padded(frames)
    good, _ = _drain_pcm(frames)

    # 1. Duration: the padded read time-stretches (measured 1.40x).
    assert bad.size > good.size + _MAX_DURATION_SLACK, (
        f"[{label}] the padded plane is no longer longer than what _AudioStream "
        f"emits ({bad.size} vs {good.size}). Either av stopped over-allocating, "
        f"or _AudioStream itself went back to reading the padded plane — in both "
        f"cases these regression tests have stopped proving anything"
    )
    # 2. Envelope: correlation collapses (measured 0.012 vs 0.9999999).
    bad_corr = _envelope_corr(bad, source)
    assert bad_corr < _MIN_ENVELOPE_CORR, (
        f"[{label}] padded-plane envelope correlation {bad_corr:.6f} still clears "
        f"{_MIN_ENVELOPE_CORR} — the correlation check has no teeth"
    )

    # 3. Stale tail: silence after speech is NOT silent on the buggy path.
    silence = np.zeros(SRC_RATE * 2, dtype=np.int16)
    tail_frames = _frames_from_48k_stereo(np.concatenate([speech, silence]))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OUT_RATE)
    tail_peak = 0
    padded_tail = []
    for frame in tail_frames:
        for resampled in resampler.resample(frame):
            valid = resampled.samples * 2
            padded_tail.append(np.frombuffer(bytes(resampled.planes[0])[valid:], dtype="<i2"))
            break
    for chunk in padded_tail[-50:]:
        if chunk.size:
            tail_peak = max(tail_peak, int(np.abs(chunk).max()))
    assert tail_peak > 0, (
        f"[{label}] the padding after 500ms of silent input is all zero — the "
        f"stale-pool leak this suite guards is no longer reproducible here"
    )
