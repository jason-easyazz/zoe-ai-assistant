"""Segmentation state machine of ``routers.voice_livekit._collect_audio_stream``.

This is the second half of the LiveKit ingest lane: `test_livekit_audio_frame_bytes.py`
proves the PCM arriving here is faithful; this proves what the agent *does* with it —
where an utterance starts, where it ends, and what gets handed to STT.

`test_voice_barge_in.py` owns the Silero/barge-in machine (ZOE_VOICE_BARGE_IN=1).
This file owns the parts that file leaves uncovered, all of which are live today:

  * the **PTT override** (`ptt_active`) — the first branch in the frame loop, and
    until now completely untested despite short-circuiting *both* VAD paths;
  * the **legacy RMS machine** counter contracts — `speech_count` / `silence_count`
    are CONSECUTIVE counters, and the resets are the whole behaviour: without them
    scattered noise accumulates into a false turn, and a mid-sentence breath cuts
    the speaker off. Only the two happy-path transitions had coverage;
  * the barge-in **ring-buffer caps** (`barge_window`, `barge_frames`) — unbounded
    growth here would leak for the lifetime of a participant;
  * **participant removal mid-stream** and the **COOLDOWN watchdog** expiry.

Frames are synthetic PCM shaped like speech (harmonic bursts) or silence, so every
threshold crossing is deterministic. One test additionally drives a REAL recording
from Jason's corpus when the box has one, with a synthetic equivalent that always
runs — CI has no corpus (see test_voice_barge_in.py's corpus notes).

No model, no network, no DB, no LiveKit stacks: `voice_livekit` lazy-imports
`livekit` / `livekit_aiortc` inside functions, so a slim fake in `sys.modules` is
enough to take the aiortc branch.
"""
import asyncio
import json
import math
import os
import struct
import sys
import types
import wave

import pytest

import routers.voice_livekit as v

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: validate.yml's `-m ci_safe` lane

_SAMPLES_DIR = "/home/zoe/.zoe-voice-samples"
_RATE = 16000
_FRAME_SAMPLES = 160          # 10ms — what _AudioStream emits


# ── harness (mirrors test_voice_barge_in.py / test_voice_livekit_lifecycle.py) ──

