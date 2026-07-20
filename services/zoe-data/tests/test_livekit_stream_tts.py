"""P-W1.3 — sentence-streamed TTS in the LiveKit conversation lane.

Fake-harness tests in the ``test_livekit_failure_paths.py`` style: no network,
no DB, no LiveKit stack, no models. ``_run_pipeline`` imports its collaborators
lazily, so each test injects fake modules into ``sys.modules`` and mocks
``_send_data`` to capture every data-channel message.

Locked behaviour:
- flag OFF (default): exactly ONE audio message, payload shape unchanged
  (no ``seq``/``final`` keys) — old clients keep working;
- flag ON: one audio message PER sentence with ordered 0-based ``seq`` and
  ``final`` set only on the last;
- barge-in cancel between sentences stops synthesis (no further messages).
"""
import asyncio
import sys
import types

import pytest

import routers.voice_livekit as v

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in the `-m ci_safe` lane

_THREE_SENTENCES = "First thing. Second thing. Third thing."


@pytest.fixture(autouse=True)
def _reset_voice_health():
    """Restore the shared module global between tests (pipeline mutates it)."""
    import copy
    v._VOICE_HEALTH.clear()
    v._VOICE_HEALTH.update(copy.deepcopy(v._INITIAL_VOICE_HEALTH))
    yield
    v._VOICE_HEALTH.clear()
    v._VOICE_HEALTH.update(copy.deepcopy(v._INITIAL_VOICE_HEALTH))


def _run(coro, timeout=5.0):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


class _FakeTTSResponse:
    def __init__(self, body=b"RIFFfake-wav-bytes"):
        self.body = body
        self.media_type = "audio/wav"


def _split_like_prod(text):
    """Stand-in for voice_tts._split_sentences (the real module is faked out)."""
    return [s.strip() for s in text.replace("!", ".").replace("?", ".").split(". ") if s.strip()]


def _install_fakes(monkeypatch, *, reply):
    """Fake routers.voice_tts / brain_dispatch / voice_presence and mock
    ``_send_data`` to record payloads. Returns (sent, synth_calls)."""
    sent = []
    synth_calls = []

    async def _send_data(_local_participant, payload):
        sent.append(payload)

    monkeypatch.setattr(v, "_send_data", _send_data)

    vt = types.ModuleType("routers.voice_tts")

    async def _transcribe_audio(_path):
        return "hello zoe"

    async def _synth(payload, caller=None):
        synth_calls.append({"payload": payload, "caller": caller})
        return _FakeTTSResponse()

    vt._transcribe_audio = _transcribe_audio
    vt.synthesize = _synth
    vt._split_sentences = _split_like_prod
    monkeypatch.setitem(sys.modules, "routers.voice_tts", vt)

    bd = types.ModuleType("brain_dispatch")

    async def _brain_oneshot(*_a, **_k):
        return reply

    bd.brain_oneshot = _brain_oneshot
    monkeypatch.setitem(sys.modules, "brain_dispatch", bd)

    vp = types.ModuleType("voice_presence")
    vp.processing_ack_event = lambda: None
    monkeypatch.setitem(sys.modules, "voice_presence", vp)

    async def _no_fast_tier(*_a, **_k):
        return None

    monkeypatch.setattr(v, "_maybe_fast_tier", _no_fast_tier)
    return sent, synth_calls


def _audio_msgs(sent):
    return [m for m in sent if m.get("type") == "audio"]


def _frames():
    return [b"\x00\x00" * 320]


def test_flag_off_single_message_no_seq(monkeypatch):
    """Default (flag unset): exactly one audio message, byte-shape unchanged."""
    monkeypatch.delenv("ZOE_LIVEKIT_STREAM_TTS", raising=False)
    sent, synth_calls = _install_fakes(monkeypatch, reply=_THREE_SENTENCES)
    _run(v._run_pipeline(object(), _frames(), "jason", "sess"))
    audio = _audio_msgs(sent)
    assert len(audio) == 1
    assert set(audio[0]) == {"type", "audio_base64", "content_type"}  # no seq/final
    assert len(synth_calls) == 1
    assert synth_calls[0]["payload"] == {"text": _THREE_SENTENCES}


def test_flag_on_streams_ordered_sentences(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_STREAM_TTS", "1")
    sent, synth_calls = _install_fakes(monkeypatch, reply=_THREE_SENTENCES)
    _run(v._run_pipeline(object(), _frames(), "jason", "sess"))
    audio = _audio_msgs(sent)
    assert len(audio) == 3
    assert [m["seq"] for m in audio] == [0, 1, 2]
    assert [m["final"] for m in audio] == [False, False, True]
    # Same payload shape as today, plus the two new keys.
    for m in audio:
        assert m["audio_base64"] and m["content_type"] == "audio/wav"
    assert [c["payload"]["text"] for c in synth_calls] == [
        "First thing", "Second thing", "Third thing."
    ]  # exact strings come from the (faked) splitter; ordering is what's locked


def test_flag_on_text_pipeline_streams_too(monkeypatch):
    """The second synth site (_run_text_pipeline) streams as well."""
    monkeypatch.setenv("ZOE_LIVEKIT_STREAM_TTS", "1")
    sent, _ = _install_fakes(monkeypatch, reply="One. Two.")
    _run(v._run_text_pipeline(object(), "do a thing", "jason", "sess"))
    audio = _audio_msgs(sent)
    assert [m["seq"] for m in audio] == [0, 1]
    assert [m["final"] for m in audio] == [False, True]


def test_cancel_between_sentences_stops_stream(monkeypatch):
    """Barge-in cancels the pipeline task: the cancel checkpoint between
    sentences must stop synthesis — no further audio messages go out."""
    monkeypatch.setenv("ZOE_LIVEKIT_STREAM_TTS", "1")
    sent, synth_calls = _install_fakes(monkeypatch, reply=_THREE_SENTENCES)

    async def _drive():
        task = asyncio.ensure_future(
            v._stream_sentence_audio(object(), _THREE_SENTENCES, "jason")
        )
        # Let sentence 0 synthesize + send, then barge in.
        while not _audio_msgs(sent):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(_drive())
    assert len(_audio_msgs(sent)) == 1          # stream stopped mid-reply
    assert len(synth_calls) <= 2                # and no further synthesis ran on


def test_flag_off_default_is_off():
    assert v._livekit_stream_tts_enabled() is False
