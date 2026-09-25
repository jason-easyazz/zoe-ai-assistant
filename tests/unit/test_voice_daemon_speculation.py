"""Daemon side of B1.1 speculative turn-start (ZOE_SPECULATIVE_TURN, default OFF).

Torch-free like test_voice_endpointer_tail.py: `_vad_prob` is scripted and the
real `_Endpointer` / `record_command` / `_do_single_turn_stream` run against
fakes. Pins:

  1. flag OFF: `speculative_ready` never fires, the endpointer's stop decisions
     are unchanged, and the turn payload never carries `speculative`/`turn_id`;
  2. flag ON: the first verdict fires exactly once after
     ZOE_SPECULATIVE_TAIL_MS of deep quiet, `record_command` hands the audio-so-far
     to the speculation and keeps recording to the real endpoint;
  3. speech after the first verdict is reported as `resumed` (→ resolve, never a
     blind commit);
  4. a `cancelled` done frame on the speculative stream plays nothing, beeps
     nothing, never re-POSTs, and marks the speculation cancelled;
  5. a committed speculative stream plays normally.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import types
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"

SPEECH = 0.9
DEEP = 0.02


def _load(name: str):
    stubs = {}
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    stubs["pyaudio"] = fake_pyaudio
    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(
        SSLError=type("SSLError", (Exception,), {}),
        HTTPError=type("HTTPError", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}),
    )
    stubs["requests"] = fake_requests
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is not None:
                sys.modules[n] = prev
            else:
                sys.modules.pop(n, None)


@pytest.fixture(scope="module")
def daemon():
    return _load("zoe_voice_daemon_spec_test")


def _endpointer(daemon, monkeypatch, probs, *, flag_on, spec_tail_ms=320, vad_tail_ms=0):
    seq = iter(probs)
    monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: next(seq))
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", vad_tail_ms)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TURN", flag_on)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TAIL_MS", spec_tail_ms)
    return daemon._Endpointer()


def _chunks(daemon, spec_ms):
    return max(1, -(-spec_ms * daemon.SAMPLE_RATE // (1000 * daemon.CHUNK_SIZE)))


def _drive(ep, daemon, probs):
    """Feed chunks past the min-recording guard; return (fire_index, stop_index, resumed)."""
    chunk = b"\x00\x00" * daemon.CHUNK_SIZE
    fire = stop = None
    n = ep._min_frames + 1  # start past the guard so tails are the only variable
    for i, _ in enumerate(probs):
        n += 1
        if ep.push(chunk, n):
            stop = i
            break
        if fire is None and ep.speculative_ready(n):
            fire = i
    return fire, stop, ep.resumed_after_speculation


def test_flag_off_never_fires_and_stop_decision_is_unchanged(daemon, monkeypatch):
    probs = [SPEECH] * 3 + [DEEP] * 40
    ep_off = _endpointer(daemon, monkeypatch, probs, flag_on=False)
    fire, stop, resumed = _drive(ep_off, daemon, probs)
    assert fire is None and not resumed
    assert ep_off._spec_max_silent is None
    # Same script, flag ON: the stop index must not move — speculation only ADDS a hook.
    ep_on = _endpointer(daemon, monkeypatch, probs, flag_on=True)
    fire_on, stop_on, _ = _drive(ep_on, daemon, probs)
    assert stop_on == stop
    assert fire_on is not None and fire_on < stop_on


def test_flag_on_fires_once_after_spec_tail_of_deep_quiet(daemon, monkeypatch):
    probs = [SPEECH] * 3 + [DEEP] * 40
    ep = _endpointer(daemon, monkeypatch, probs, flag_on=True, spec_tail_ms=320)
    fire, stop, resumed = _drive(ep, daemon, probs)
    assert fire == 3 + _chunks(daemon, 320) - 1
    assert stop is not None and stop > fire
    assert not resumed
    assert ep.speculation_fired
    # Latched: it does not fire again even though deep quiet continues.
    assert ep.speculative_ready(10_000) is False


def test_speech_after_first_verdict_is_reported_as_resumed(daemon, monkeypatch):
    k = _chunks(daemon, 320)
    probs = [SPEECH] * 3 + [DEEP] * k + [SPEECH] * 2 + [DEEP] * 40
    ep = _endpointer(daemon, monkeypatch, probs, flag_on=True)
    fire, stop, resumed = _drive(ep, daemon, probs)
    assert fire == 3 + k - 1
    assert resumed, "speech after the first verdict must force a resolve"
    assert stop is not None and stop > 3 + k + 2
    assert ep.speculative_ready(10_000) is False  # one speculation per recording


def test_amplitude_mode_never_fires(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", False)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TURN", True)
    ep = daemon._Endpointer()
    assert ep.mode == "amplitude" and ep._spec_max_silent is None
    assert ep.speculative_ready(10_000) is False


# ── record_command hook ──────────────────────────────────────────────────

class _FakeStream:
    def __init__(self, n):
        self.n = n
        self.closed = False

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        return b"\x01\x00" * size

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class _FakePA:
    def get_sample_size(self, fmt):  # noqa: ARG002
        return 2


class _Spec:
    def __init__(self):
        self.fired_wav = None
        self.resumed = None

    def fire(self, wav):
        assert self.fired_wav is None, "must fire once"
        self.fired_wav = wav

    def recording_closed(self, *, resumed):
        self.resumed = resumed


def test_record_command_fires_prefix_then_keeps_recording(daemon, monkeypatch):
    k = _chunks(daemon, 320)
    probs = [SPEECH] * 30 + [DEEP] * k + [DEEP] * 60
    _endpointer(daemon, monkeypatch, probs, flag_on=True)  # installs the scripted VAD + flags
    monkeypatch.setattr(daemon, "_PREROLL", [])
    spec = _Spec()
    wav = daemon.record_command(_FakePA(), stream=_FakeStream(len(probs)), speculation=spec)
    assert wav is not None and spec.fired_wav is not None

    def _n_frames(w):
        with wave.open(io.BytesIO(w)) as wf:
            return wf.getnframes()
    assert _n_frames(spec.fired_wav) < _n_frames(wav), "the speculative wav is a strict prefix"
    assert _n_frames(spec.fired_wav) == (30 + k) * daemon.CHUNK_SIZE
    assert spec.resumed is False


def test_record_command_without_speculation_is_unchanged(daemon, monkeypatch):
    probs = [SPEECH] * 30 + [DEEP] * 60
    _endpointer(daemon, monkeypatch, probs, flag_on=True)
    monkeypatch.setattr(daemon, "_PREROLL", [])
    wav = daemon.record_command(_FakePA(), stream=_FakeStream(len(probs)))
    assert wav is not None and wav[:4] == b"RIFF"


# ── _do_single_turn_stream: speculative protocol on the wire ─────────────

class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        return iter(self._lines)


def _wire(daemon, monkeypatch, lines):
    posted: list[dict] = []
    played: list[bytes] = []

    def _post(url, json=None, headers=None, timeout=None, stream=False, verify=True):  # noqa: A002, ARG001
        posted.append(json)
        return _FakeResp(lines)
    monkeypatch.setattr(daemon.requests, "post", _post, raising=False)
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn", lambda _w: None)
    monkeypatch.setattr(daemon, "_feed_pcm_chunk", lambda aplay, b: played.append(b) or None)
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: pytest.fail("beep on a speculative stream"))
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: pytest.fail("blocking re-POST of a speculative prefix"))
    return posted, played


def test_cancelled_speculative_stream_plays_nothing_and_marks_cancelled(daemon, monkeypatch):
    lines = [
        json.dumps({"transcript": "add milk"}).encode(),
        json.dumps({"done": True, "cancelled": True, "reply": "", "reason": "cancel"}).encode(),
    ]
    posted, played = _wire(daemon, monkeypatch, lines)
    spec = daemon._SpeculativeTurn(_FakePA())
    ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, speculation=spec)
    assert ok is False and spec.cancelled is True
    assert played == []
    assert posted[0]["speculative"] is True and posted[0]["turn_id"] == spec.turn_id


def test_committed_speculative_stream_plays(daemon, monkeypatch):
    lines = [
        json.dumps({"transcript": "what time is it"}).encode(),
        json.dumps({"chunk": 0, "text": "Noon."}).encode(),
        base64.b64encode(b"RIFFnoon"),
        json.dumps({"done": True, "reply": "Noon."}).encode(),
    ]
    _, played = _wire(daemon, monkeypatch, lines)
    monkeypatch.setattr(daemon, "_is_junk_transcript", lambda _t: False)
    spec = daemon._SpeculativeTurn(_FakePA())
    ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, speculation=spec)
    assert ok is True and spec.cancelled is False
    assert played == [b"RIFFnoon"]


def test_non_speculative_payload_carries_no_speculation_fields(daemon, monkeypatch):
    lines = [json.dumps({"transcript": "", "done": True, "reply": ""}).encode()]
    posted, _ = _wire(daemon, monkeypatch, lines)
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False)
    assert "speculative" not in posted[0] and "turn_id" not in posted[0]


def test_handed_over_claim_is_not_rescored(daemon, monkeypatch):
    lines = [json.dumps({"transcript": "", "done": True, "reply": ""}).encode()]
    posted, _ = _wire(daemon, monkeypatch, lines)
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn",
                        lambda _w: pytest.fail("must not re-score a handed-over claim"))
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, voice_claim=("jason", 0.81))
    assert posted[0]["voice_user_id"] == "jason" and posted[0]["voice_score"] == 0.81


def test_speculative_turn_finish_sends_resolve_with_final_audio(daemon, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(daemon, "_api_post", lambda path, body, **kw: calls.append((path, body)) or {"ok": True, "verdict": "cancel"})
    spec = daemon._SpeculativeTurn(_FakePA())
    spec.fired = True
    spec.recording_closed(resumed=True)
    played = spec.finish(b"RIFFfinal")
    assert played is False  # nothing was played on a never-started stream
    path, body = calls[0]
    assert path == "/api/voice/turn_stream/speculation"
    assert body["action"] == "resolve" and base64.b64decode(body["audio_base64"]) == b"RIFFfinal"

    calls.clear()
    spec2 = daemon._SpeculativeTurn(_FakePA())
    spec2.fired = True
    spec2.recording_closed(resumed=False)
    spec2.finish(b"RIFFfinal")
    assert calls[0][1] == {"turn_id": spec2.turn_id, "action": "commit"}