def _run(coro):
    """Fresh loop per case — keeps state isolated and avoids a shared
    session-scoped pytest-asyncio loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeFrame:
    def __init__(self, data: bytes):
        self.data = data


class _FakeFrameEvent:
    def __init__(self, data: bytes):
        self.frame = _FakeFrame(data)


class _FakeLocalParticipant:
    """Captures every data-channel message the agent broadcasts."""

    def __init__(self):
        self.sent = []

    async def publish_data(self, data, reliable=True):
        self.sent.append(json.loads(data.decode()))


def _install_fake_aiortc(monkeypatch):
    """Slim fake ``livekit_aiortc`` so _collect_audio_stream takes the aiortc
    branch and iterates the track directly."""
    mod = types.ModuleType("livekit_aiortc")

    class _RemoteAudioTrack:
        pass

    class _TrackKind:
        KIND_AUDIO = 1

    mod._RemoteAudioTrack = _RemoteAudioTrack
    mod._TrackKind = _TrackKind
    mod.make_audio_stream = lambda track, **_k: track
    monkeypatch.setitem(sys.modules, "livekit_aiortc", mod)
    return mod


def _make_track(mod, frames):
    """An aiortc-marker track that async-iterates the given raw PCM frames."""

    class _Track(mod._RemoteAudioTrack):
        def __init__(self):
            self._events = [_FakeFrameEvent(f) for f in frames]
            self.consumed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._events:
                self.consumed += 1
                return self._events.pop(0)
            raise StopAsyncIteration

    return _Track()


def _install_fake_voice_vad(monkeypatch, hop_probs_per_frame):
    """Fake ``voice_vad`` replaying one list of hop probabilities per frame."""
    mod = types.ModuleType("voice_vad")
    mod.HOP_MS = 32.0
    mod.HOP_SAMPLES = 512
    mod.speech_threshold = lambda: 0.5
    created = []

    class _FakeVAD:
        def __init__(self, script):
            self._script = list(script)
            self.calls = 0

        def reset(self):
            pass

        def process_hops(self, _raw):
            self.calls += 1
            return self._script.pop(0) if self._script else []

        def process(self, raw):
            probs = self.process_hops(raw)
            return max(probs) if probs else 0.0

    def create_vad():
        vad = _FakeVAD(hop_probs_per_frame)
        created.append(vad)
        return vad

    mod.create_vad = create_vad
    mod._created = created
    monkeypatch.setitem(sys.modules, "voice_vad", mod)
    return mod


async def _noop_prewarm(*_a, **_k):
    return None


def _clean_env(monkeypatch):
    """These flags are ON in the live .env — an inherited value silently changes
    which machine runs. Never let a test read ambient config."""
    for name in (
        "ZOE_VOICE_BARGE_IN", "ZOE_BARGE_MIN_MS", "ZOE_BARGE_SPEECH_THRESHOLD",
        "ZOE_VAD_SPEECH_THRESHOLD", "ZOE_SMART_TURN_ENABLED",
        "ZOE_SMART_TURN_THRESHOLD", "ZOE_SMART_TURN_MAX_CHECKS",
    ):
        monkeypatch.delenv(name, raising=False)


def _capture_pipeline(monkeypatch):
    """Replace _run_pipeline; return the list of utterances handed to STT."""
    calls = []

    async def _fake_pipeline(local_participant, frames, user_id, session_id):
        calls.append({"frames": frames, "user_id": user_id, "session_id": session_id})

    monkeypatch.setattr(v, "_run_pipeline", _fake_pipeline)
    monkeypatch.setattr(v, "_prewarm_brain", _noop_prewarm)
    return calls


# ── synthetic PCM: speech-shaped bursts vs silence, RMS-calibrated ──────────

def _silence(n=_FRAME_SAMPLES) -> bytes:
    return b"\x00\x00" * n


def _constant(value, n=_FRAME_SAMPLES) -> bytes:
    """RMS of a constant frame is exactly |value| — used for the threshold
    BOUNDARY, where an approximate fixture would prove nothing."""
    return struct.pack(f"<{n}h", *([value] * n))


def _speech_pcm(seconds=1.0, rate=_RATE, amplitude=9000.0):
    """Speech-SHAPED int16: a drifting harmonic stack, continuously voiced (no
    syllable gaps) so every 10ms frame clears the RMS gate. Deterministic."""
    n = int(seconds * rate)
    t = [i / rate for i in range(n)]
    out = []
    phase = 0.0
    for i in range(n):
        f0 = 115.0 + 12.0 * math.sin(2 * math.pi * 0.9 * t[i])
        phase += 2 * math.pi * f0 / rate
        sample = (math.sin(phase) + 0.6 * math.sin(2 * phase)
                  + 0.3 * math.sin(3 * phase) + 0.15 * math.sin(5 * phase))
        out.append(int(max(-32768, min(32767, sample * amplitude / 2.05))))
    return struct.pack(f"<{n}h", *out)


def _framed(pcm: bytes, n=_FRAME_SAMPLES):
    """Slice raw PCM into fixed-size frames, dropping any short tail."""
    step = n * 2
    return [pcm[i:i + step] for i in range(0, len(pcm) - step + 1, step)]


def _speech_frames(count):
    frames = _framed(_speech_pcm(seconds=count * _FRAME_SAMPLES / _RATE + 0.05))
    assert len(frames) >= count, "speech fixture too short"
    frames = frames[:count]
    for frame in frames:
        assert v._rms(frame) >= v._VAD_ENERGY_THRESHOLD, (
            "speech fixture does not clear the RMS gate — the fixture is broken, "
            "not the code under test"
        )
    return frames


def _silence_frames(count):
    frames = [_silence() for _ in range(count)]
    assert v._rms(frames[0]) < v._VAD_ENERGY_THRESHOLD
    return frames


# ── 1. PTT override — the first branch in the frame loop ────────────────────

def test_ptt_buffers_every_frame_and_never_endpoints(monkeypatch):
    """``ptt_start`` disables VAD for the turn: frames are buffered verbatim,
    the state never moves, and no pipeline runs until ``ptt_stop`` says so.

    Crucially it buffers SILENT frames too — the IDLE VAD path would discard
    those (``ps["frames"] = []``), so this is what distinguishes the paths.
    """
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    vad_mod = _install_fake_voice_vad(monkeypatch, [[0.99]] * 60)
    calls = _capture_pipeline(monkeypatch)

    sid = "sid-ptt-basic"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["ptt_active"] = True
    local = _FakeLocalParticipant()

    # Deliberately mostly silence + a long trailing run that would trip the
    # ~600ms endpoint on the VAD path.
    frames = _speech_frames(3) + _silence_frames(v._VAD_SILENCE_FRAMES + 10)
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.IDLE, "PTT must not drive the state machine"
    assert ps["frames"] == frames, "PTT must buffer every frame verbatim, silence included"
    assert ps["speech_count"] == 0 and ps["silence_count"] == 0
    assert calls == [], "PTT endpointing is driven by ptt_stop, never by the VAD"
    assert local.sent == [], "PTT must not broadcast VAD state changes"
    assert vad_mod._created == [], "PTT must short-circuit before Silero"


def test_ptt_takes_precedence_over_barge_in(monkeypatch):
    """PTT is checked BEFORE the barge-in branch: with ZOE_VOICE_BARGE_IN=1 and
    a VAD that would scream speech, a held button still bypasses Silero entirely."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    mod = _install_fake_aiortc(monkeypatch)
    vad_mod = _install_fake_voice_vad(monkeypatch, [[0.99]] * 60)
    calls = _capture_pipeline(monkeypatch)

    sid = "sid-ptt-barge"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["ptt_active"] = True
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 10 ** 12
    local = _FakeLocalParticipant()

    frames = _speech_frames(20)
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert vad_mod._created == [], "PTT must short-circuit before _ensure_participant_vad"
    assert ps["state"] == v._ParticipantState.COOLDOWN
    assert ps["frames"] == frames
    assert all(m.get("type") != "stop_playback" for m in local.sent), \
        "a held PTT button must not barge itself in"
    assert calls == []


