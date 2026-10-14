"""Mishear clarification: ask "Sorry, what was that?" instead of answering a short line the STT probably got wrong (ZOE_MISHEAR_CLARIFY).

Owner complaint 2026-10-14: a short spoken device command was heard as a short feeling-shaped line (STT slow for its clip), routed to chat, and drew a
counsellor-style reply. Pins, each with the failure it stops (the NEGATIVE CONTROL of each is the paired case that must go the other way):

  * a short, slow-STT, feeling-shaped, chat-routed line with no explicit cue is clarified (control: the same line with normal STT speed is answered);
  * a clear disclosure is NEVER clarified - affect words, the restraint classes, the distress hand-off, this module's extra cues (control: the same
    unreliable evidence with the cue removed clarifies - so the cue, not the evidence, is what saved it);
  * a device command is never clarified - a keyword intent, a Skybridge card, a tool-routed line, an unavailable router (control: the chat-routed
    feeling-shaped line clarifies);
  * never twice in a row: the turn after a clarification is answered whatever it is, the one after that is judged afresh (control: without the
    first clarification the same turn clarifies);
  * flag off (default, or a typo) is unchanged: nothing runs, the brain is called with exactly the old payload (control: flag ``on`` clarifies);
  * the route: a clarification is spoken with NO brain call, NO transcript broadcast, and keeps the follow-up window open (the reply is a question);
  * the STT evidence: the load guard (every turn slow => nobody clarified), the WAV header reader, the pass-through of the router verdict.

All utterances are invented. Brain, STT, TTS and the broadcaster are mocked; the router is a stub.
"""
import base64
import io
import json
import wave

import pytest

pytestmark = pytest.mark.ci_safe  # pure functions + TestClient with mocks; no models/DB/network

import mishear_clarify as mc

CHAT = lambda _t: {"routed": "chat", "domain": "chat", "score": 0.1}     # noqa: E731 - the router said chat
LIGHTS = lambda _t: {"routed": "smart_home", "domain": "smart_home", "score": 0.9}  # noqa: E731

# the owner's shape: ~1.6 s clip, STT took 1.47 s (ratio 0.92) after a run of ordinary-speed turns
SLOW = dict(clip_s=1.6, stt_s=1.47)
FAST = dict(clip_s=1.6, stt_s=0.35)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv(mc.FLAG, "on")
    mc.reset_state()
    yield
    mc.reset_state()


def _warm(n=4, panel="warm"):
    """A few ordinary-speed turns so the recent-median baseline exists (the load guard needs history)."""
    for _ in range(n):
        mc.evaluate("What time is it today", panel_id=panel, clip_s=2.0, stt_s=0.4, route_fn=CHAT)


def _ev(text, panel="p1", route=CHAT, **kw):
    return mc.evaluate(text, panel_id=panel, route_fn=route, **{**SLOW, **kw})


# ── 1. clarify a short, unreliable, feeling-shaped line ───────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I'm feeling a lot.",
    "I'm feeling something.",
    "I am feeling so.",
    "I feel it again.",
])
def test_clarifies_short_unreliable_feeling_shaped_line(text):
    _warm()
    d = _ev(text)
    assert d.action == "clarify" and d.reason == "unreliable" and "slow_stt" in d.signals
    assert d.reply.endswith("?"), "every clarification is a question: that is what opens the follow-up window"
    assert d.router == {"routed": "chat", "domain": "chat", "score": 0.1}, "the router verdict rides along so the turn does not route twice"


def test_control_normal_stt_speed_is_answered():
    _warm()
    d = _ev("I'm feeling a lot.", **FAST)
    assert d.action == "answer" and d.reason == "reliable"


def test_control_long_turn_is_never_second_guessed():
    _warm()
    d = _ev("I'm feeling a lot about everything that happened today.")
    assert d.action == "answer" and d.reason == "not_short"


def test_shadow_mode_logs_but_never_clarifies(caplog, monkeypatch):
    import logging
    monkeypatch.setenv(mc.FLAG, "shadow")
    _warm()
    with caplog.at_level(logging.INFO, logger=mc.logger.name):
        d = _ev("I'm feeling a lot.")
    assert d.action == "answer" and d.shadow is True
    line = [r.getMessage() for r in caplog.records if "MISHEAR_CLARIFY" in r.getMessage()][-1]
    assert "decision=would_clarify" in line and "mode=shadow" in line
    assert "feeling" not in line, "the metric line never carries transcript text"
    assert mc._PENDING == {}, "shadow mode leaves no 'just clarified' memory: the next turn is judged afresh"


