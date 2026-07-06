"""Barge-in (ZOE_VOICE_BARGE_IN) + Silero VAD tests for the LiveKit voice agent.

Two layers:

1. State-machine tests with a FAKE ``voice_vad`` module injected into
   ``sys.modules`` (no model, no numpy inference — CI-safe). They drive
   ``routers.voice_livekit._collect_audio_stream`` directly through the aiortc
   track branch, mirroring ``test_voice_livekit_lifecycle.py``:
     - flag OFF → frames during PROCESSING/COOLDOWN are ignored and Silero is
       never touched (regression lock on today's half-duplex behaviour);
     - flag ON → sustained speech during COOLDOWN triggers stop_playback +
       LISTENING seeded with the interrupting frames;
     - brief noise (< the sustained-speech hop gate) does NOT trigger;
     - sustained speech during PROCESSING cancels the pipeline task;
     - Silero drives IDLE → LISTENING → endpoint when the flag is on;
     - Silero unavailable → legacy RMS path even with the flag on.

2. Silero wrapper tests against the REAL model — host-only, skipped when
   /home/zoe/models/silero_vad.onnx is absent (e.g. GitHub runners).

No network, no DB, no LiveKit stacks.
"""
import asyncio
import json
import os
import struct
import sys
import types

import pytest

import routers.voice_livekit as v

_MODEL_PATH = "/home/zoe/models/silero_vad.onnx"
_SAMPLES_DIR = "/home/zoe/.zoe-voice-samples"


def _run(coro):
    """Drive a coroutine on a fresh loop — keeps each case isolated (mirrors
    test_voice_livekit_lifecycle)."""
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


def _loud(n: int = 160) -> bytes:
    """A well-above-RMS-threshold int16 frame (5000 >> _VAD_ENERGY_THRESHOLD)."""
    return struct.pack(f"<{n}h", *([5000] * n))


def _silence(n: int = 160) -> bytes:
    return b"\x00\x00" * n


def _install_fake_aiortc(monkeypatch):
    """Minimal fake ``livekit_aiortc`` so _collect_audio_stream takes the aiortc
    branch (no native livekit import) and iterates the track itself."""
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

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._events:
                return self._events.pop(0)
            raise StopAsyncIteration

    return _Track()


class _FakeLocalParticipant:
    """Captures every data-channel message the agent broadcasts."""

    def __init__(self):
        self.sent = []

    async def publish_data(self, data, reliable=True):
        self.sent.append(json.loads(data.decode()))


def _install_fake_voice_vad(monkeypatch, hop_probs_per_frame):
    """Inject a fake ``voice_vad`` whose per-stream VAD replays a script:
    one list of hop probabilities per incoming frame ([] = no hop completed)."""
    mod = types.ModuleType("voice_vad")
    mod.HOP_MS = 32.0
    mod.HOP_SAMPLES = 512
    mod.speech_threshold = lambda: 0.5
    created = []

    class _FakeVAD:
        def __init__(self, script):
            self._script = list(script)

        def reset(self):
            pass

        def process_hops(self, _raw):
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
    # Tests must not inherit ambient/prod env (these flags are now ON in the
    # live .env — an inherited ZOE_SMART_TURN_ENABLED turns the plain silence
    # endpoint into a smart-turn check and breaks the endpoint tests).
    monkeypatch.delenv("ZOE_BARGE_MIN_MS", raising=False)
    monkeypatch.delenv("ZOE_VAD_SPEECH_THRESHOLD", raising=False)
    monkeypatch.delenv("ZOE_BARGE_SPEECH_THRESHOLD", raising=False)
    monkeypatch.delenv("ZOE_SMART_TURN_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_SMART_TURN_THRESHOLD", raising=False)
    monkeypatch.delenv("ZOE_SMART_TURN_MAX_CHECKS", raising=False)


# ── 1. Flag OFF: today's behaviour, locked ───────────────────────────────────