# ── 2. IDLE: speech_count is a CONSECUTIVE counter ──────────────────────────

def test_idle_sub_threshold_resets_count_and_discards_frames(monkeypatch):
    """Below-threshold audio is noise: the counter resets AND the buffer is
    dropped, so ambient hum never becomes the head of an utterance."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _capture_pipeline(monkeypatch)

    sid = "sid-idle-noise"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    # One short of the gate, then silence — everything buffered must be dropped.
    frames = _speech_frames(v._VAD_MIN_SPEECH_FRAMES - 1) + _silence_frames(1)
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.IDLE
    assert ps["speech_count"] == 0, "sub-threshold energy must reset speech_count"
    assert ps["frames"] == [], "sub-threshold energy must discard the buffered noise"
    assert local.sent == []


def test_idle_below_gate_stays_idle_but_buffers(monkeypatch):
    """Speech under the minimum duration accumulates without transitioning —
    the frames are kept so the utterance is not clipped when it does fire."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _capture_pipeline(monkeypatch)

    sid = "sid-idle-partial"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    count = v._VAD_MIN_SPEECH_FRAMES - 1
    _run(v._collect_audio_stream(_make_track(mod, _speech_frames(count)), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.IDLE, \
        f"{count} frames is under the {v._VAD_MIN_SPEECH_FRAMES}-frame gate"
    assert ps["speech_count"] == count
    assert len(ps["frames"]) == count, "pre-trigger speech must be kept, not dropped"
    assert local.sent == []


def test_idle_to_listening_at_exactly_the_gate(monkeypatch):
    """Exactly _VAD_MIN_SPEECH_FRAMES consecutive frames fires the transition
    (``>=``, not ``>``), broadcasts `listening`, and arms the silence counter."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _capture_pipeline(monkeypatch)

    sid = "sid-idle-gate"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["silence_count"] = 99  # must be re-armed by the transition
    local = _FakeLocalParticipant()

    frames = _speech_frames(v._VAD_MIN_SPEECH_FRAMES)
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.LISTENING
    assert ps["silence_count"] == 0, "IDLE→LISTENING must re-arm the silence counter"
    assert len(ps["frames"]) == v._VAD_MIN_SPEECH_FRAMES
    assert {"type": "state", "state": "listening"} in local.sent


def test_idle_scattered_speech_never_accumulates(monkeypatch):
    """The counter is CONSECUTIVE: alternating speech/quiet — clattering dishes,
    a ticking clock — must never reach the gate however long it goes on."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _capture_pipeline(monkeypatch)

    sid = "sid-idle-scatter"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    loud = _speech_frames(1)[0]
    frames = [loud, _silence()] * (v._VAD_MIN_SPEECH_FRAMES * 6)
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.IDLE, \
        "non-consecutive above-threshold frames must never trigger speech-start"
    assert ps["speech_count"] == 0
    assert ps["frames"] == []
    assert local.sent == []


def test_idle_energy_exactly_at_threshold_counts_as_speech(monkeypatch):
    """Boundary: the comparison is ``energy >= _VAD_ENERGY_THRESHOLD``. A frame
    whose RMS is exactly the threshold is speech; one LSB under it is not."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _capture_pipeline(monkeypatch)

    at = _constant(v._VAD_ENERGY_THRESHOLD)
    below = _constant(v._VAD_ENERGY_THRESHOLD - 1)
    assert v._rms(at) == pytest.approx(v._VAD_ENERGY_THRESHOLD)
    assert v._rms(below) == pytest.approx(v._VAD_ENERGY_THRESHOLD - 1)

    for label, frame, expected in (
        ("at", at, v._ParticipantState.LISTENING),
        ("below", below, v._ParticipantState.IDLE),
    ):
        sid = f"sid-bound-{label}"
        ps_map = {sid: v._make_participant_state(sid)}
        local = _FakeLocalParticipant()
        frames = [frame] * v._VAD_MIN_SPEECH_FRAMES
        _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))
        assert ps_map[sid]["state"] == expected, f"RMS exactly-{label}-threshold"


# ── 3. LISTENING: silence_count is a CONSECUTIVE counter ───────────────────

def test_listening_speech_resets_silence_and_pause_does_not_end_turn(monkeypatch):
    """A mid-sentence breath must not cut the speaker off: any speech frame
    resets silence_count, so a pause one frame under the window is survivable
    however many times it happens."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    calls = _capture_pipeline(monkeypatch)

    sid = "sid-listen-pause"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    speech = _speech_frames(v._VAD_MIN_SPEECH_FRAMES)
    pause = _silence_frames(v._VAD_SILENCE_FRAMES - 1)
    resume = _speech_frames(2)
    frames = speech + pause + resume + pause + resume
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.LISTENING, \
        "pauses shorter than the endpoint window must not end the turn"
    assert ps["silence_count"] == 0, "trailing speech must have reset the counter"
    assert calls == [], "no pipeline may be scheduled mid-utterance"
    assert len(ps["frames"]) == len(frames), \
        "LISTENING buffers EVERY frame, pauses included — that audio is speech context"


def test_listening_endpoints_after_the_silence_window(monkeypatch):
    """_VAD_SILENCE_FRAMES consecutive quiet frames ends the turn: PROCESSING,
    one pipeline with the WHOLE utterance, and both counters re-armed."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    calls = _capture_pipeline(monkeypatch)

    sid = "sid-listen-end"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["user_id"] = "jason"
    ps_map[sid]["session_id"] = "sess-1"
    local = _FakeLocalParticipant()

    speech = _speech_frames(v._VAD_MIN_SPEECH_FRAMES + 4)
    tail = _silence_frames(v._VAD_SILENCE_FRAMES)
    frames = speech + tail

    async def _body():
        await v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local)
        await asyncio.sleep(0)   # let the pipeline task run

    _run(_body())

    ps = ps_map[sid]
    assert len(calls) == 1, "exactly one pipeline per utterance"
    assert calls[0]["user_id"] == "jason" and calls[0]["session_id"] == "sess-1"
    # The snapshot is the whole utterance: speech AND the trailing silence that
    # ended it (STT needs the tail; clipping it truncates the last word).
    assert calls[0]["frames"] == frames, "the pipeline must receive the full utterance"
    assert ps["frames"] == [], "the live buffer must be cleared for the next turn"
    assert ps["speech_count"] == 0 and ps["silence_count"] == 0

    # The snapshot must be INDEPENDENT of the live buffer — the pipeline runs
    # concurrently with the next turn's frames arriving, so an alias would let
    # the next utterance mutate the one being transcribed. (`is not` alone does
    # not prove this: rebinding ps["frames"] leaves an alias distinct-but-shared.)
    snapshot_before = list(calls[0]["frames"])
    ps["frames"].append(_silence())
    assert calls[0]["frames"] == snapshot_before, \
        "writing to the live buffer changed the utterance already handed to STT"
    assert ps["state"] in (v._ParticipantState.PROCESSING, v._ParticipantState.COOLDOWN)


def test_listening_endpoint_needs_the_full_window(monkeypatch):
    """One frame short of the window must NOT end the turn — the direct negative
    control for the test above."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    calls = _capture_pipeline(monkeypatch)

    sid = "sid-listen-short"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    frames = _speech_frames(v._VAD_MIN_SPEECH_FRAMES) + _silence_frames(v._VAD_SILENCE_FRAMES - 1)
    _run(v._collect_audio_stream(_make_track(mod, frames), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.LISTENING
    assert ps["silence_count"] == v._VAD_SILENCE_FRAMES - 1
    assert calls == []


# ── 4. Barge-in ring buffers must stay BOUNDED ─────────────────────────────

def test_barge_window_and_frame_buffers_are_capped(monkeypatch):
    """While PROCESSING/COOLDOWN the agent keeps listening indefinitely. Both
    rolling buffers are explicitly trimmed; without the trims a participant who
    never speaks again still grows them for the lifetime of the session."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    mod = _install_fake_aiortc(monkeypatch)
    # Sub-threshold hops forever: never barges, so the trims are the only thing
    # bounding the buffers.
    _install_fake_voice_vad(monkeypatch, [[0.05]] * 400)
    _capture_pipeline(monkeypatch)

    sid = "sid-barge-cap"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 10 ** 12
    local = _FakeLocalParticipant()

    n_frames = v._barge_window_hops() * 8
    _run(v._collect_audio_stream(_make_track(mod, _silence_frames(n_frames)), sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.COOLDOWN, "silent hops must not barge in"
    assert len(ps["barge_window"]) <= v._barge_window_hops(), (
        f"barge_window grew to {len(ps['barge_window'])} over {n_frames} frames — "
        f"cap is {v._barge_window_hops()}"
    )
    assert len(ps["barge_frames"]) <= v._barge_window_hops() * 2, (
        f"barge_frames grew to {len(ps['barge_frames'])} over {n_frames} frames — "
        f"cap is {v._barge_window_hops() * 2}"
    )


def test_barge_window_ages_out_stale_speech_hops(monkeypatch):
    """The window is ROLLING, not cumulative: enough speech hops to barge, but
    spread past the window, must age out instead of eventually firing."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    mod = _install_fake_aiortc(monkeypatch)

    # One speech hop, then a full window of silence — repeated. Cumulatively far
    # more than _barge_min_hops speech hops; never that many inside one window.
    span = v._barge_window_hops()
    script = ([[0.9]] + [[0.05]] * span) * 6
    _install_fake_voice_vad(monkeypatch, script)
    _capture_pipeline(monkeypatch)

    sid = "sid-barge-stale"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.PROCESSING
    local = _FakeLocalParticipant()

    _run(v._collect_audio_stream(_make_track(mod, _silence_frames(len(script))), sid, ps_map, local))

    assert ps_map[sid]["state"] == v._ParticipantState.PROCESSING, \
        "speech hops spread beyond the rolling window must not accumulate into a barge"
    assert all(m.get("type") != "stop_playback" for m in local.sent)


# ── 5. Participant removed mid-stream, and COOLDOWN expiry ─────────────────

def test_participant_removed_mid_stream_stops_cleanly(monkeypatch):
    """A disconnect drops the sid from the map. The frame loop must break — not
    raise, and not keep draining a track for a participant that is gone."""
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _capture_pipeline(monkeypatch)

    sid = "sid-gone"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    frames = _speech_frames(2) + _silence_frames(40)
    track = _make_track(mod, frames)

    original = track.__anext__.__func__

    async def _anext(self):
        event = await original(self)
        if self.consumed == 3:
            ps_map.pop(sid, None)      # participant disconnects mid-stream
        return event

    track.__class__.__anext__ = _anext

    _run(v._collect_audio_stream(track, sid, ps_map, local))

    assert sid not in ps_map
    # The lookup is the first thing in the loop body, so the frame delivered in
    # the same step the participant vanished is the last one consumed.
    assert track.consumed == 3, (
        f"consumed {track.consumed} of {len(frames)} frames — the loop must break "
        f"as soon as the participant vanished, not drain the whole track"
    )


def test_cooldown_watchdog_expires_to_idle_and_rearms(monkeypatch):
    """COOLDOWN is not permanent: a browser that never reports playback_done
    must still be returned to IDLE with both counters re-armed, or the
    participant is deaf forever."""
    sid = "sid-cooldown"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 0.0     # already expired
    ps_map[sid]["speech_count"] = 7
    ps_map[sid]["silence_count"] = 9

    async def _body():
        task = asyncio.ensure_future(v._cooldown_watchdog(ps_map))
        try:
            # The watchdog sleeps 1.0s before its first sweep — poll rather than
            # racing a fixed sleep on a loaded box.
            for _ in range(60):
                await asyncio.sleep(0.05)
                if ps_map[sid]["state"] == v._ParticipantState.IDLE:
                    return True
            return False
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert _run(_body()), "expired COOLDOWN must return to IDLE"
    assert ps_map[sid]["speech_count"] == 0
    assert ps_map[sid]["silence_count"] == 0


def test_cooldown_watchdog_respects_a_live_deadline(monkeypatch):
    """Negative control for the test above: a deadline in the future must be
    left alone (otherwise the watchdog would just reset everything on sight)."""
    sid = "sid-cooldown-live"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 10 ** 12

    async def _body():
        task = asyncio.ensure_future(v._cooldown_watchdog(ps_map))
        try:
            for _ in range(30):
                await asyncio.sleep(0.05)
                if ps_map[sid]["state"] != v._ParticipantState.COOLDOWN:
                    return False
            return True
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert _run(_body()), "an unexpired COOLDOWN must not be reset"


# ── 6. Real recordings — corpus when present, synthetic always ─────────────

_CORPUS_STRIDE = 37              # a stable spread, not one end of the corpus
_CORPUS_SLICE = 7                # members evaluated — a population, not a pick


def _corpus_utterance_slice(limit=_CORPUS_SLICE):
    """Up to `limit` (name, frames) pairs from real 16 kHz mono recordings.

    `~/.zoe-voice-samples` is a live, growing, UNCURATED save-everything capture,
    so this returns a deterministic SLICE, not a chosen file. Off-format members
    (24 kHz resamples, one non-RIFF file) and near-silent false wakes are skipped
    as known corpus facts rather than failed on — see
    services/zoe-data/tests/AGENTS.md. The caller then asserts a corpus-LEVEL
    property over the slice; binding to `[0]` is what left test_voice_barge_in.py
    red for weeks with the code and the model untouched.
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
                        handle.getsampwidth()) != (_RATE, 1, 2):
                    continue
                if handle.getnframes() < _RATE:      # ≥1s
                    continue
                raw = handle.readframes(handle.getnframes())
        except Exception:
            continue
        frames = _framed(raw)
        loud = sum(1 for f in frames if v._rms(f) >= v._VAD_ENERGY_THRESHOLD)
        if loud >= v._VAD_MIN_SPEECH_FRAMES * 4:     # a real utterance, not a false wake
            picked.append((name, frames))
    return picked


@pytest.fixture(params=["synthetic", "corpus"])
def utterance_population(request):
    """The audio under test, as a POPULATION in both arms.

    Synthetic is a population of one — deterministic, so "strict majority" means
    it must hold exactly. Corpus is the strided slice. Identical assertions run
    over both, which is what makes keeping a CI arm worth anything.
    """
    if request.param == "corpus":
        members = _corpus_utterance_slice()
        if not members:
            pytest.skip(f"no usable 16k mono recording in {_SAMPLES_DIR} (CI has no corpus)")
        return members
    return [("synthetic", _speech_frames(60))]


def _segment_one(monkeypatch, frames):
    """Drive `frames` + the silence window through _collect_audio_stream once.

    Returns the pipeline calls, the local participant, the participant state,
    and the DRIVEN frame list — the last one so callers can ask where inside the
    input an utterance was cut.
    """
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    calls = _capture_pipeline(monkeypatch)

    sid = "sid-real"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()
    driven = frames + _silence_frames(v._VAD_SILENCE_FRAMES)

    async def _body():
        await v._collect_audio_stream(_make_track(mod, driven), sid, ps_map, local)
        await asyncio.sleep(0)

    _run(_body())
    return calls, local, ps_map[sid], driven


def _is_loud(frame):
    return v._rms(frame) >= v._VAD_ENERGY_THRESHOLD


def _trailing_silence(frames):
    """How many frames at the END of `frames` are below the energy gate."""
    count = 0
    for frame in reversed(frames):
        if _is_loud(frame):
            break
        count += 1
    return count


def _locate(driven, utterance):
    """Index where `utterance` sits inside `driven` as a contiguous run, or -1."""
    span = len(utterance)
    for start in range(len(driven) - span + 1):
        if driven[start:start + span] == utterance:
            return start
    return -1


def _assert_turn_is_whole(label, utterance, driven):
    """ONE TURN IS NOT THE SAME AS THE RIGHT TURN.

    `_collect_audio_stream` moves to PROCESSING at the endpoint and ignores
    frames until COOLDOWN expires, so a PREMATURE cut also yields exactly one
    pipeline call — `len(calls) == 1` on its own cannot tell a clean turn from a
    clipped one. Two properties can:

      (a) the cut was caused by a real silence run — the utterance ends with at
          least `_VAD_SILENCE_FRAMES` sub-threshold frames, which IS the endpoint
          condition. Measured across the sampled corpus: exactly 20/20 every
          time. An arbitrary cut would not land there.
      (b) nothing spoken BEFORE the cut was dropped. Only the pre-roll ahead of
          speech confirmation may be missing, so the tolerance is the
          confirmation window itself rather than a magic number (measured 0-4
          against a window of 5).

    Deliberately NOT asserted: that speech AFTER the endpoint survives. The
    segmenter is designed to endpoint on a pause and start a NEW turn once
    cooldown expires; this single-shot harness feeds the whole recording in
    microseconds, so the remainder legitimately lands in PROCESSING/COOLDOWN and
    is ignored — exactly as it would be on the box. That is why a raw
    captured/source coverage RATIO would be the wrong assertion: measured
    0.02-1.00 across the corpus with segmentation behaving correctly.
    """
    tail = _trailing_silence(utterance)
    assert tail >= v._VAD_SILENCE_FRAMES, (
        f"[{label}] the utterance ends with only {tail} silent frames, fewer than "
        f"the {v._VAD_SILENCE_FRAMES}-frame endpoint window — it was cut "
        f"mid-speech, not endpointed"
    )
    at = _locate(driven, utterance)
    assert at >= 0, f"[{label}] the utterance is not a contiguous run of the input"
    kept = sum(1 for f in utterance if _is_loud(f))
    dropped = sum(1 for f in driven[:at + len(utterance)] if _is_loud(f)) - kept
    assert dropped <= v._VAD_MIN_SPEECH_FRAMES, (
        f"[{label}] {dropped} speech frames before the endpoint never reached STT "
        f"(only the {v._VAD_MIN_SPEECH_FRAMES}-frame speech-confirmation pre-roll "
        f"may be lost) — the turn was clipped"
    )


def test_real_utterance_segments_into_one_turn(monkeypatch, utterance_population):
    """End to end on actual audio: a recording plus the silence window must
    produce exactly one utterance, and it must still contain the speech.

    Asserted as a CORPUS-LEVEL property. The corpus is uncurated and growing, so
    an individual member can legitimately be a multi-utterance clip, background
    TV, or a recording holding a pause longer than the silence window; requiring
    one arbitrary file to segment into exactly one turn reddens unrelated builds
    while segmentation behaviour is unchanged. So: a strict MAJORITY must yield
    exactly one turn, while every member that endpoints at all must keep its
    speech and clear its buffer — those stay per-member and falsifiable.
    """
    verdicts = []
    for label, frames in utterance_population:
        calls, local, state, driven = _segment_one(monkeypatch, frames)
        verdicts.append((label, len(calls)))

        if calls:
            utterance = calls[0]["frames"]
            assert {"type": "state", "state": "listening"} in local.sent, \
                f"[{label}] speech-start must be broadcast to the browser"
            loud = sum(1 for f in utterance if _is_loud(f))
            assert loud >= v._VAD_MIN_SPEECH_FRAMES, (
                f"[{label}] the utterance handed to STT holds only {loud} speech "
                f"frames — the segmentation clipped the speech"
            )
            assert state["frames"] == [], f"[{label}] buffer must be cleared after the turn"

            _assert_turn_is_whole(label, utterance, driven)

    singles = sum(1 for _, n in verdicts if n == 1)
    assert singles * 2 > len(verdicts), (
        f"only {singles}/{len(verdicts)} recordings segmented into exactly one "
        f"utterance — zero means it never endpointed, more than one means the "
        f"segmenter cut the speaker off mid-sentence. Per-member counts: {verdicts}"
    )


def test_majority_assertion_can_actually_fail(monkeypatch, utterance_population):
    """NEGATIVE CONTROL for the majority: a broken segmenter must break it.

    A "a majority passed" assertion is exactly the shape that quietly stops
    proving anything — loosen a threshold and it stays green forever. So run the
    same population through a segmenter that can never endpoint (nothing clears
    the energy gate) and require the majority to be LOST. The population itself
    is selected with the real threshold first, so this breaks the code under
    test, not the fixture.
    """
    monkeypatch.setattr(v, "_VAD_ENERGY_THRESHOLD", 10 ** 9)
    counts = [len(_segment_one(monkeypatch, frames)[0]) for _, frames in utterance_population]
    singles = sum(1 for n in counts if n == 1)
    assert singles * 2 <= len(counts), (
        f"a segmenter that can never detect speech still produced {singles}/"
        f"{len(counts)} single-turn results — the majority assertion above is "
        f"not measuring segmentation at all"
    )


@pytest.mark.parametrize("regression", ["trim_the_trailing_silence", "drop_the_opening"])
def test_clipping_is_actually_detected(monkeypatch, utterance_population, regression):
    """NEGATIVE CONTROL for `_assert_turn_is_whole`: clipped turns must be caught.

    Both variants leave `len(calls) == 1` — which is the whole point. They break
    the real seam (`_schedule_pipeline`, where the buffered utterance is handed
    to the pipeline) with the two plausible regressions: someone "tidying up"
    the trailing silence before STT, and an off-by-a-lot in the snapshot start.
    """
    real_schedule = v._schedule_pipeline

    def _clipping(sid, ps, local_participant, frames_snapshot):
        if regression == "trim_the_trailing_silence":
            clipped = frames_snapshot[:-v._VAD_SILENCE_FRAMES]
        else:
            clipped = frames_snapshot[len(frames_snapshot) // 2:]
        return real_schedule(sid, ps, local_participant, clipped or frames_snapshot)

    monkeypatch.setattr(v, "_schedule_pipeline", _clipping)

    caught = 0
    checked = 0
    for label, frames in utterance_population:
        calls, _local, _state, driven = _segment_one(monkeypatch, frames)
        if len(calls) != 1:
            continue
        checked += 1
        try:
            _assert_turn_is_whole(label, calls[0]["frames"], driven)
        except AssertionError:
            caught += 1
    assert checked, "the clipping control produced no single-turn results to check"
    assert caught == checked, (
        f"{regression}: only {caught}/{checked} clipped turns were detected — "
        f"`len(calls) == 1` plus these properties is not distinguishing a clean "
        f"turn from a truncated one"
    )


def test_corpus_slice_is_a_population_not_a_pick():
    """CONTROL for the test above: it must be reading more than one recording.

    If the slice ever collapses to a single member on the box, the majority
    assertion silently degenerates back into the per-file binding it replaced —
    still green, and just as fragile. Off-box there is no corpus and nothing to
    check, so this skips rather than failing CI.
    """
    members = _corpus_utterance_slice()
    if not members:
        pytest.skip(f"no corpus at {_SAMPLES_DIR} (CI has none) — nothing to sample")
    assert len({name for name, _ in members}) == len(members), "duplicate members sampled"
    assert len(members) >= 3, (
        f"only {len(members)} usable recording(s) found by a stride-{_CORPUS_STRIDE} "
        f"walk — the majority assertion needs a population. Either the corpus "
        f"shrank or the format filter now rejects too much."
    )