def test_a_question_is_not_feeling_shaped():
    _warm()
    assert _ev("Am I feeling a lot?").action == "answer"


def test_a_line_that_does_not_speak_about_the_speaker_is_answered():
    _warm()
    d = _ev("Rain again today.")
    assert d.action == "answer" and d.reason == "not_feeling_shaped"


def test_a_language_with_no_lexicon_is_inert():
    _warm()
    d = _ev("我感觉很多")  # Chinese: no mishear_clarify entry
    assert d.action == "answer" and d.reason == "no_lexicon"


def test_spanish_line_clarifies_in_spanish():
    _warm()
    d = _ev("Me siento mucho.")
    assert d.action == "clarify" and d.lang == "es" and d.reply.startswith(("Perd", "No te he"))


# ── 2. never clarify a clear emotional disclosure ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I'm really stressed about work.",   # restraint affect
    "I'm so worried.",
    "I'm sad.",
    "I feel awful.",                     # this module's own cue
    "I'm exhausted.",
    "I'm not okay.",
    "I can't cope.",
    "I feel so alone.",
    "My head hurts a lot.",              # this module's own pain cue
    "My chest hurts.",
    "Me duele mucho.",                   # es pain cue
    "I miss my late husband.",           # restraint grief
    "Me siento fatal.",                  # es cue
    "Estoy muy triste.",                 # es affect
])
def test_never_clarifies_a_clear_disclosure(text):
    _warm()
    d = _ev(text)
    assert d.action == "answer", (text, d)
    assert d.reason.startswith("disclosure:"), (text, d.reason)


def test_distress_handoff_language_is_never_clarified():
    _warm()
    d = _ev("I want to die.")
    assert d.action == "answer" and d.reason.startswith("disclosure:")


def test_control_remove_the_cue_and_the_same_evidence_clarifies():
    _warm()
    assert _ev("I'm stressed.").action == "answer"
    assert _ev("I'm something.", panel="p2").action == "clarify"


def test_a_failing_tier_is_treated_as_a_disclosure(monkeypatch):
    import distress_handoff

    def _boom(_t):
        raise RuntimeError("tier down")
    monkeypatch.setattr(distress_handoff, "detect", _boom)
    _warm()
    d = _ev("I'm feeling a lot.")
    assert d.action == "answer" and d.reason == "disclosure:distress:error"


# ── 3. never clarify a device command ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Turn on my lights.",
    "Switch off my porch light.",
    "Put my music on.",
    "Dim the lights for me.",
])
def test_never_clarifies_a_device_command(text):
    # every one of these is first-person shaped (my / me), so ONLY the command checks (keyword intent / Skybridge / router) can save it
    _warm()
    d = _ev(text, route=LIGHTS)
    assert d.action == "answer" and d.reason.startswith("command:"), (text, d)


def test_tool_routed_line_is_never_clarified_even_when_feeling_shaped():
    _warm()
    d = _ev("I'm feeling a lot.", route=LIGHTS)
    assert d.action == "answer" and d.reason == "command:router"


def test_router_unavailable_answers():
    _warm()
    d = _ev("I'm feeling a lot.", route=lambda _t: None)
    assert d.action == "answer" and d.reason == "router_unavailable"
    d = _ev("I'm feeling a lot.", panel="p2", route=lambda _t: (_ for _ in ()).throw(RuntimeError("router down")))
    assert d.action == "answer" and d.reason == "router_unavailable"


def test_a_keyword_intent_owns_the_line(monkeypatch):
    import intent_router

    class _I:
        name = "smart_home"
    monkeypatch.setattr(intent_router, "detect_intent", lambda *a, **k: _I())
    _warm()
    d = _ev("I'm feeling a lot.")
    assert d.action == "answer" and d.reason == "command:intent:smart_home"


