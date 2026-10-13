"""The pre-brain utterance gate: hold a clause cut mid-sentence, drop side speech.

Panel evidence (2026-10-12): an endpoint at a 1.5 s pause cut a sentence at "...and"
and Zoe answered half a thought; the follow-up and conversation windows (no wake word)
took the owner talking to someone else as commands.

The daemon has no transcript at endpoint time, so the SERVER decides after STT, before
the brain: ``voice_utterance_shape.gate_frame`` answers with an ``incomplete`` done frame
(the daemon listens a short window and re-sends ONE merged clip) or a ``skipped`` frame
(side speech: silence, no brain). Pins, with the failure each one stops:

  * ``ends_incomplete`` holds on a trailing "and/but/because/or/..." (en + es), never on
    a question, a 1-2 word answer, or a weak word ("that") in a short/question utterance;
  * ``addressedness`` acts on positive evidence only by default (a vocative naming
    someone else, no question/command shape, no "Zoe"), is stricter only in ``strict``;
  * ``gate_frame``: ``continued`` is never held twice, a daemon that did not ask
    (``can_continue`` absent) is never held, speculative turns are never gated, the
    addressedness flag defaults OFF, and a typo'd flag value is OFF, not "on";
  * the /turn_stream route returns the gate frame INSTEAD of the brain and without the
    ``voice:transcript`` broadcast, and a gate crash fails open.

All phrases are invented. Brain, STT, TTS and the broadcaster are mocked.
"""
import base64
import json

import pytest

pytestmark = pytest.mark.ci_safe  # pure functions + TestClient with mocks; no models/DB/network

import voice_utterance_shape as us


# ── ends_incomplete ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I was thinking we could walk down to the market and",
    "I wanted to say thank you for the lift but",
    "We should leave early because",
    "Do you want tea or",
    "Remind me to call the plumber, and",       # trailing comma stripped
    "I will bring the ladder if",
    "Put the kettle on and um",                  # trailing filler = mid-thought
    "Fui a la tienda esta mañana y",             # es
    "Quería ir al cine pero",                    # es
    "Me gusta el té o",                          # es
    "I really enjoyed the way that",             # weak word, long statement
])
def test_trailing_continuation_is_incomplete(text):
    assert us.ends_incomplete(text) is True, text


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "Put the kettle on.",
    "Is it going to rain and?",                  # a question mark is complete
    "Yes and",                                   # two words: an answer, not a cut thought
    "So",
    "What was that",                             # weak word, question
    "Who said that",
    "I like it though",                          # not a continuation word
    "I think so",                                # "so" is deliberately NOT a continuer
    "Play the next song",
    "Can you tell me which",                     # weak word, only 5 words
])
def test_complete_or_ambiguous_is_not_held(text):
    assert us.ends_incomplete(text) is False, text


@pytest.mark.parametrize("text", [
    "Tell me the cause",                      # noun after a determiner
    "I don't know the cause",
    "Please find out what the root cause",
    "They want to know the real cause.",
    "That is not something they cause",      # verb
    "Nobody knows what might cause",
])
def test_cause_as_a_noun_or_verb_is_not_held(text):
    assert us.ends_incomplete(text) is False, text


@pytest.mark.parametrize("text", [
    "I will leave early cause",              # conjunction, bare
    "I will leave early 'cause",             # clipped conjunction
    "I will leave early ’cause,",            # curly apostrophe + trailing comma
    "We should stay home because",           # the full word is unaffected
    "Tell me the cause and",                 # a different continuer after the noun still holds
])
def test_cause_as_a_conjunction_is_still_held(text):
    assert us.ends_incomplete(text) is True, text


def test_ending_punctuation_does_not_hide_a_continuation_word():
    assert us.ends_incomplete("We could have pasta or...") is True
    assert us.ends_incomplete("We could have pasta, or,") is True
    assert us.ends_incomplete("We could have pasta or?") is False


# ── addressedness ────────────────────────────────────────────────────────────

SIDE_SPEECH = (
    "Sounds like you're making a lot of noise over there, Casey. I wonder why that is.",
    "I think you already know, Morgan. We have talked about it before.",
)


@pytest.mark.parametrize("text", SIDE_SPEECH)
@pytest.mark.parametrize("window", ["followup", "conversation"])
def test_vocative_side_speech_is_not_addressed(text, window):
    v = us.addressedness(text, window)
    assert (v.addressed, v.reason) == (False, "vocative")


def test_a_command_to_someone_else_by_name_is_still_a_command():
    # Conservative: command shape wins over a vocative.
    assert us.addressedness("Turn the porch light off, Casey.", "followup").addressed is True


def test_naming_zoe_is_always_addressed():
    assert us.addressedness("Thanks for that, Zoe. Casey, come here.", "followup").addressed is True
    assert us.addressedness("Sounds good Zoey, Casey.", "conversation").addressed is True


