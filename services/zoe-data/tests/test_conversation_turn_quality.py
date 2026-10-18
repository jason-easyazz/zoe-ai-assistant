"""Turn-quality defects found in Zoe's live conversations on 2026-10-12 (one PR, six items; invented examples only).

1. SPACERS ("Let me check.", "One sec."): at most ONE per turn across the timer filler, the tool ack and the tool filler; never the
   same line twice in a row on a panel; a wide pool in the lexicon (en, es); silence on a chat-domain turn, on an emotional or
   personal disclosure, and on a recall/identity question the stack answers fast; a family that fits what the turn is doing.
2. (identity on the fast path: tests/test_skybridge_router_veto.py section 5 + the lane-parity cell ``identity_name``.)
3. "How are you?" is a question for Zoe, not the canned greeting (``intent_router._GREETING_RE``).
4. The guest PIN challenge: small talk toward Zoe is public even when its lead-in says "i want to" (``voice_scope``).
5. "Sounds good" right after Zoe's OFFER accepts it: it must reach the brain, not the canned "Okay.".
6. (duplicate user rows: the unit contract below + tests/test_voice_lane_parity.py end to end.)

Negative controls (each run by hand when this was written; every one went red and green again on restore):
  voice_spacer.SpacerTurn.veto -> drop the ``already_spoken`` line                (one-per-turn tests)
  voice_spacer.pick            -> ignore the panel's recent lines                  (no-repeat tests)
  voice_spacer.SpacerTurn.veto -> drop the ``chat_domain`` / ``disclosure`` / ``fast_first_token`` / ``fast_lane`` branch
  intent_router._GREETING_RE   -> put the ``how are you`` alternative back         (the greeting tests)
  voice_scope._is_small_talk_toward_zoe -> return False                            (the guest-scope tests)
  intent_router.ack_answers_offer -> return False                                  (the offer tests)
  routers.voice_tts._schedule_voice_chat_save -> drop the ``_USER_TURN_SAVED`` check  (the duplicate-row tests)
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic + TestClient; no models, no DB, no network

import conversation_cues
import intent_router
import lexicons
import voice_first_sound as fs
import voice_scope
import voice_spacer
from voice_spacer import SpacerTurn

PANEL = "panel-quality"
DISCLOSURES = [
    "My friend has had a really rough week and I'm worried about her",
    "I feel really sad today",
    "my grandmother passed away last week",
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    voice_spacer.reset()
    for k in ("ZOE_VOICE_FILLER_PHRASES", "ZOE_VOICE_FILLER_EXPECT_MIN_S", "ZOE_FIRST_SOUND_TOOL_ACK"):
        monkeypatch.delenv(k, raising=False)
    yield
    voice_spacer.reset()


def turn(text: str = "what is on tomorrow", domain: str = "calendar", panel: str = PANEL) -> SpacerTurn:
    t = SpacerTurn(panel_key=panel, text=text)
    t.note_router({"routed": domain} if domain else {})
    return t


def lines(lang: str, family: str) -> list[str]:
    return list(lexicons.load(lang)["spacer"]["families"][family])


# ── 1a. one spacer per turn ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_tool_ack_takes_the_turns_one_spacer_and_the_timer_filler_finds_it_taken(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn()
    ack = fs.dispatch_ack({"routed": "calendar"}, spacer=t)
    assert ack and t.spoken == "tool_ack"
    assert t.timer_line() is None and t.veto("timer").startswith("already_spoken")


def test_the_timer_filler_takes_the_turns_one_spacer_and_the_tool_ack_and_tool_filler_find_it_taken(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn()
    assert t.timer_line() and t.spoken == "timer"
    assert fs.dispatch_ack({"routed": "calendar"}, spacer=t) is None
    assert t.tool_filler_line("Let me check your calendar.") is None


def test_the_live_double_spacer_order_timer_filler_first_then_the_tool_ack_on_one_calendar_turn(monkeypatch):
    """2026-10-12 18:42:43: the timer filler spoke ("Hmm, let me look.") and THEN the dispatch-time tool ack ("Let me check your
    calendar.") on the same turn. The timer has the turn first here; the ack must find it taken and say nothing."""
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn("what do I have on tomorrow", "calendar")
    first = t.timer_line()                                            # 3.5 s in, the first audio is late
    second = fs.dispatch_ack({"routed": "calendar"}, spacer=t)        # the brain is dispatched afterwards
    assert first and second is None and t.spoken == "timer"
    # ... and the next turn on the panel does not open with the same words
    nxt = turn("what do I have on tomorrow", "calendar")
    assert nxt.timer_line() != first


def test_the_tool_filler_is_the_same_budget():
    t = turn()
    assert t.tool_filler_line("Let me check your calendar.") == "Let me check your calendar."
    assert t.timer_line() is None and t.tool_ack_line() is None


def test_two_layers_racing_for_the_turn_cannot_both_win():
    """The check and the claim are separate steps: both layers can pass ``veto`` before either has synthesized. The claim is
    synchronous, so exactly one of them gets the turn."""
    t = turn()
    assert t.veto("timer") is None and t.veto("tool_ack") is None
    assert t.claim("tool_ack") is True and t.claim("timer") is False
    assert t.spoken == "tool_ack"


def test_a_failed_synthesis_gives_the_spacer_back():
    t = turn()
    assert t.timer_line()
    t.release("timer")
    assert t.spoken is None and t.tool_ack_line() is not None


def test_release_only_lets_go_of_your_own_claim():
    t = turn()
    t.claim("tool_ack")
    t.release("timer")
    assert t.spoken == "tool_ack"


# ── 1b. never the same line twice in a row ─────────────────────────────────────────────────────────────────────────────────────
def test_no_line_repeats_back_to_back_on_a_panel_across_every_mechanism():
    spoken = []
    for i in range(400):
        t = turn(panel="panel-a")
        line = (t.timer_line, t.tool_ack_line, lambda: t.tool_filler_line("Let me check your calendar."))[i % 3]()
        assert line, i
        spoken.append(line)
    assert all(a != b for a, b in zip(spoken, spoken[1:])), [(a, b) for a, b in zip(spoken, spoken[1:]) if a == b][:3]
    assert len(set(spoken)) >= 6          # and it really uses the pool, not two lines in turn


def test_the_recent_lines_are_kept_per_panel():
    a, b = turn(panel="panel-a"), turn(panel="panel-b")
    voice_spacer.pick(["only"], "panel-a")
    assert voice_spacer.last_line("panel-a") == "only" and voice_spacer.last_line("panel-b") is None
    assert a.timer_line() and b.timer_line()


def test_a_pool_of_one_still_speaks_and_a_pool_of_two_alternates():
    assert voice_spacer.pick(["only"], PANEL) == "only" and voice_spacer.pick(["only"], PANEL) == "only"
    seq = [voice_spacer.pick(["x", "y"], "panel-two") for _ in range(20)]
    assert all(a != b for a, b in zip(seq, seq[1:]))


def test_the_tool_filler_does_not_repeat_the_last_line():
    first, second = turn(), turn()
    assert first.tool_filler_line("Let me check your calendar.") == "Let me check your calendar."
    again = second.tool_filler_line("Let me check your calendar.")
    assert again and again != "Let me check your calendar."


# ── 1c. a wide pool in the lexicon, a family that fits ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_lexicon_pools_are_wide(lang):
    sec = lexicons.load(lang)["spacer"]
    assert {"lookup", "recall", "action", "generic"} <= set(sec["families"])
    assert all(len(v) >= 4 for v in sec["families"].values()), lang
    for domain in ("calendar", "lists", "reminders", "weather"):
        assert len(sec["tool_ack"][domain]) >= 3, (lang, domain)
    assert len(voice_spacer.timer_pool("calendar", lang)) >= 6


@pytest.mark.parametrize("lang", ["en", "es"])
def test_every_ack_domain_has_a_pool_and_chat_has_none(lang):
    for domain in voice_spacer.TOOL_ACK_DOMAINS:
        assert voice_spacer.tool_ack_pool(domain, lang), (lang, domain)
    assert "chat" not in voice_spacer.TOOL_ACK_DOMAINS
    assert voice_spacer.tool_ack_pool("chat", lang) == [] and voice_spacer.tool_ack_pool("", lang) == []


def test_a_spanish_turn_speaks_a_spanish_line():
    t = SpacerTurn(panel_key=PANEL, text="qué tengo mañana en el calendario")
    t.note_router({"routed": "calendar"})
    assert t.lang == "es"
    assert t.tool_ack_line() in lexicons.load("es")["spacer"]["tool_ack"]["calendar"]


def test_let_me_check_is_for_lookups_only():
    check = re.compile(r"\b(check|checking|look|looking)\b", re.I)
    for family in ("recall", "action", "generic"):
        assert not [p for p in lines("en", family) if check.search(p)], family
    for domain in ("music", "smart_home", "timers", "memory", ""):
        assert not [p for p in voice_spacer.timer_pool(domain) if check.search(p)], domain
    for domain in ("calendar", "lists", "reminders", "weather"):
        assert "Let me check." in voice_spacer.timer_pool(domain) or any(
            check.search(p) for p in voice_spacer.timer_pool(domain)), domain


def test_a_recall_turn_says_think_not_check():
    pool = voice_spacer.timer_pool("memory")
    assert pool and all("think" in p.lower() or "remember" in p.lower() for p in pool), pool


def test_the_env_pin_still_overrides_the_timer_pool(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_FILLER_PHRASES", "Alpha.|Beta. |")
    assert voice_spacer.timer_pool("calendar") == ["Alpha.", "Beta."]
    assert turn().timer_line() in ("Alpha.", "Beta.")


def test_the_tool_ack_flag_still_gates_the_dispatch_ack(monkeypatch):
    assert fs.dispatch_ack({"routed": "calendar"}, spacer=turn()) is None            # flag unset = off
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    assert fs.dispatch_ack({"routed": "calendar"}, spacer=turn())
    for kw in ("audio_started", "filler_emitted", "processing_ack_sent"):
        assert fs.dispatch_ack({"routed": "calendar"}, spacer=turn(), **{kw: True}) is None


# ── 1d. silence where a spacer would be wrong ──────────────────────────────────────────────────────────────────────────────────
def test_a_chat_domain_turn_gets_no_timer_filler():
    t = turn("what is the meaning of life", "chat")
    assert t.timer_line() is None and t.spoken is None
    assert t.veto("timer") == "chat_domain"


def test_an_unknown_domain_still_gets_a_generic_timer_filler():
    t = turn("tell me something", "")
    assert t.timer_line() in lines("en", "generic")


@pytest.mark.parametrize("text", DISCLOSURES)
@pytest.mark.parametrize("domain", ["people", "memory", "calendar", ""])
def test_a_disclosure_gets_no_spacer_from_any_mechanism(text, domain, monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn(text, domain)
    assert t.disclosure
    assert t.timer_line() is None and t.tool_ack_line() is None and t.tool_filler_line("One sec.") is None
    assert fs.dispatch_ack({"routed": domain}, spacer=t) is None
    assert t.spoken is None


def test_a_first_person_feeling_with_no_restraint_class_is_still_a_disclosure():
    pytest.importorskip("zoe_flue_client")
    assert turn("I had a rough day", "people").disclosure


def test_an_ordinary_lookup_is_not_a_disclosure():
    assert not turn("what is on my calendar tomorrow").disclosure


@pytest.mark.parametrize("text,domain", [("what's my name", "memory"), ("who am I", "people")])
def test_an_identity_question_gets_no_tool_ack(text, domain, monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn(text, domain)
    assert t.fast and t.tool_ack_line() is None and t.spoken is None
    assert t.veto("tool_ack") == "fast_lane" or t.veto("tool_ack") == "already_spoken:tool_ack" or t.veto("tool_ack")
    assert fs.dispatch_ack({"routed": domain}, spacer=t) is None


def test_a_recall_floor_question_gets_no_tool_ack(monkeypatch):
    pytest.importorskip("zoe_flue_client")
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn("What is my sister called?", "memory")
    assert t.fast and t.tool_ack_line() is None


def test_a_memory_turn_that_is_not_a_fast_lane_still_gets_its_recall_line(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")
    t = turn("remember that the gate code is blue", "memory")
    assert not t.fast
    assert fs.dispatch_ack({"routed": "memory"}, spacer=t) in lines("en", "recall")


def test_the_fast_lane_only_covers_the_memory_and_people_domains():
    assert not turn("what's my name", "calendar").fast and not turn("what's my name", "chat").fast


def test_no_timer_filler_when_the_first_token_is_expected_inside_a_second(monkeypatch):
    t = turn()
    t.expect_first_token(0.4)                       # the hook a measured TTFT or a cache hit at dispatch calls
    assert t.timer_line() is None and t.veto("timer") == "fast_first_token"
    slow = turn()
    slow.expect_first_token(2.0)
    assert slow.timer_line()
    monkeypatch.setenv("ZOE_VOICE_FILLER_EXPECT_MIN_S", "3")
    late = turn()
    late.expect_first_token(2.0)
    assert late.timer_line() is None


def test_the_fast_lane_hands_the_timer_filler_its_estimate():
    t = turn("what's my name", "memory")
    assert t.expected_ttft_s is not None and t.expected_ttft_s < voice_spacer.expect_min_s()
    assert t.timer_line() is None


def test_the_tool_filler_is_not_silenced_by_a_chat_domain_only_the_timer_is():
    t = turn("search the web for tide tables", "chat")
    assert t.veto("tool_filler") is None and t.veto("timer") == "chat_domain"


# ── 1e. the live wiring: /voice/turn_stream's timer filler reads the same turn state voice_command fills ───────────────────────
def _stream_app(monkeypatch, brain, transcript):
    from fastapi import FastAPI
    import routers.voice_tts as vt

    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "1")
    monkeypatch.setenv("ZOE_VOICE_FILLER_AFTER_S", "0.15")

    async def _stt(_path):
        return transcript

    async def _tts(_text):
        return b"RIFFfakewav"

    monkeypatch.setattr(vt, "_transcribe_audio", _stt)
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _tts)
    monkeypatch.setattr(vt, "voice_command", brain)
    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {"source": "device", "panel_id": PANEL, "user_id": "voice-daemon"}
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None
    return app


def _frames(app):
    from fastapi.testclient import TestClient

    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": PANEL}
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200
        out = []
        for line in r.iter_lines():
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    out.append({"_raw_audio": True})
        return out


def _slow_brain(domain, claim=None):
    async def brain(payload, caller=None, stream=True, db=None):
        spacer = payload["_spacer"]                                  # the shared turn state the wrapper put in the payload
        spacer.note_router({"routed": domain} if domain else {})     # what voice_command does the moment the router has run
        if claim:
            spacer.claim(claim)
        await asyncio.sleep(0.6)
        return {"reply": "Here is the real answer.", "audio_base64": base64.b64encode(b"RIFFreal").decode()}
    return brain


def _fillers(frames):
    return [f["text"] for f in frames if f.get("provider") == "filler"]


def test_a_slow_lookup_turn_speaks_exactly_one_line_from_its_family(monkeypatch):
    voice_spacer.reset()
    frames = _frames(_stream_app(monkeypatch, _slow_brain("calendar"), "what is on tomorrow"))
    spoken = _fillers(frames)
    assert len(spoken) == 1 and spoken[0] in voice_spacer.timer_pool("calendar"), frames
    assert any(f.get("done") or "full_audio" in f for f in frames)


def test_a_slow_chat_turn_speaks_no_filler(monkeypatch):
    frames = _frames(_stream_app(monkeypatch, _slow_brain("chat"), "what is the meaning of life"))
    assert _fillers(frames) == [], frames
    assert any("full_audio" in f for f in frames)


@pytest.mark.parametrize("text", DISCLOSURES)
def test_a_slow_disclosure_turn_speaks_no_filler(monkeypatch, text):
    assert _fillers(_frames(_stream_app(monkeypatch, _slow_brain("people"), text))) == []


def test_a_turn_whose_tool_ack_already_spoke_gets_no_timer_filler(monkeypatch):
    assert _fillers(_frames(_stream_app(monkeypatch, _slow_brain("calendar", claim="tool_ack"), "what is on tomorrow"))) == []


def test_a_slow_identity_turn_speaks_no_filler(monkeypatch):
    assert _fillers(_frames(_stream_app(monkeypatch, _slow_brain("memory"), "what's my name"))) == []


def test_the_same_panel_never_hears_the_same_filler_twice_running(monkeypatch):
    voice_spacer.reset()
    heard = []
    for _ in range(12):
        heard += _fillers(_frames(_stream_app(monkeypatch, _slow_brain("calendar"), "what is on tomorrow")))
    assert len(heard) == 12 and all(a != b for a, b in zip(heard, heard[1:])), heard


def test_voice_command_shares_the_turn_state_with_the_ack_and_the_tool_filler():
    import inspect
    import routers.voice_tts as vt

    src = inspect.getsource(vt.voice_command)
    assert "_spacer.note_router(_router_decision)" in src
    assert "spacer=_spacer," in src and "_spacer.tool_filler_line(" in src
    assert 'payload or {}).get("_spacer")' in src
    wrapper = inspect.getsource(vt.voice_turn_stream)
    assert '"_spacer": _spacer' in wrapper and "_spacer.timer_line()" in wrapper and 'release("timer")' in wrapper


# ── 3. "how are you?" is a question for Zoe, not the canned greeting ──────────────────────────────────────────────────────────
WELLBEING = ["how are you", "how are you today", "how are you going", "How are you going?", "how's it going", "hi there how are you",
             "hey zoe how are you?", "hello how are you today", "good morning how are you", "how you doing"]


@pytest.mark.parametrize("text", WELLBEING)
def test_a_wellbeing_question_is_not_a_canned_greeting(text):
    intent = intent_router.detect_intent(text)
    assert intent is None or intent.name != "greeting", (text, intent)


@pytest.mark.parametrize("lang", [c for c in lexicons.LANGS if lexicons.load(c).get("wellbeing")])
def test_no_lexicon_wellbeing_example_is_matched_by_the_greeting_pattern(lang):
    for ex in lexicons.load(lang)["wellbeing"]["examples"]:
        if conversation_cues.is_wellbeing_question(ex):
            intent = intent_router.detect_intent(ex)
            assert intent is None or intent.name != "greeting", (lang, ex, intent)


@pytest.mark.parametrize("text,name", [
    ("hi", "greeting"), ("hello there", "greeting"), ("g'day", "greeting"), ("what's up", "greeting"),
    ("good afternoon", "greeting"), ("good morning", "good_morning"), ("thanks", "acknowledgement"),
])
def test_real_greetings_keep_their_instant_answer(text, name):
    assert intent_router.detect_intent(text).name == name


def test_a_wellbeing_phrase_with_a_real_request_is_not_swallowed_either():
    # None is right too: #2004 refuses to store the garbled item "how are you and add milk" and leaves the compound to the brain.
    got = intent_router.detect_intent("how are you and add milk to my shopping list")
    assert got is None or got.name != "greeting"


# ── 4. the guest PIN challenge on a casual question ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "I want to know how you are", "i want to know how you are", "I just want to know how you are doing", "how you are",
    "how are you", "How are you today?", "can I ask how you are", "tell me about yourself", "are you okay",
    "hey zoe I wanted to ask how you are doing",
])
def test_small_talk_toward_zoe_is_public(text):
    d = voice_scope.classify(text)
    assert d.scope == "public", (text, d)


@pytest.mark.parametrize("text", [
    "what's on my calendar", "I want to know what's on my calendar", "remind me to call the dentist",
    "I want to add milk", "how are you and what's on my calendar tomorrow", "what did I say about the dentist",
    "I need to know my appointments", "what's my shopping list",
])
def test_real_personal_requests_stay_user_scoped(text):
    assert voice_scope.classify(text).scope == "user_scoped", text


@pytest.mark.parametrize("lang", [c for c in lexicons.LANGS if lexicons.load(c).get("wellbeing")])
def test_every_lexicon_wellbeing_example_is_public(lang):
    for ex in lexicons.load(lang)["wellbeing"]["examples"]:
        assert voice_scope.classify(ex).scope == "public", (lang, ex)


# ── 5. "sounds good" after an offer is an acceptance ───────────────────────────────────────────────────────────────────────────
OFFERS = [
    "Glad that helped. How about the history of coffee?",
    "That's a lovely thought. Would you like to hear more?",
    "Want me to put that on your list?",
    "I can also read you tomorrow's headlines, if you'd like.",
]
NOT_OFFERS = ["It's sunny and 22 degrees today.", "Your sister is called Marisa.", "Sure, it's done.", ""]


@pytest.mark.parametrize("reply", OFFERS)
def test_a_reply_that_ends_in_an_offer_is_one(reply):
    assert conversation_cues.ends_with_offer(reply)


@pytest.mark.parametrize("reply", NOT_OFFERS)
def test_a_plain_reply_is_not_an_offer(reply):
    assert not conversation_cues.ends_with_offer(reply)


@pytest.mark.parametrize("lang", [c for c in lexicons.LANGS if lexicons.load(c).get("offer")])
def test_the_lexicons_offer_examples_hold(lang):
    sec = lexicons.load(lang)["offer"]
    assert all(conversation_cues.ends_with_offer(x) for x in sec["examples"]), lang
    assert not any(conversation_cues.ends_with_offer(x) for x in sec["not_offers"]), lang


def _previous(monkeypatch, reply):
    async def prev(_user_id):
        return reply

    monkeypatch.setattr(intent_router, "_previous_assistant_message", prev)


@pytest.mark.parametrize("ack", ["sounds good", "Sounds good.", "okay", "ok", "alright", "cool"])
async def test_an_ack_after_an_offer_goes_to_the_brain_not_the_canned_okay(monkeypatch, ack):
    _previous(monkeypatch, OFFERS[0])
    assert await intent_router.detect_and_extract_intent(ack, "demo_user") is None


@pytest.mark.parametrize("reply", NOT_OFFERS[:3] + [None])
async def test_an_ack_after_a_plain_reply_is_still_the_instant_ack(monkeypatch, reply):
    _previous(monkeypatch, reply)
    got = await intent_router.detect_and_extract_intent("sounds good", "demo_user")
    assert got is not None and got.name == "acknowledgement"


async def test_thanks_after_an_offer_is_still_thanks(monkeypatch):
    _previous(monkeypatch, OFFERS[0])
    got = await intent_router.detect_and_extract_intent("thanks", "demo_user")
    assert got is not None and got.name == "acknowledgement" and got.slots["kind"] == "thanks"


async def test_a_request_after_an_offer_is_untouched(monkeypatch):
    _previous(monkeypatch, OFFERS[0])
    got = await intent_router.detect_and_extract_intent("what time is it", "demo_user")
    assert got is not None and got.name == "time_query"


async def test_the_lane_that_does_not_persist_its_turns_hands_in_its_own_last_reply():
    ack = intent_router.Intent("acknowledgement", {"kind": "ack"})
    assert await intent_router.ack_answers_offer(ack, "u", previous=OFFERS[0]) is True
    assert await intent_router.ack_answers_offer(ack, "u", previous="It is sunny.") is False
    assert await intent_router.ack_answers_offer(ack, "u", previous="") is False
    assert await intent_router.ack_answers_offer(intent_router.Intent("acknowledgement", {"kind": "thanks"}), "u",
                                                 previous=OFFERS[0]) is False
    assert await intent_router.ack_answers_offer(intent_router.Intent("time_query", {}), "u", previous=OFFERS[0]) is False


# ── 7. "turn it back on again" after "turn the light off" ──────────────────────────────────────────────────────────────────────
# Live 2026-10-12: intent_miss, then a brain reply about "getting back to chatting". The last on/off command's target rides in the
# panel's Skybridge context (and the keyword lane's ConversationContext) for ~60 s; a bare pronoun command resolves to it.
import time

import skybridge_service as sky
from conversation_context import ConversationContext


def _lex_followups(kind):
    return [(lang, ex) for lang in lexicons.LANGS if lexicons.load(lang).get("smart_home_followup")
            for ex in lexicons.load(lang)["smart_home_followup"]["examples"][kind]]


@pytest.mark.parametrize("kind", ["on", "off", "reverse"])
def test_the_lexicons_followup_examples_resolve(kind):
    for lang, ex in _lex_followups(kind):
        assert conversation_cues.smart_home_followup(ex) == kind, (lang, ex)


@pytest.mark.parametrize("lang", [c for c in lexicons.LANGS if lexicons.load(c).get("smart_home_followup")])
def test_the_lexicons_non_followups_are_left_alone(lang):
    for ex in lexicons.load(lang)["smart_home_followup"]["not_followups"]:
        assert conversation_cues.smart_home_followup(ex) is None, (lang, ex)


def _memory(action="turn_off", query="light", age_s=5.0):
    return {"intent": {"domain": "smart_home", "action": action}, "cards": [],
            sky._SMART_HOME_TARGET_KEY: {"action": action, "query": query, "entity_ids": ["light.demo_one"],
                                         "ts": time.time() - age_s}}


@pytest.mark.parametrize("text,action", [
    ("Turn it back on again", "turn_on"), ("turn it back on.", "turn_on"), ("switch that on", "turn_on"),
    ("and put it back on", "turn_on"), ("turn it back", "turn_on"),
])
def test_after_turn_off_a_pronoun_command_resolves_to_the_same_device(text, action):
    got = sky.classify_skybridge_intent(text, _memory("turn_off"))
    assert got is not None and (got.domain, got.action, got.query) == ("smart_home", action, "light"), got
    assert got.entity_id == "light.demo_one"          # the device that was switched, by id - not a re-match of the words


def test_a_memory_without_a_resolved_entity_is_no_target():
    ctx = _memory("turn_off")
    ctx[sky._SMART_HOME_TARGET_KEY]["entity_ids"] = []
    got = sky.classify_skybridge_intent("turn it back on", ctx)
    assert got is None or got.domain != "smart_home"


def test_after_turn_on_the_reverse_and_the_repeat_both_work():
    assert sky.classify_skybridge_intent("turn it back", _memory("turn_on")).action == "turn_off"
    assert sky.classify_skybridge_intent("now turn it off", _memory("turn_on")).action == "turn_off"


def test_no_memory_or_a_stale_one_leaves_the_utterance_to_the_brain(monkeypatch):
    for ctx in (None, {}, _memory(age_s=120.0), {sky._SMART_HOME_TARGET_KEY: "junk"}):
        got = sky.classify_skybridge_intent("Turn it back on again", ctx)
        assert got is None or got.domain != "smart_home", (ctx, got)
    monkeypatch.setenv("ZOE_SMART_HOME_FOLLOWUP_TTL_S", "300")          # the window is configurable
    assert sky.classify_skybridge_intent("Turn it back on again", _memory(age_s=120.0)).action == "turn_on"


@pytest.mark.parametrize("text", ["turn it up", "put it on my shopping list", "turn it back on and play some jazz",
                                  "what is it", "turn on the kitchen light"])
def test_other_pronoun_sentences_are_not_stolen_by_the_memory(text):
    got = sky.classify_skybridge_intent(text, _memory("turn_off"))
    assert got is None or got.query != "light" or text.startswith("turn on the"), (text, got)


async def test_the_light_command_leaves_its_target_and_the_followup_reuses_it(monkeypatch):
    import smart_home_service

    seen = []

    async def fake_resolve(intent, room_eids=None):
        seen.append((intent.action, intent.query))
        return {"handled": True, "intent": {"domain": "smart_home", "action": intent.action}, "spoken_summary": "Done.", "cards": [],
                "controlled_entities": ["light.demo_one"]}

    monkeypatch.setattr(smart_home_service, "resolve_smart_home", fake_resolve)
    first = await sky.resolve_skybridge_request("Turn the light off.", "guest")
    assert first["handled"] and first["skybridge_context"][sky._SMART_HOME_TARGET_KEY]["action"] == "turn_off"
    # the router never saw the pronouns: it calls the bare words chat, and the context follow-up rule still lets the turn through
    chat = {"domain": "chat", "routed": "chat", "score": 0.4, "two_stage": {"domain": "chat", "tool": None, "gated": True,
                                                                        "head_top": "chat", "head_conf": 0.9, "reason": "chat_top"}}
    second = await sky.resolve_skybridge_request("Turn it back on again.", "guest", context=first["skybridge_context"],
                                                 router_decision=chat)
    assert second["handled"] is True
    assert seen[0][0] == "turn_off" and seen[1] == ("turn_on", seen[0][1])      # same device, opposite action
    third = await sky.resolve_skybridge_request("turn it off", "guest", context=second["skybridge_context"])
    assert third["handled"] and seen[2][0] == "turn_off"                          # the memory refreshed on the follow-up


def _wire_home(monkeypatch, room):
    """Two lights and the REAL ``resolve_smart_home``; only the hub calls are faked. ``room`` is the speaker's room entity set."""
    import smart_home_service as shs

    devices = [shs._device_from_light({"entity_id": "light.bedroom_lamp", "state": "on", "attributes": {"friendly_name": "Bedroom Lamp"}}),
               shs._device_from_light({"entity_id": "light.kitchen_lamp", "state": "on", "attributes": {"friendly_name": "Kitchen Lamp"}})]
    switched = []

    async def list_devices():
        return [dict(d) for d in devices]

    async def list_scenes():
        return []

    async def set_device(entity_id, on, *, brightness_pct=None):
        switched.append((entity_id, on))
        return True

    async def room_eids(_ctx, _db):
        return set(room["eids"])

    monkeypatch.setattr(shs, "list_devices", list_devices)
    monkeypatch.setattr(shs, "list_scenes", list_scenes)
    monkeypatch.setattr(shs, "set_device", set_device)
    monkeypatch.setattr(sky, "_room_eids_for_context", room_eids)
    return switched


async def test_the_followup_switches_the_device_the_resolver_selected_not_a_re_match_of_the_words(monkeypatch):
    """Codex #2001: the first version stored the ORIGINAL intent (an empty entity id). "turn the light off" said in the bedroom is
    room-disambiguated to the bedroom lamp; "turn it back on" from a moment later must switch THAT lamp - with no room context
    it would otherwise be a "which one?" question over both lights."""
    room = {"eids": {"light.bedroom_lamp"}}
    switched = _wire_home(monkeypatch, room)
    first = await sky.resolve_skybridge_request("turn the light off", "guest")
    assert first["handled"] and switched == [("light.bedroom_lamp", False)]
    assert first["controlled_entities"] == ["light.bedroom_lamp"]
    assert first["skybridge_context"][sky._SMART_HOME_TARGET_KEY]["entity_ids"] == ["light.bedroom_lamp"]
    room["eids"] = set()                                             # the room context is gone (or another panel): words alone are ambiguous
    asked = await sky.resolve_skybridge_request("turn the light on", "guest")
    assert "Which one?" in asked["spoken_summary"] and switched == [("light.bedroom_lamp", False)]
    second = await sky.resolve_skybridge_request("turn it back on", "guest", context=first["skybridge_context"])
    assert second["handled"] and switched[-1] == ("light.bedroom_lamp", True), switched


async def test_a_command_that_switched_nothing_leaves_no_target(monkeypatch):
    room = {"eids": set()}
    switched = _wire_home(monkeypatch, room)
    ambiguous = await sky.resolve_skybridge_request("turn the light off", "guest")      # two lights, no room: a question, nothing switched
    assert "Which one?" in ambiguous["spoken_summary"] and switched == []
    assert sky._SMART_HOME_TARGET_KEY not in ambiguous["skybridge_context"]


async def test_a_plural_sweep_is_remembered_whole(monkeypatch):
    switched = _wire_home(monkeypatch, {"eids": set()})
    first = await sky.resolve_skybridge_request("turn the lights off", "guest")
    assert sorted(first["controlled_entities"]) == ["light.bedroom_lamp", "light.kitchen_lamp"]
    await sky.resolve_skybridge_request("switch them back on", "guest", context=first["skybridge_context"])
    assert sorted(e for e, on in switched if on) == ["light.bedroom_lamp", "light.kitchen_lamp"]


def test_the_keyword_lane_resolves_it_too_inside_the_window():
    ctx = ConversationContext()
    ctx.activate("smart_home", {"action": "turn_off", "entity": "light", "room": "kitchen"}, "turn off the kitchen lights")
    got = intent_router.detect_intent("Turn it back on again", context=ctx, log_miss=False)
    assert got is not None and got.name == "smart_home" and got.slots["action"] == "turn_on" and got.slots["room"] == "kitchen"
    ctx.updated_at -= 90                                                          # fresh for the context (120 s), stale for "it" (60 s)
    assert ctx.is_fresh()
    stale = intent_router.detect_intent("Turn it back on again", context=ctx, log_miss=False)
    assert stale is None or stale.name != "smart_home"
    assert intent_router.detect_intent("Turn it back on again", log_miss=False) is None


# ── 6. the user turn is written once ─────────────────────────────────────────────────────────────────────────────────────────
def _arrival(session="s1", text="what time is it", *, saved, settled=True):
    import routers.voice_tts as vt

    row = vt._ArrivalRow(session, text)
    row.saved = saved
    if settled:
        row.settled.set()
    return row


async def _run_save(monkeypatch, *, saved_on_arrival, settle_timeout=None, user_text="what time is it"):
    import routers.voice_tts as vt

    rows, scheduled = [], []
    if settle_timeout is not None:
        monkeypatch.setattr(vt, "_ARRIVAL_SETTLE_TIMEOUT_S", settle_timeout)

    async def fake_save(session_id, role, content, user_id=None, **_kw):
        rows.append((role, content))
        return True

    async def fake_ensure(session_id, user_id):
        return None

    monkeypatch.setitem(sys.modules, "routers.chat", types.SimpleNamespace(
        _save_chat_message=fake_save, _ensure_user_and_chat_session=fake_ensure))
    monkeypatch.setattr(vt, "_spawn_bg", scheduled.append)
    vt._USER_TURN_SAVED.set(saved_on_arrival)
    await vt._schedule_voice_chat_save("s1", user_text, "It's noon.", "demo_user")
    for task in scheduled:
        await task
    return rows


BOTH = [("user", "what time is it"), ("assistant", "It's noon.")]


async def test_a_user_turn_whose_arrival_insert_is_confirmed_is_not_saved_again(monkeypatch):
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(saved=True)) == [("assistant", "It's noon.")]


