"""P-F3 — LiveKit pipeline failure paths (brain hang + empty transcript).

Fake-harness tests in the ``test_voice_barge_in.py`` style: no network, no DB,
no LiveKit stack, no models. ``_run_pipeline`` imports its collaborators lazily
(``routers.voice_tts``, ``brain_dispatch``, ``voice_presence``), so each test
injects fake modules into ``sys.modules`` and drives the real pipeline:

- a brain that never returns must hit the ``ZOE_LIVEKIT_BRAIN_TIMEOUT_S`` bound
  and route through the existing ``llm_ok = False`` apology path (turn still
  completes, state still resets to COOLDOWN via ``_on_pipeline_done``);
- an empty transcript must produce exactly one audible TTS message
  ("Sorry, I didn't catch that.") before returning to ambient;
- if that feedback synth itself fails, behaviour degrades to today's silent
  return to ambient (never crashes the loop);
- the happy path (working brain) is regression-locked unchanged.
"""
import asyncio
import json
import sys
import types

import pytest

import routers.voice_livekit as v

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_APOLOGY = "Sorry, I had trouble with that."
_DIDNT_CATCH = "Sorry, I didn't catch that."


@pytest.fixture(autouse=True)
def _reset_voice_health():
    """Restore the shared module global between tests. The pipeline mutates
    `_VOICE_HEALTH` in place (pipeline_failures/successes, last_error), so an
    absolute assertion in one test would otherwise leak into the next — the
    isolation-leak class this repo fights. Production ships `_INITIAL_VOICE_HEALTH`
    (a deepcopy snapshot) for exactly this restore."""
    import copy
    v._VOICE_HEALTH.clear()
    v._VOICE_HEALTH.update(copy.deepcopy(v._INITIAL_VOICE_HEALTH))
    yield
    v._VOICE_HEALTH.clear()
    v._VOICE_HEALTH.update(copy.deepcopy(v._INITIAL_VOICE_HEALTH))