def test_a_wake_turn_or_a_barge_seed_is_never_judged():
    for text in SIDE_SPEECH:
        assert us.addressedness(text, None) == us.Verdict(True, "wake")
        assert us.addressedness(text, "") == us.Verdict(True, "wake")
        assert us.addressedness(text, "barge").addressed is True


def test_default_lets_a_plain_statement_through():
    # No vocative = no positive evidence it was meant for someone else.
    for window in ("followup", "conversation"):
        assert us.addressedness("It has been a long and tiring week for everyone.", window).addressed is True


def test_strict_followup_wants_a_shape_but_conversation_does_not():
    statement = "It has been a long and tiring week for everyone."
    assert us.addressedness(statement, "followup", strict=True) == us.Verdict(False, "no_shape")
    assert us.addressedness(statement, "conversation", strict=True).addressed is True


@pytest.mark.parametrize("text,reason", [
    ("What time does the pharmacy shut", "question"),
    ("Is it going to rain tomorrow", "question"),
    ("Pause the music for a minute", "command"),
    ("Add bread to the list", "command"),
    ("Thanks that was helpful for the evening", "acknowledgement"),
    ("Cuánto falta para llegar a casa", "question"),
    ("Enciende la luz de la cocina por favor", "command"),
    ("Anything else I should know about it?", "question"),
])
def test_strict_accepts_question_command_acknowledgement_shapes(text, reason):
    assert us.addressedness(text, "followup", strict=True) == us.Verdict(True, reason)


def test_strict_accepts_an_answer_to_a_question_and_short_replies():
    assert us.addressedness("Probably around half past six I think", "followup",
                            strict=True, prev_reply_question=True) == us.Verdict(True, "answer")
    assert us.addressedness("The red one", "followup", strict=True) == us.Verdict(True, "short")


def test_a_place_name_after_a_comma_does_not_beat_a_question():
    # ", Portugal?" has the vocative shape; the question shape must win.
    assert us.addressedness("What is the weather like in Lisbon, Portugal?", "followup").addressed is True


def test_weekdays_and_thanks_after_a_comma_are_not_names():
    assert us.addressedness("I will see you there, Friday.", "followup").addressed is True
    assert us.addressedness("That works for me, Thanks.", "followup").addressed is True


# ── the flags ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    monkeypatch.delenv(us.FOLLOWUP_FLAG, raising=False)
    monkeypatch.delenv(us.CONTINUATION_FLAG, raising=False)


def test_followup_flag_defaults_off_and_typos_are_off(monkeypatch):
    assert us.followup_addressed_mode() == "off"
    for raw, want in (("on", "on"), ("1", "on"), ("TRUE", "on"), ("strict", "strict"),
                      ("off", "off"), ("", "off"), ("onn", "off"), ("strictt", "off")):
        monkeypatch.setenv(us.FOLLOWUP_FLAG, raw)
        assert us.followup_addressed_mode() == want, raw


# ── gate_frame ───────────────────────────────────────────────────────────────

def test_nothing_is_gated_with_the_flags_at_their_defaults():
    # Side speech passes (flag off) and no hold is asked for (daemon did not send can_continue).
    assert us.gate_frame({"window": "followup"}, SIDE_SPEECH[0]) is None
    assert us.gate_frame({}, "We could walk to the market and") is None
    assert us.gate_frame(None, "We could walk to the market and") is None


def test_incomplete_frame_when_the_daemon_asked_and_the_clause_is_open():
    frame = us.gate_frame({"can_continue": True}, "We could walk down to the market and")
    assert frame == {"done": True, "reply": "", "incomplete": True}


def test_a_continued_retry_is_never_held_twice():
    assert us.gate_frame({"can_continue": True, "continued": True},
                         "We could walk down to the market and") is None


def test_a_complete_clause_is_not_held():
    assert us.gate_frame({"can_continue": True}, "Turn the porch light on.") is None


@pytest.mark.parametrize("raw", ["off", "0", "false", "NO"])
def test_continuation_kill_switch(monkeypatch, raw):
    monkeypatch.setenv(us.CONTINUATION_FLAG, raw)
    assert us.continuation_hold_enabled() is False
    assert us.gate_frame({"can_continue": True}, "We could walk down to the market and") is None


@pytest.mark.parametrize("raw", ["", "on", "1", "yes"])
def test_continuation_hold_is_on_unless_explicitly_disabled(monkeypatch, raw):
    monkeypatch.setenv(us.CONTINUATION_FLAG, raw)
    assert us.continuation_hold_enabled() is True