@pytest.mark.parametrize("busy_state", [v._ParticipantState.PROCESSING, v._ParticipantState.COOLDOWN])
def test_flag_off_busy_states_ignore_frames(monkeypatch, busy_state):
    """Regression lock: with ZOE_VOICE_BARGE_IN unset, frames arriving during
    PROCESSING/COOLDOWN are ignored (no buffering, no messages, no Silero)."""
    monkeypatch.delenv("ZOE_VOICE_BARGE_IN", raising=False)
    mod = _install_fake_aiortc(monkeypatch)
    vad_mod = _install_fake_voice_vad(monkeypatch, [[0.99]] * 50)

    sid = "sid-flagoff1"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = busy_state
    ps_map[sid]["cooldown_deadline"] = 10**12  # far future — watchdog irrelevant
    local = _FakeLocalParticipant()

    track = _make_track(mod, [_loud()] * 30)  # loud sustained "speech"
    _run(v._collect_audio_stream(track, sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == busy_state, "flag OFF must keep ignoring frames while busy"
    assert ps["frames"] == []
    assert local.sent == []
    assert vad_mod._created == [], "flag OFF must never instantiate Silero"


def test_flag_off_idle_to_listening_via_rms(monkeypatch):
    """Regression lock: legacy RMS path (IDLE → LISTENING on energy) unchanged."""
    monkeypatch.delenv("ZOE_VOICE_BARGE_IN", raising=False)
    mod = _install_fake_aiortc(monkeypatch)
    vad_mod = _install_fake_voice_vad(monkeypatch, [[0.99]] * 50)
    monkeypatch.setattr(v, "_prewarm_brain", _noop_prewarm)

    sid = "sid-flagoff2"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    track = _make_track(mod, [_loud()] * (v._VAD_MIN_SPEECH_FRAMES + 1))
    _run(v._collect_audio_stream(track, sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.LISTENING
    assert len(ps["frames"]) >= v._VAD_MIN_SPEECH_FRAMES
    assert {"type": "state", "state": "listening"} in local.sent
    assert vad_mod._created == []


# ── 2. Flag ON: barge-in ─────────────────────────────────────────────────────


def test_flag_on_cooldown_barge_triggers_stop_playback_and_seeds_frames(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    # 10 frames, one confident speech hop each → crosses the 8-hop (250ms) gate.
    _install_fake_voice_vad(monkeypatch, [[0.9]] * 10)
    monkeypatch.setattr(v, "_prewarm_brain", _noop_prewarm)

    sid = "sid-barge-cd"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 10**12
    local = _FakeLocalParticipant()
    barge_ins_before = v._VOICE_HEALTH["barge_ins"]

    track = _make_track(mod, [_loud()] * 10)
    _run(v._collect_audio_stream(track, sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.LISTENING
    assert {"type": "stop_playback"} in local.sent
    assert {"type": "state", "state": "listening"} in local.sent
    assert len(ps["frames"]) >= v._barge_min_hops(), \
        "the interrupting speech must seed the next utterance"
    assert ps["barge_window"] == [] and ps["barge_frames"] == []
    assert ps["cooldown_deadline"] == 0.0, "cooldown watchdog state must be reset"
    assert v._VOICE_HEALTH["barge_ins"] == barge_ins_before + 1


def test_flag_on_brief_noise_does_not_trigger(monkeypatch):
    """< min-hops of speech (echo blip / cough) must NOT interrupt Zoe."""
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    # Genuinely brief noise: 4 speech hops (~128ms — under the 6-hop/192ms
    # windowed gate) then sustained silence. A cough/echo blip, not speech.
    script = [[0.9]] * 4 + [[0.05]] * 14 + [[]] * 2
    _install_fake_voice_vad(monkeypatch, script)
    monkeypatch.setattr(v, "_prewarm_brain", _noop_prewarm)

    sid = "sid-barge-noise"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 10**12
    local = _FakeLocalParticipant()

    track = _make_track(mod, [_loud()] * len(script))
    _run(v._collect_audio_stream(track, sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.COOLDOWN, "brief noise must not barge in"
    assert all(m.get("type") != "stop_playback" for m in local.sent)
    assert ps["frames"] == []


def test_flag_on_processing_barge_cancels_pipeline_task(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    _install_fake_voice_vad(monkeypatch, [[0.9]] * 10)
    monkeypatch.setattr(v, "_prewarm_brain", _noop_prewarm)

    sid = "sid-barge-proc"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    async def _body():
        ps = ps_map[sid]
        ps["state"] = v._ParticipantState.PROCESSING
        pipeline = asyncio.ensure_future(asyncio.sleep(30))
        ps["pipeline_task"] = pipeline
        # Same wiring as _schedule_pipeline: the done callback must not clobber
        # the post-barge LISTENING state when the cancelled task settles.
        pipeline.add_done_callback(lambda _t: v._on_pipeline_done(sid, ps))

        track = _make_track(mod, [_loud()] * 10)
        await v._collect_audio_stream(track, sid, ps_map, local)
        await asyncio.gather(pipeline, return_exceptions=True)
        await asyncio.sleep(0)  # let the done callback run
        return pipeline

    pipeline = _run(_body())
    ps = ps_map[sid]
    assert pipeline.cancelled(), "barge-in during PROCESSING must cancel the pipeline"
    assert ps["state"] == v._ParticipantState.LISTENING, \
        "_on_pipeline_done must not flip a barged turn into COOLDOWN"
    assert {"type": "stop_playback"} in local.sent
    assert len(ps["frames"]) > 0


def test_flag_on_silero_drives_idle_listening_endpoint(monkeypatch):
    """With the flag on, Silero (not RMS) drives IDLE→LISTENING and the ~600ms
    silence endpoint, then the pipeline gets the buffered utterance."""
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    speech = [[0.9]] * v._VAD_MIN_SPEECH_FRAMES
    silence = [[0.1]] * v._VAD_SILENCE_FRAMES
    _install_fake_voice_vad(monkeypatch, speech + silence)
    monkeypatch.setattr(v, "_prewarm_brain", _noop_prewarm)

    calls = []

    async def _fake_pipeline(local_participant, frames, user_id, session_id):
        calls.append({"frames": frames, "user_id": user_id, "session_id": session_id})

    monkeypatch.setattr(v, "_run_pipeline", _fake_pipeline)

    sid = "sid-silero-ep"
    ps_map = {sid: v._make_participant_state(sid)}
    local = _FakeLocalParticipant()

    # Quiet frames (RMS ~0) — only Silero can see this "speech".
    n_frames = v._VAD_MIN_SPEECH_FRAMES + v._VAD_SILENCE_FRAMES
    track = _make_track(mod, [_silence()] * n_frames)

    async def _body():
        await v._collect_audio_stream(track, sid, ps_map, local)
        await asyncio.sleep(0)  # let the pipeline task + done callback settle

    _run(_body())

    ps = ps_map[sid]
    assert {"type": "state", "state": "listening"} in local.sent
    assert len(calls) == 1, "silence endpoint must schedule exactly one pipeline"
    assert len(calls[0]["frames"]) > 0
    assert ps["state"] == v._ParticipantState.COOLDOWN  # via _on_pipeline_done


def test_flag_on_but_silero_unavailable_falls_back_to_rms(monkeypatch):
    """Model can't load → legacy RMS path even with the flag on (busy frames
    ignored, vad_failed latched so we don't retry per frame)."""
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    mod = _install_fake_aiortc(monkeypatch)
    vad_mod = types.ModuleType("voice_vad")
    vad_mod.HOP_MS = 32.0
    vad_mod.speech_threshold = lambda: 0.5
    vad_mod.create_vad = lambda: None  # graceful-degradation contract
    monkeypatch.setitem(sys.modules, "voice_vad", vad_mod)

    sid = "sid-degraded"
    ps_map = {sid: v._make_participant_state(sid)}
    ps_map[sid]["state"] = v._ParticipantState.COOLDOWN
    ps_map[sid]["cooldown_deadline"] = 10**12
    local = _FakeLocalParticipant()

    track = _make_track(mod, [_loud()] * 10)
    _run(v._collect_audio_stream(track, sid, ps_map, local))

    ps = ps_map[sid]
    assert ps["state"] == v._ParticipantState.COOLDOWN
    assert ps["vad_failed"] is True
    assert local.sent == []


def test_barge_min_hops_env_knob(monkeypatch):
    _clean_env(monkeypatch)
    assert v._barge_min_hops() == 6  # 192ms / 32ms → ceil = 6
    monkeypatch.setenv("ZOE_BARGE_MIN_MS", "500")
    assert v._barge_min_hops() == 16
    monkeypatch.setenv("ZOE_BARGE_MIN_MS", "garbage")
    assert v._barge_min_hops() == 6
    monkeypatch.setenv("ZOE_BARGE_MIN_MS", "1")
    assert v._barge_min_hops() == 1


def test_barge_in_flag_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_VOICE_BARGE_IN", raising=False)
    assert v._barge_in_enabled() is False
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    assert v._barge_in_enabled() is True
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "0")
    assert v._barge_in_enabled() is False


# ── 3. Silero wrapper against the real model (host-only) ────────────────────

_needs_model = pytest.mark.skipif(
    not os.path.isfile(_MODEL_PATH), reason=f"Silero model not present at {_MODEL_PATH}"
)


def _fresh_voice_vad(monkeypatch):
    """Import the real module with a clean singleton + default model path.

    Globals are reset via monkeypatch.setattr (repo rule: pytest restores them
    automatically even if a test is interrupted mid-run)."""
    monkeypatch.delenv("ZOE_SILERO_VAD_MODEL", raising=False)
    monkeypatch.delenv("ZOE_VAD_SPEECH_THRESHOLD", raising=False)
    import voice_vad
    monkeypatch.setattr(voice_vad, "_session", None)
    monkeypatch.setattr(voice_vad, "_session_failed", False)
    monkeypatch.setattr(voice_vad, "_warned", False)
    return voice_vad


@pytest.fixture
def real_voice_vad(monkeypatch):
    # monkeypatch teardown restores the singleton globals automatically.
    yield _fresh_voice_vad(monkeypatch)


@_needs_model
def test_silero_real_model_speech_sample_detected(real_voice_vad):
    import wave

    wavs = sorted(
        os.path.join(_SAMPLES_DIR, f)
        for f in os.listdir(_SAMPLES_DIR)
        if f.endswith(".wav")
    ) if os.path.isdir(_SAMPLES_DIR) else []
    if not wavs:
        pytest.skip("no voice samples available")

    vad = real_voice_vad.create_vad()
    assert vad is not None
    with wave.open(wavs[0], "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        raw = w.readframes(w.getnframes())
    max_prob = 0.0
    for i in range(0, len(raw), 640):  # feed as 20ms frames
        max_prob = max(max_prob, vad.process(raw[i:i + 640]))
    assert max_prob > 0.5, f"real speech should exceed the 0.5 threshold (got {max_prob:.3f})"


@_needs_model
def test_silero_real_model_near_silence_stays_low(real_voice_vad):
    import numpy as np

    vad = real_voice_vad.create_vad()
    assert vad is not None
    noise = (np.random.randn(16000) * 80).astype(np.int16).tobytes()
    max_prob = 0.0
    for i in range(0, len(noise), 640):
        max_prob = max(max_prob, vad.process(noise[i:i + 640]))
    assert max_prob < 0.4, f"noise floor should stay below 0.4 (got {max_prob:.3f})"


@_needs_model
def test_silero_reset_and_partial_hops(real_voice_vad):
    vad = real_voice_vad.create_vad()
    assert vad is not None
    # 100 samples — no full 512-sample hop completes → 0.0 by contract.
    assert vad.process(b"\x00\x00" * 100) == 0.0
    vad.reset()
    assert vad.process(b"") == 0.0


def test_silero_graceful_degradation_on_bogus_path(monkeypatch):
    import voice_vad

    monkeypatch.setenv("ZOE_SILERO_VAD_MODEL", "/nonexistent/silero_vad.onnx")
    monkeypatch.setattr(voice_vad, "_session", None)
    monkeypatch.setattr(voice_vad, "_session_failed", False)
    monkeypatch.setattr(voice_vad, "_warned", False)
    assert voice_vad.create_vad() is None, "missing model must degrade to None"
    assert voice_vad.create_vad() is None  # cached failure, still None, no raise


# ── Smart Turn v3 endpointing (V2, ZOE_SMART_TURN_ENABLED) ───────────────────
#
# The end-of-turn model refines the silence endpoint on the Silero path: at the
# silence window, score the buffered utterance — complete → end turn; mid-thought
# → extend the listen (bounded by ZOE_SMART_TURN_MAX_CHECKS, fail-open).


def _install_fake_voice_turn(monkeypatch, prob=None, delay_s=0.0):
    """Fake voice_turn module. prob=None → get_smart_turn() returns None
    (unavailable). Otherwise a detector whose end_of_turn_prob returns `prob`
    after `delay_s` (simulating the ~200ms inference in a worker thread)."""
    import time as _time

    mod = types.ModuleType("voice_turn")

    class _FakeDetector:
        def __init__(self):
            self.calls = 0

        def end_of_turn_prob(self, _pcm):
            self.calls += 1
            if delay_s:
                _time.sleep(delay_s)
            return prob if not callable(prob) else prob(self.calls)

    det = None if prob is None else _FakeDetector()
    mod.get_smart_turn = lambda: det
    mod._det = det
    monkeypatch.setitem(sys.modules, "voice_turn", mod)
    return mod


def _listening_ps(sid):
    ps = v._make_participant_state(sid)
    ps["state"] = v._ParticipantState.LISTENING
    ps["frames"] = [_loud()]
    return ps


def _capture_end_turn(monkeypatch):
    calls = []
    monkeypatch.setattr(v, "_schedule_pipeline", lambda *a, **k: calls.append(a))
    return calls


async def _feed_silence_until_window(ps, vad_mod, local, sid, n=None):
    frames = n if n is not None else v._VAD_SILENCE_FRAMES
    for _ in range(frames):
        await v._handle_frame_barge_in(_silence(), sid, ps, local, ps["_vad"])


def _mk_vad_all_silence(monkeypatch, n_frames):
    return _install_fake_voice_vad(monkeypatch, [[0.0]] * n_frames)


def test_smart_turn_flag_off_silence_ends_turn_immediately(monkeypatch):
    """Regression lock: without ZOE_SMART_TURN_ENABLED the silence window ends
    the turn exactly as before — no smart-turn consultation."""
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    monkeypatch.delenv("ZOE_SMART_TURN_ENABLED", raising=False)
    _clean_env(monkeypatch)
    vad_mod = _mk_vad_all_silence(monkeypatch, v._VAD_SILENCE_FRAMES + 2)
    turn_mod = _install_fake_voice_turn(monkeypatch, prob=0.0)  # would say "incomplete"
    pipeline = _capture_end_turn(monkeypatch)

    sid = "sid-st-off"
    ps = _listening_ps(sid)
    ps["_vad"] = vad_mod.create_vad()
    local = _FakeLocalParticipant()
    _run(_feed_silence_until_window(ps, vad_mod, local, sid))

    assert pipeline, "flag off → silence window must end the turn immediately"
    assert ps["state"] == v._ParticipantState.PROCESSING
    assert turn_mod._det.calls == 0, "smart turn must not be consulted when off"


def test_smart_turn_complete_verdict_ends_turn(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_SMART_TURN_ENABLED", "1")
    vad_mod = _mk_vad_all_silence(monkeypatch, v._VAD_SILENCE_FRAMES + 2)
    _install_fake_voice_turn(monkeypatch, prob=0.92)
    pipeline = _capture_end_turn(monkeypatch)

    sid = "sid-st-done"
    ps = _listening_ps(sid)
    ps["_vad"] = vad_mod.create_vad()
    local = _FakeLocalParticipant()

    async def scenario():
        await _feed_silence_until_window(ps, vad_mod, local, sid)
        assert ps["turn_check_task"] is not None, "check task must launch at the window"
        await asyncio.wait_for(ps_task(ps), timeout=2)

    def ps_task(ps):
        return ps["turn_check_task"]

    _run(scenario())
    assert pipeline, "complete verdict must end the turn"
    assert ps["state"] == v._ParticipantState.PROCESSING
    assert ps["turn_checks"] == 0 and ps["turn_check_task"] is None


def test_smart_turn_incomplete_extends_then_bounded_end(monkeypatch):
    """Mid-thought verdict extends the listen (halved silence window); the
    MAX_CHECKS bound guarantees the turn still ends (fail-open, never hangs)."""
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_SMART_TURN_ENABLED", "1")
    monkeypatch.setenv("ZOE_SMART_TURN_MAX_CHECKS", "2")
    total = v._VAD_SILENCE_FRAMES * 3
    vad_mod = _mk_vad_all_silence(monkeypatch, total)
    _install_fake_voice_turn(monkeypatch, prob=0.05)  # always "mid-thought"
    pipeline = _capture_end_turn(monkeypatch)

    sid = "sid-st-extend"
    ps = _listening_ps(sid)
    ps["_vad"] = vad_mod.create_vad()
    local = _FakeLocalParticipant()

    async def scenario():
        # First window → check #1 → incomplete → extended (no pipeline yet).
        await _feed_silence_until_window(ps, vad_mod, local, sid)
        await asyncio.wait_for(ps["turn_check_task"], timeout=2)
        assert not pipeline, "first incomplete verdict must extend, not end"
        assert ps["turn_checks"] == 1
        assert 0 < ps["silence_count"] < v._VAD_SILENCE_FRAMES, "silence window must be halved"
        # Second window → check #2 → bound reached → turn ends despite verdict.
        await _feed_silence_until_window(ps, vad_mod, local, sid)
        await asyncio.wait_for(ps["turn_check_task"], timeout=2)

    _run(scenario())
    assert pipeline, "MAX_CHECKS bound must force the turn to end"
    assert ps["state"] == v._ParticipantState.PROCESSING


def test_smart_turn_speech_resume_discards_verdict(monkeypatch):
    """If speech resumes while the model is scoring, the verdict is moot —
    stay LISTENING with the new speech, no pipeline launch."""
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_SMART_TURN_ENABLED", "1")
    script = [[0.0]] * v._VAD_SILENCE_FRAMES + [[0.9]]  # silence window, then speech
    vad_mod = _install_fake_voice_vad(monkeypatch, script)
    _install_fake_voice_turn(monkeypatch, prob=0.99, delay_s=0.15)  # slow "complete"
    pipeline = _capture_end_turn(monkeypatch)

    sid = "sid-st-resume"
    ps = _listening_ps(sid)
    ps["_vad"] = vad_mod.create_vad()
    local = _FakeLocalParticipant()

    async def scenario():
        await _feed_silence_until_window(ps, vad_mod, local, sid)
        task = ps["turn_check_task"]
        assert task is not None
        # Speech resumes while the check is still scoring.
        await v._handle_frame_barge_in(_loud(), sid, ps, local, ps["_vad"])
        assert ps["silence_count"] == 0
        await asyncio.wait_for(task, timeout=2)

    _run(scenario())
    assert not pipeline, "verdict must be discarded when speech resumed"
    assert ps["state"] == v._ParticipantState.LISTENING


def test_smart_turn_unavailable_falls_back_to_immediate_end(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    monkeypatch.setenv("ZOE_SMART_TURN_ENABLED", "1")
    vad_mod = _mk_vad_all_silence(monkeypatch, v._VAD_SILENCE_FRAMES + 2)
    _install_fake_voice_turn(monkeypatch, prob=None)  # detector unavailable
    pipeline = _capture_end_turn(monkeypatch)

    sid = "sid-st-none"
    ps = _listening_ps(sid)
    ps["_vad"] = vad_mod.create_vad()
    local = _FakeLocalParticipant()
    _run(_feed_silence_until_window(ps, vad_mod, local, sid))

    assert pipeline, "model unavailable → legacy immediate end"
    assert ps["state"] == v._ParticipantState.PROCESSING


# ── real-voice barge replay (host-only) ───────────────────────────────────────
# The regression that motivated the rolling-window gate: with the original
# strictly-consecutive counter, 0/6 REAL voice clips triggered a barge (natural
# speech dips between syllables reset the counter). This replays Jason's actual
# saved voice through the real Silero model and the real state machine.

_CORPUS_DIR = "/home/zoe/.zoe-voice-samples"


@pytest.mark.skipif(
    not os.path.isfile(_MODEL_PATH) or not os.path.isdir(_CORPUS_DIR),
    reason="Silero model or voice corpus not on this host",
)
def test_real_voice_triggers_barge_from_cooldown(monkeypatch):
    import glob
    import wave

    monkeypatch.setenv("ZOE_VOICE_BARGE_IN", "1")
    _clean_env(monkeypatch)
    import voice_vad
    monkeypatch.setattr(voice_vad, "_session", None)
    monkeypatch.setattr(voice_vad, "_session_failed", False)
    monkeypatch.setattr(voice_vad, "_warned", False)

    fired = 0
    total = 0
    for wav_path in sorted(glob.glob(os.path.join(_CORPUS_DIR, "*.wav")))[-6:]:
        try:
            with wave.open(wav_path) as w:
                if w.getframerate() != 16000 or w.getnchannels() != 1:
                    continue
                raw = w.readframes(w.getnframes())
        except Exception:
            continue
        total += 1
        sid = f"replay-{total}"
        ps = v._make_participant_state(sid)
        ps["state"] = v._ParticipantState.COOLDOWN
        ps["cooldown_deadline"] = 10**12
        vad = voice_vad.create_vad()
        assert vad is not None
        local = _FakeLocalParticipant()

        async def replay():
            for i in range(0, len(raw), 640):
                await v._handle_frame_barge_in(raw[i : i + 640], sid, ps, local, vad)
                if any(m.get("type") == "stop_playback" for m in local.sent):
                    return True
            return False

        if _run(replay()):
            fired += 1
            assert ps["state"] == v._ParticipantState.LISTENING
            assert ps["frames"], "interrupting speech must seed the next utterance"
    if total < 3:
        pytest.skip("not enough loadable 16k mono clips")
    assert fired >= max(2, int(total * 0.6)), (
        f"real voice must reliably barge in: fired {fired}/{total}"
    )