def test_a_skybridge_card_owns_the_line(monkeypatch):
    import skybridge_service

    monkeypatch.setattr(skybridge_service, "classify_skybridge_intent", lambda *a, **k: object())
    _warm()
    d = _ev("I'm feeling a lot.")
    assert d.action == "answer" and d.reason == "command:skybridge"


def test_a_spoken_mute_is_a_command_not_a_mishearing():
    _warm()
    d = _ev("I don't want to talk about it.")
    assert d.action == "answer"


def test_zoe_just_asked_a_question_so_a_short_reply_is_an_answer():
    _warm()
    d = _ev("I'm feeling a lot.", prev_reply_question=True)
    assert d.action == "answer" and d.reason == "answers_question"


@pytest.mark.parametrize("kw", [{"speculative": True}, {"continued": True}])
def test_speculative_and_merged_turns_are_never_judged(kw):
    _warm()
    assert _ev("I'm feeling a lot.", **kw).action == "answer"


# ── 4. never twice in a row ───────────────────────────────────────────────────────────────────────

def test_the_repeat_is_answered_then_the_next_turn_is_judged_afresh():
    _warm()
    assert _ev("I'm feeling a lot.").action == "clarify"
    second = _ev("I'm feeling a lot.")
    assert second.action == "answer" and second.reason == "repeat", "the repeat is unclear too: answer it, do not clarify twice"
    assert _ev("I'm feeling a lot.").action == "clarify", "the turn after that is judged afresh"


def test_the_repeat_rule_covers_any_turn_not_just_a_short_one():
    _warm()
    assert _ev("I'm feeling a lot.").action == "clarify"
    d = _ev("Turn the kitchen lights on and set them to fifty percent please.", route=LIGHTS)
    assert d.action == "answer" and d.reason == "repeat"


def test_control_without_the_first_clarification_the_same_turn_clarifies():
    _warm()
    assert _ev("I'm feeling a lot.", panel="other").action == "clarify"


def test_a_clarification_is_per_panel():
    _warm()
    assert _ev("I'm feeling a lot.", panel="kitchen").action == "clarify"
    assert _ev("I'm feeling a lot.", panel="study").action == "clarify", "another panel is not a repeat"


def test_a_clarification_expires():
    _warm()
    assert _ev("I'm feeling a lot.", now=1000.0).action == "clarify"
    late = _ev("I'm feeling a lot.", now=1000.0 + mc.REPEAT_TTL_S + 1)
    assert late.action == "clarify", "a turn long after the question is not its repeat"
    mc.reset_state(); _warm()
    assert _ev("I'm feeling a lot.", now=1000.0).action == "clarify"
    assert _ev("I'm feeling a lot.", now=1000.0 + mc.REPEAT_TTL_S - 1).reason == "repeat", "control: inside the window it IS the repeat"


def test_clarification_phrases_vary_and_all_ask():
    seen = set()
    for i in range(8):
        mc.reset_state()
        mc._ROTATE[0] = i
        _warm()
        seen.add(_ev("I'm feeling a lot.").reply)
    assert len(seen) >= 3 and all(p.endswith("?") for p in seen)