def test_speculative_and_empty_turns_are_never_gated(monkeypatch):
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "on")
    assert us.gate_frame({"can_continue": True}, "We could walk down to the market and",
                         speculative=True) is None
    assert us.gate_frame({"can_continue": True, "window": "followup"}, "") is None


def test_skipped_frame_when_flag_on_and_side_speech(monkeypatch):
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "on")
    frame = us.gate_frame({"window": "followup"}, SIDE_SPEECH[1])
    assert frame == {"done": True, "reply": "", "skipped": "not_addressed", "reason": "vocative"}


def test_skipped_wins_over_incomplete(monkeypatch):
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "on")
    frame = us.gate_frame({"window": "followup", "can_continue": True},
                          "I think you already know, Morgan. We could walk to the market and")
    assert frame and frame.get("skipped") == "not_addressed" and "incomplete" not in frame


def test_a_wake_turn_is_not_skipped_even_with_the_flag_on(monkeypatch):
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "on")
    assert us.gate_frame({}, SIDE_SPEECH[1]) is None


def test_strict_mode_reaches_the_rule(monkeypatch):
    statement = "It has been a long and tiring week for everyone."
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "on")
    assert us.gate_frame({"window": "followup"}, statement) is None
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "strict")
    assert us.gate_frame({"window": "followup"}, statement)["reason"] == "no_shape"
    assert us.gate_frame({"window": "followup", "prev_reply_question": True}, statement) is None


# ── the route ────────────────────────────────────────────────────────────────

from fastapi import FastAPI
from fastapi.testclient import TestClient

import push
import routers.voice_tts as vt

PANEL = "test-panel"


def _app(monkeypatch, transcript, brain_calls):
    monkeypatch.delenv("ZOE_SPECULATIVE_TURN", raising=False)
    monkeypatch.delenv("ZOE_VOICE_FILLER_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_CONVERSATION_OPENER_ENABLED", raising=False)

    async def _fake_stt(_path):
        return transcript
    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)

    async def _fake_tts(_text):
        return b"RIFFfakewav"
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)

    async def _brain(payload, caller=None, stream=True, db=None):
        brain_calls.append(payload)
        return {"reply": "Brain answer.", "audio_base64": base64.b64encode(b"RIFFreal").decode()}
    monkeypatch.setattr(vt, "voice_command", _brain)

    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {
        "source": "device", "panel_id": PANEL, "user_id": "voice-daemon",
    }
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None
    return app


@pytest.fixture
def events(monkeypatch):
    sent = []

    async def _broadcast(channel, event_type, data, *a, **kw):
        sent.append((event_type, dict(data)))
        return 1
    monkeypatch.setattr(push.broadcaster, "broadcast", _broadcast)
    return sent


def _turn(app, **extra):
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": PANEL, **extra}
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200
        return [json.loads(line) for line in r.iter_lines() if line]


def test_route_holds_an_open_clause_without_running_the_brain(monkeypatch, events):
    calls = []
    frames = _turn(_app(monkeypatch, "We could walk down to the market and", calls), can_continue=True)
    assert frames == [{"done": True, "reply": "", "incomplete": True}]
    assert calls == [], "a held turn must never reach the brain"
    assert not any(t == "voice:transcript" for t, _ in events), "a held turn leaves no UI trace"


def test_route_runs_the_retry_normally(monkeypatch, events):
    calls = []
    transcript = "We could walk down to the market and"   # still open - but this IS the retry
    frames = _turn(_app(monkeypatch, transcript, calls), can_continue=True, continued=True)
    assert any(f.get("done") and f.get("reply") for f in frames), frames
    assert len(calls) == 1 and calls[0]["text"] == transcript


def test_route_without_can_continue_is_untouched(monkeypatch, events):
    calls = []
    _turn(_app(monkeypatch, "We could walk down to the market and", calls))
    assert len(calls) == 1


def test_route_skips_side_speech_only_when_the_flag_is_on(monkeypatch, events):
    calls = []
    app = _app(monkeypatch, SIDE_SPEECH[0], calls)
    _turn(app, window="followup")
    assert len(calls) == 1, "flag off: unchanged"
    monkeypatch.setenv(us.FOLLOWUP_FLAG, "on")
    frames = _turn(app, window="followup")
    assert frames == [{"done": True, "reply": "", "skipped": "not_addressed", "reason": "vocative"}]
    assert len(calls) == 1, "flag on: the brain was not called again"


def test_route_fails_open_when_the_gate_crashes(monkeypatch, events):
    calls = []
    app = _app(monkeypatch, "Turn the porch light on", calls)

    def _boom(*a, **k):
        raise RuntimeError("gate bug")
    monkeypatch.setattr(us, "gate_frame", _boom)
    frames = _turn(app, can_continue=True, window="followup")
    assert any(f.get("done") for f in frames) and len(calls) == 1