def _run(coro, timeout=5.0):
    """Drive a coroutine on a fresh loop with a hard test-level timeout —
    a wedged pipeline must fail the test, not hang the suite."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


class _FakeLocalParticipant:
    """Captures every data-channel message the agent broadcasts."""

    def __init__(self):
        self.sent = []

    async def publish_data(self, data, reliable=True):
        self.sent.append(json.loads(data.decode()))


class _FakeTTSResponse:
    body = b"RIFFfake-wav-bytes"
    media_type = "audio/wav"


def _install_fakes(monkeypatch, *, transcript, brain, synth=None):
    """Inject fake ``routers.voice_tts`` / ``brain_dispatch`` / ``voice_presence``
    modules so the real ``_run_pipeline`` runs its lazy imports against them.

    ``brain`` is an async callable; ``synth`` (optional) overrides the default
    recording synthesizer. Returns (brain_calls, synth_calls) recorders."""
    brain_calls = []
    synth_calls = []

    vt = types.ModuleType("routers.voice_tts")

    async def _transcribe_audio(_path):
        return transcript

    async def _default_synth(payload, caller=None):
        synth_calls.append({"payload": payload, "caller": caller})
        return _FakeTTSResponse()

    vt._transcribe_audio = _transcribe_audio
    vt.synthesize = synth or _default_synth
    monkeypatch.setitem(sys.modules, "routers.voice_tts", vt)

    bd = types.ModuleType("brain_dispatch")

    async def _brain_oneshot(*args, **kwargs):
        brain_calls.append({"args": args, "kwargs": kwargs})
        return await brain(*args, **kwargs)

    bd.brain_oneshot = _brain_oneshot
    monkeypatch.setitem(sys.modules, "brain_dispatch", bd)

    vp = types.ModuleType("voice_presence")
    vp.processing_ack_event = lambda: None
    monkeypatch.setitem(sys.modules, "voice_presence", vp)

    # Deterministic: the fast tier must never answer for these transcripts.
    async def _no_fast_tier(*_a, **_k):
        return None

    monkeypatch.setattr(v, "_maybe_fast_tier", _no_fast_tier)
    return brain_calls, synth_calls


def _pipeline_with_state_wiring(local):
    """Run the pipeline exactly as _schedule_pipeline wires it: PROCESSING state
    plus the _on_pipeline_done callback, so 'state reset' is really exercised."""
    sid = "sid-failure-paths"
    ps = v._make_participant_state(sid)
    ps["state"] = v._ParticipantState.PROCESSING

    async def _body():
        task = asyncio.ensure_future(
            v._run_pipeline(local, [b"\x00\x00" * 160], "jason", "sess-1")
        )
        ps["pipeline_task"] = task
        task.add_done_callback(lambda _t: v._on_pipeline_done(sid, ps))
        await task
        await asyncio.sleep(0)  # let the done callback run
        return ps

    return _body()


# ── 1. Brain hang → timeout → existing apology path ──────────────────────────


def test_brain_hang_times_out_to_apology_and_state_reset(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_BRAIN_TIMEOUT_S", "0.05")

    async def _hung_brain(*_a, **_k):
        await asyncio.sleep(3600)  # never returns within any sane test window

    brain_calls, synth_calls = _install_fakes(
        monkeypatch, transcript="what time is it", brain=_hung_brain
    )
    local = _FakeLocalParticipant()
    failures_before = v._VOICE_HEALTH["pipeline_failures"]
    successes_before = v._VOICE_HEALTH["pipeline_successes"]

    ps = _run(_pipeline_with_state_wiring(local))

    assert len(brain_calls) == 1
    assert {"type": "transcript", "role": "zoe", "text": _APOLOGY} in local.sent, \
        "timeout must reuse the existing llm_ok=False apology"
    assert {"type": "done"} in local.sent, "the turn must still complete"
    assert any(m.get("type") == "audio" for m in local.sent), \
        "the apology must be spoken (TTS still runs)"
    assert synth_calls and synth_calls[-1]["payload"]["text"] == _APOLOGY
    assert ps["state"] == v._ParticipantState.COOLDOWN, \
        "_on_pipeline_done must reset the state exactly like any finished turn"
    assert v._VOICE_HEALTH["pipeline_failures"] == failures_before + 1
    assert v._VOICE_HEALTH["pipeline_successes"] == successes_before, \
        "a timed-out turn must not count as a pipeline success"
    # last_stage moves on to "tts" (the apology is spoken); the timeout stays
    # recorded in last_error because the failed turn never clears it.
    assert v._VOICE_HEALTH["last_error"] == "brain_oneshot timed out"


def test_brain_timeout_default_is_20s_when_env_unset(monkeypatch):
    """Exercise production: with the env unset, the pipeline must wrap the brain
    call in a 20s wait_for. Capturing the actual timeout voice_livekit passes
    (not re-testing os.environ) means changing the "20" default breaks this."""
    monkeypatch.delenv("ZOE_LIVEKIT_BRAIN_TIMEOUT_S", raising=False)

    seen_timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _recording_wait_for(aw, timeout=None):
        seen_timeouts.append(timeout)
        return await real_wait_for(aw, timeout=timeout)

    monkeypatch.setattr(v.asyncio, "wait_for", _recording_wait_for)

    async def _fast_brain(*_a, **_k):
        return "It is noon."

    _install_fakes(monkeypatch, transcript="what time is it", brain=_fast_brain)
    local = _FakeLocalParticipant()
    _run(_pipeline_with_state_wiring(local))

    assert 20.0 in seen_timeouts, \
        f"brain call must default to a 20s timeout when the env is unset; saw {seen_timeouts}"


# ── 2. Empty transcript → one audible canned line, then ambient ───────────────


def test_empty_transcript_sends_exactly_one_tts_message(monkeypatch):
    async def _must_not_be_called(*_a, **_k):
        raise AssertionError("brain must not run on an empty transcript")

    brain_calls, synth_calls = _install_fakes(
        monkeypatch, transcript="", brain=_must_not_be_called
    )
    local = _FakeLocalParticipant()

    _run(v._run_pipeline(local, [b"\x00\x00" * 160], "jason", "sess-2"))

    assert brain_calls == []
    audio_msgs = [m for m in local.sent if m.get("type") == "audio"]
    assert len(audio_msgs) == 1, "empty transcript must send exactly one TTS message"
    assert len(synth_calls) == 1
    assert synth_calls[0]["payload"]["text"] == _DIDNT_CATCH
    assert synth_calls[0]["caller"] == {"source": "livekit", "user_id": "jason"}
    assert {"type": "transcript", "role": "zoe", "text": _DIDNT_CATCH} in local.sent
    assert local.sent[-1] == {"type": "state", "state": "ambient"}, \
        "the turn must still return to ambient"


def test_empty_transcript_synth_failure_degrades_to_silent_ambient(monkeypatch):
    async def _must_not_be_called(*_a, **_k):
        raise AssertionError("brain must not run on an empty transcript")

    async def _broken_synth(_payload, caller=None):
        raise RuntimeError("kokoro down")

    _install_fakes(
        monkeypatch, transcript="", brain=_must_not_be_called, synth=_broken_synth
    )
    local = _FakeLocalParticipant()

    _run(v._run_pipeline(local, [b"\x00\x00" * 160], "jason", "sess-3"))

    # Today's exact behaviour: thinking, then silent return to ambient.
    assert local.sent == [
        {"type": "state", "state": "thinking"},
        {"type": "state", "state": "ambient"},
    ], "a broken feedback synth must degrade to the pre-P-F3 silent path"


# ── 3. Happy path regression lock ─────────────────────────────────────────────


def test_happy_path_unchanged(monkeypatch):
    monkeypatch.delenv("ZOE_LIVEKIT_BRAIN_TIMEOUT_S", raising=False)

    async def _brain(*_a, **_k):
        return "It's three o'clock."

    brain_calls, synth_calls = _install_fakes(
        monkeypatch, transcript="what time is it", brain=_brain
    )
    local = _FakeLocalParticipant()
    successes_before = v._VOICE_HEALTH["pipeline_successes"]

    ps = _run(_pipeline_with_state_wiring(local))

    assert len(brain_calls) == 1
    assert {"type": "transcript", "role": "zoe", "text": "It's three o'clock."} in local.sent
    assert sum(1 for m in local.sent if m.get("type") == "audio") == 1
    assert synth_calls[-1]["payload"]["text"] == "It's three o'clock."
    assert {"type": "done"} in local.sent
    assert ps["state"] == v._ParticipantState.COOLDOWN
    assert v._VOICE_HEALTH["pipeline_successes"] == successes_before + 1