async def test_a_failed_arrival_insert_is_retried_by_the_replys_save(monkeypatch):
    """Codex #2001 P1: ``_save_chat_message`` swallows a DB failure and returns False. Marking the row saved when the insert was
    merely SCHEDULED left a turn whose first insert failed with no user row at all."""
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(saved=False)) == BOTH


async def test_an_arrival_insert_that_never_settles_does_not_lose_the_row(monkeypatch):
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(saved=False, settled=False), settle_timeout=0.05) == BOTH


async def test_the_replys_save_waits_for_a_slow_arrival_insert_and_then_defers_to_it(monkeypatch):
    row = _arrival(saved=False, settled=False)

    async def land_later():
        await asyncio.sleep(0.05)
        row.saved = True
        row.settled.set()

    task = asyncio.ensure_future(land_later())
    assert await _run_save(monkeypatch, saved_on_arrival=row, settle_timeout=5.0) == [("assistant", "It's noon.")]
    await task


async def test_a_user_turn_not_saved_on_arrival_is_still_saved_with_the_reply(monkeypatch):
    assert await _run_save(monkeypatch, saved_on_arrival=None) == BOTH
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(text="something else", saved=True)) == BOTH
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(session="other-session", saved=True)) == BOTH


async def test_the_streaming_finalizer_matches_its_arrival_by_session_and_still_saves_both_turns(monkeypatch):
    """Codex #2001 P1 (thread PRRT_kwDOPZpkks6rpMQj): the streaming finalizer calls the save with an intentionally EMPTY ``user_text``
    (the user turn was saved pre-stream), so the arrival row never matched - the save neither waited for the arrival attempt nor
    retried a failed user insert, leaving an assistant-only exchange on the primary streaming brain path."""
    # the arrival insert failed: the finalizer must write the user turn (the arrival's text) AND the reply, once each
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(saved=False), user_text="") == BOTH
    # the arrival insert landed: only the reply - no duplicate user row
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(saved=True), user_text="") == [("assistant", "It's noon.")]
    # a slow arrival insert is waited for, then deferred to
    row = _arrival(saved=False, settled=False)

    async def land_later():
        await asyncio.sleep(0.05)
        row.saved = True
        row.settled.set()

    task = asyncio.ensure_future(land_later())
    assert await _run_save(monkeypatch, saved_on_arrival=row, settle_timeout=5.0, user_text="") == [("assistant", "It's noon.")]
    await task
    # no arrival, or one for another session: nothing to recover - the reply alone (an empty user_text is intentional)
    assert await _run_save(monkeypatch, saved_on_arrival=None, user_text="") == [("assistant", "It's noon.")]
    assert await _run_save(monkeypatch, saved_on_arrival=_arrival(session="other-session", saved=False), user_text="") == [
        ("assistant", "It's noon.")]


def test_voice_command_marks_the_arrival_row_only_from_a_confirmed_insert_and_resets_per_turn():
    import inspect
    import routers.voice_tts as vt

    src = inspect.getsource(vt.voice_command)
    assert "_USER_TURN_SAVED.set(None)" in src and "_USER_TURN_SAVED.set(_arrival)" in src
    assert src.index("_USER_TURN_SAVED.set(None)") < src.index("_USER_TURN_SAVED.set(_arrival)")
    assert "_arrival.saved = bool(await _svc_user_turn(" in src and "_arrival.settled.set()" in src
