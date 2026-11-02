"""The panel daemon's junk-transcript filter must never eat an ANSWER, and must drop wake-time junk BEFORE playback.

2026-11-03: Zoe asked a question, the owner answered "Yes.", the server answered it and the reply PLAYED - then the daemon's
post-hoc ``_is_junk_transcript`` ("yes" is in VOICE_IGNORE_TRANSCRIPTS) returned False and the follow-up chain died. Two
faults, both pinned here:

  1. the short-answer list (yes/no/okay/thanks...) is wake-time noise only: in a follow-up / conversation window, or right
     after Zoe asked a question, a short answer is the reply she asked for;
  2. the check ran AFTER the reply was synthesised and played, so it suppressed nothing but the follow-up. The server leads
     the stream with the transcript, so wake-time junk is now dropped there, before any audio (closing the stream).

Torch-free, like test_voice_daemon_speculation.py.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


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
    return _load("zoe_voice_daemon_junk_answers_test")


@pytest.fixture(autouse=True)
def _fresh(daemon, monkeypatch):
    # The DEFAULT list (an env-set list on the panel is a superset of it) and no question pending.
    monkeypatch.setattr(daemon, "VOICE_IGNORE_TRANSCRIPTS",
                        frozenset("yes,yeah,yep,yup,no,ok,okay,thank you,thanks,hi,hello,hmm,um".split(",")))
    monkeypatch.setattr(daemon, "_prev_reply_asked", False)


# ── the filter itself ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["Yes.", "yes", "No.", "yeah", "okay", "Thanks!", "thank you"])
def test_a_short_answer_is_never_junk_while_answering(daemon, text):
    assert daemon._is_junk_transcript(text, answering=True) is False


@pytest.mark.parametrize("text", ["Yes.", "okay", "thank you", "hmm"])
def test_the_same_words_are_still_junk_at_wake_time(daemon, text):
    assert daemon._is_junk_transcript(text) is True


@pytest.mark.parametrize("text", ["Hmm.", "um", "", "a", "you you you you"])
def test_non_words_and_structural_noise_stay_junk_while_answering(daemon, text):
    assert daemon._is_junk_transcript(text, answering=True) is True


def test_answering_window_covers_windows_and_a_pending_question(daemon, monkeypatch):
    assert daemon._answering_window("followup") and daemon._answering_window("conversation")
    assert not daemon._answering_window("")
    monkeypatch.setattr(daemon, "_prev_reply_asked", True)   # "Hey Zoe, yes" after the window closed
    assert daemon._answering_window("")


# ── the streamed turn ──────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, lines):
        self._lines = lines
        self.consumed = 0
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        for line in self._lines:
            self.consumed += 1
            yield line

    def close(self):
        self.closed = True


def _reply_lines(heard: str, reply: str = "Done, I've set it."):
    return [
        json.dumps({"transcript": heard}).encode(),
        json.dumps({"chunk": 0, "text": reply}).encode(),
        base64.b64encode(b"RIFFreply"),
        json.dumps({"done": True, "reply": reply}).encode(),
    ]


def _wire(daemon, monkeypatch, lines):
    resp = _FakeResp(lines)
    played: list[bytes] = []
    monkeypatch.setattr(daemon.requests, "post", lambda *a, **k: resp, raising=False)
    monkeypatch.setattr(daemon, "_HTTP_SESSION", None)
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn", lambda _w: None)
    monkeypatch.setattr(daemon, "_feed_pcm_chunk", lambda aplay, b: played.append(b) or None)
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: None)
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: pytest.fail("no blocking re-POST"))
    return resp, played


@pytest.mark.parametrize("window", ["followup", "conversation"])
def test_yes_in_a_follow_up_window_keeps_the_chain_alive(daemon, monkeypatch, window):
    """The 2026-11-03 failure: the answer was played, then the chain ended (returned False)."""
    resp, played = _wire(daemon, monkeypatch, _reply_lines("Yes."))
    ok = daemon._do_single_turn_stream(None, b"RIFFwav", prompt_on_empty=False, window=window)
    assert played == [b"RIFFreply"]
    assert ok is True, "a played answer to Zoe's question must keep the follow-up chain going"


def test_hey_zoe_yes_after_a_question_is_an_answer(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_prev_reply_asked", True)
    _, played = _wire(daemon, monkeypatch, _reply_lines("Yes."))
    assert daemon._do_single_turn_stream(None, b"RIFFwav", prompt_on_empty=False) is True
    assert played == [b"RIFFreply"]


def test_wake_time_junk_is_dropped_before_any_audio_plays(daemon, monkeypatch):
    """Order: junk must be dropped BEFORE the reply plays, not after (the stream is closed after the transcript frame)."""
    resp, played = _wire(daemon, monkeypatch, _reply_lines("Thank you."))
    ok = daemon._do_single_turn_stream(None, b"RIFFwav", prompt_on_empty=False)
    assert ok is False
    assert played == [], "a wake-time hallucination must not be answered aloud"
    assert resp.consumed == 1 and resp.closed, "the daemon stops at the transcript frame and closes the stream"


def test_a_real_wake_command_is_untouched(daemon, monkeypatch):
    _, played = _wire(daemon, monkeypatch, _reply_lines("what time is it", "Noon."))
    assert daemon._do_single_turn_stream(None, b"RIFFwav", prompt_on_empty=False) is True
    assert played == [b"RIFFreply"]


# ── the blocking turn (the stream's fallback) ──────────────────────────────────

def _blocking(daemon, monkeypatch, text, window=""):
    played: list[str] = []
    monkeypatch.setattr(daemon, "_post_turn_riding_restart",
                        lambda payload, on_restart=None, path="": {"ok": True, "text": text, "audio_base64": "UklGRg=="})
    monkeypatch.setattr(daemon, "play_audio_b64", lambda b, ct="audio/wav": played.append(b))
    ok = daemon._do_single_turn(None, b"RIFFwav", prompt_on_empty=False, voice_claim=None, window=window)
    return ok, played


def test_blocking_turn_plays_a_yes_in_a_follow_up_window(daemon, monkeypatch):
    ok, played = _blocking(daemon, monkeypatch, "Yes.", window="followup")
    assert ok is True and played == ["UklGRg=="]


def test_blocking_turn_drops_wake_time_junk_before_playing(daemon, monkeypatch):
    ok, played = _blocking(daemon, monkeypatch, "Yes.")
    assert ok is False and played == []