# ── 5. flag off => unchanged ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "off", "0", "false", "bogus", "ON-ish", ""])
def test_flag_off_or_a_typo_does_nothing(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(mc.FLAG, raising=False)
    else:
        monkeypatch.setenv(mc.FLAG, value)
    _warm()
    d = _ev("I'm feeling a lot.", route=lambda _t: pytest.fail("flag off must not route"))
    assert d is mc.OFF and d.action == "off"
    assert mc.counts() == {} and not mc._RATIOS and not mc._PENDING, "flag off keeps no state"


def test_control_flag_on_clarifies_the_same_turn():
    _warm()
    assert _ev("I'm feeling a lot.").action == "clarify"


# ── 6. the STT evidence ───────────────────────────────────────────────────────────────────────────

def test_load_guard_a_box_where_every_turn_is_slow_clarifies_nobody():
    for _ in range(5):
        mc.evaluate("What time is it today", panel_id="warm", clip_s=1.6, stt_s=1.4, route_fn=CHAT)  # loaded: ratio 0.88 every time
    assert _ev("I'm feeling a lot.").action == "answer"


def test_signals_are_pure_and_conservative():
    assert mc.stt_signals(4, 1.6, 1.47, 0.2) == ("slow_stt",)
    assert mc.stt_signals(4, 1.6, 1.47, None) == ("slow_stt",), "no history yet: the absolute rule alone"
    assert mc.stt_signals(4, 1.6, 0.9, 0.2) == (), "below the absolute floor"
    assert mc.stt_signals(4, 6.0, 1.5, 0.2) == (), "a long clip taking 1.5 s is ordinary (ratio 0.25)"
    assert mc.stt_signals(4, 3.0, 1.3, 0.2) == (), "ratio 0.43 is neither slow nor sparse"
    assert mc.stt_signals(2, 5.0, 2.1, 0.2) == ("sparse",), "a long clip that yielded almost no words, and somewhat slow"
    assert mc.stt_signals(2, 5.0, 0.8, 0.2) == (), "sparse alone (fast STT) is not evidence"
    assert mc.stt_signals(4, None, 1.47, 0.2) == () and mc.stt_signals(4, 1.6, None, 0.2) == ()


def _wav(seconds, rate=16000, bits=16):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate * bits // 8))
    return buf.getvalue()


def test_wav_seconds_reads_the_header():
    assert mc.wav_seconds(_wav(1.6)) == pytest.approx(1.6, abs=0.001)
    assert mc.wav_seconds(_wav(0.5, rate=48000)) == pytest.approx(0.5, abs=0.001)
    streamed = bytearray(_wav(1.0))
    streamed[40:44] = b"\xff\xff\xff\xff"   # a streamed header: data size unknown
    assert mc.wav_seconds(bytes(streamed)) == pytest.approx(1.0, abs=0.001)
    assert mc.wav_seconds(b"not a wav at all, just bytes" * 5) is None and mc.wav_seconds(b"") is None


def test_the_route_wav_duration_helper_works():
    # voice_tts used `wave` without importing it, so `_wav_duration_seconds` swallowed a NameError and the STT log's
    # audio_duration_seconds was None on every live turn (found 2026-10-14). The guard also falls back to the header reader.
    import tempfile
    import routers.voice_tts as vt_

    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        f.write(_wav(1.6))
        f.flush()
        assert vt_._wav_duration_seconds(f.name) == pytest.approx(1.6, abs=0.001)


# ── 7. the route ──────────────────────────────────────────────────────────────────────────────────

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

import push
import routers.voice_tts as vt

PANEL = "test-panel"
CLIP_S = 0.2     # a 0.2 s clip whose (fake) STT takes 0.15 s: ratio 0.75, the owner's shape scaled down so the test does not sleep 1.5 s


def _app(monkeypatch, transcript, brain_calls, *, tts=b"RIFFfakewav", stt_s=0.15, route=CHAT, routed_texts=None):
    monkeypatch.setattr(mc, "SLOW_STT_MIN_S", 0.1)   # the owner's 1.47 s / 1.6 s shape, scaled to a 0.15 s / 0.2 s fake
    monkeypatch.setattr(mc, "SLOW_STT_RATIO", 0.5)
    monkeypatch.delenv("ZOE_SPECULATIVE_TURN", raising=False)
    monkeypatch.delenv("ZOE_VOICE_FILLER_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_CONVERSATION_OPENER_ENABLED", raising=False)

    async def _fake_stt(_path, **_kw):
        await asyncio.sleep(stt_s)
        return transcript
    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)

    async def _fake_tts(_text):
        return tts
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)

    async def _brain(payload, caller=None, stream=True, db=None):
        brain_calls.append(payload)
        return {"reply": "Brain answer.", "audio_base64": base64.b64encode(b"RIFFreal").decode()}
    monkeypatch.setattr(vt, "voice_command", _brain)

    import semantic_router as sr
    monkeypatch.setattr(sr, "is_enabled", lambda: True)

    def _route(t):
        if routed_texts is not None:
            routed_texts.append(t)
        return route(t)
    monkeypatch.setattr(sr, "route", _route)

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


def _frames(app, **extra):
    payload = {"audio_base64": base64.b64encode(_wav(CLIP_S)).decode(), "panel_id": PANEL, **extra}
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200
        out = []
        for line in r.iter_lines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass   # a base64 audio line
        return out


def _prime():
    """Ordinary-speed history (ratio 0.2) so the load guard's baseline exists."""
    _warm(panel="elsewhere")


def test_route_clarifies_without_the_brain_or_a_transcript_broadcast(monkeypatch, events):
    _prime()
    calls = []
    frames = _frames(_app(monkeypatch, "I'm feeling a lot.", calls))
    done = frames[-1]
    assert done["done"] is True and done["reply"].endswith("?"), "a question: the daemon opens its follow-up window and tags the repeat"
    assert frames[0] == {"transcript": "I'm feeling a lot."} and any("chunk" in f for f in frames), "the clarification is SPOKEN"
    assert calls == [], "the brain must not run"
    assert not any(t == "voice:transcript" for t, _ in events), "the garbled line never reaches the panel UI"
    assert any(t == "voice:responding" and d["text"].endswith("?") for t, d in events), "the kiosk shows the question"
    assert not ({"skipped", "incomplete", "conversation_end"} & set(done)), "nothing closes the follow-up window"


def test_route_the_repeat_goes_to_the_brain_normally(monkeypatch, events):
    _prime()
    calls = []
    app = _app(monkeypatch, "I'm feeling a lot.", calls)
    assert _frames(app)[-1]["reply"].endswith("?") and calls == []
    _frames(app, window="followup", prev_reply_question=True)
    assert len(calls) == 1 and calls[0]["text"] == "I'm feeling a lot."


def test_route_the_router_verdict_is_computed_once_and_handed_on(monkeypatch, events):
    _prime()
    calls, routed = [], []
    # a tool-routed feeling-shaped line: answered, with the verdict the guard computed riding along to the turn
    _frames(_app(monkeypatch, "I'm feeling a lot.", calls, route=LIGHTS, routed_texts=routed))
    assert routed == ["I'm feeling a lot."], "the guard routed it exactly once"
    assert len(calls) == 1
    assert calls[0]["_router_decision"]["routed"] == "smart_home" and calls[0]["_router_text"] == "I'm feeling a lot."


def test_route_a_disclosure_reaches_the_brain(monkeypatch, events):
    _prime()
    calls = []
    _frames(_app(monkeypatch, "I'm really stressed about work.", calls))
    assert len(calls) == 1 and calls[0]["text"] == "I'm really stressed about work."


def test_route_a_device_command_reaches_the_brain(monkeypatch, events):
    _prime()
    calls = []
    _frames(_app(monkeypatch, "Turn on my lights.", calls, route=LIGHTS))
    assert len(calls) == 1


def test_route_control_the_clarified_line_reaches_the_brain_when_stt_was_quick(monkeypatch, events):
    _prime()
    calls = []
    _frames(_app(monkeypatch, "I'm feeling a lot.", calls, stt_s=0.0))
    assert len(calls) == 1, "same line, normal STT speed: answered"


def test_route_flag_off_is_the_old_path_byte_for_byte(monkeypatch, events):
    monkeypatch.delenv(mc.FLAG, raising=False)
    calls = []
    _frames(_app(monkeypatch, "I'm feeling a lot.", calls))
    assert len(calls) == 1
    assert set(calls[0]) == {"text", "panel_id", "_t_turn_start", "_spacer"}, "no new keys reach the turn with the flag off"
    assert any(t == "voice:transcript" for t, _ in events)
    assert mc.counts() == {}


def test_route_silent_tts_falls_back_to_answering(monkeypatch, events):
    _prime()
    calls = []
    _frames(_app(monkeypatch, "I'm feeling a lot.", calls, tts=None))
    assert len(calls) == 1, "no audio => no clarification: a silent turn is worse than an answer"
    # and the failed attempt left no 'just clarified' memory behind
    assert mc.evaluate("I'm feeling a lot.", panel_id=PANEL, route_fn=CHAT, clip_s=CLIP_S, stt_s=0.15).action == "clarify"


def test_route_fails_open_when_the_guard_crashes(monkeypatch, events):
    _prime()
    calls = []
    app = _app(monkeypatch, "I'm feeling a lot.", calls)

    def _boom(*a, **k):
        raise RuntimeError("guard bug")
    monkeypatch.setattr(mc, "evaluate", _boom)
    _frames(app)
    assert len(calls) == 1
