"""Struggle-detector RECALL fixes (2026-10-14 live shadow check): four struggle shapes the first rules missed, each with
hard negatives that must stay quiet and a negative control that proves the new guard is what keeps them quiet.

  (a) a chain of asks for the same thing: a COUNT-ONLY fast answer, "what are they?", the question reworded, "what are
      they?" again (two turns apart), then a fourth wording - REPHRASE / RE_ASK across the look-back;
  (b) a number homophone heard for a word ("two on the hall light") then said clearly - MISHEAR;
  (c) "it's actually X, not Y" with Y from the previous exchange - CORRECTION without a leading "no";
  (d) Skybridge recognised a fast domain, the router vetoed it, the brain served it - FALLTHROUGH (with a second witness).

Every phrasing is invented for the test (public repo).
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.ci_safe

import struggle_signals as s

A = "member-a"


def T(text, *, ts=0.0, reply="Done.", dom="", path=s.PATH_FAST, ch="voice", user=A, score=0.85, tsd=None,
      sim=None, sky="", vetoed=False, head="", conv="voice:hall", started=None):
    if tsd is None:
        tsd = dom
    return s.Turn(ts=ts, channel=ch, user_id=user, is_guest=(user == "guest"), conv_key=conv, text=text, reply=reply,
                  path=path, predicted_domain=dom, predicted_score=score, two_stage_domain=tsd, two_stage_active=True,
                  sim_scores=dict(sim or {}), sky_domain=sky, sky_vetoed=vetoed, head_top=head, started=started)


def run(turns):
    """The live worker's loop: each turn against the conversation's recent history."""
    out, hist = [], []
    for t in turns:
        out.append(sorted({e.signal for e in s.detect_window(hist, t)}))
        hist = (hist + [t])[-(s.LOOKBACK_TURNS + 1):]
    return out


# ── (a) the ask chain ────────────────────────────────────────────────────────

def _chain():
    count = "You have 2 events today."
    return [
        T("what do I have on my calendar?", ts=0, dom="calendar", reply=count),
        T("what are they?", ts=6, dom="", tsd="", path=s.PATH_BRAIN, reply="Let me check.", score=0.6),
        T("what are the two events on my calendar?", ts=16, dom="", tsd="", reply=count, score=0.6,
          sim={"calendar": 0.86}, sky="calendar"),
        T("what are they?", ts=21, dom="", tsd="", path=s.PATH_BRAIN, reply="I'm not sure.", score=0.6),
        T("I'd like to know what the two events on my calendar today are.", ts=38, dom="", tsd="", path=s.PATH_BRAIN,
          score=0.6, sim={"calendar": 0.84}),
    ]


def test_a_the_whole_chain_is_caught():
    assert run(_chain()) == [[], ["REPHRASE"], ["REPHRASE"], ["REPHRASE", "RE_ASK"], ["REPHRASE"]]


@pytest.mark.parametrize("reply", ["You have 1 event today.", "Lists has 3 items.", "There are 2 reminders.",
                                   "You have no events tomorrow.", "I found 4 contacts."])
@pytest.mark.parametrize("ask", ["what is it?", "what are they?", "which one?", "what are they exactly?", "so what is it"])
def test_a_detail_after_a_count_only_answer(reply, ask):
    prev = T("what's on it", ts=0, dom="calendar", reply=reply)
    assert [e.signal for e in s.detect(prev, T(ask, ts=6))] == ["REPHRASE"]


A_NEGATIVES = [
    (None, "what is it?"),                                                        # a first turn
    (T("tell me a riddle", ts=0, path=s.PATH_BRAIN, reply="What has keys but no locks?"), "what is it?"),   # not a count
    (T("what's on today", ts=0, dom="calendar", reply="Dentist at 3 and gym at 6."), "which one is first?"),  # a full answer
    (T("what's on today", ts=0, dom="calendar", path=s.PATH_BRAIN, reply="You have 2 events today."), "what are they?"),  # brain
    (T("what's on today", ts=0, dom="calendar", reply="You have 2 events today."), "what's the weather like?"),
    (T("what's on today", ts=0, dom="calendar", reply="You have 2 events today."), "great, thanks"),
    (T("what's on today", ts=0, dom="calendar", reply="You have 2 events today."), "what is it like outside?"),
    (T("what's on today", ts=0, dom="calendar", reply="You have 2 events today."), "what is it about tomorrow?"),
    (T("what's on today", ts=-200, dom="calendar", reply="You have 2 events today."), "what are they?"),   # too late
    (T("how many timers are running", ts=0, dom="timers", reply="Two timers are running and both finish soon."),
     "which one finishes first?"),
]


@pytest.mark.parametrize("prev,ask", A_NEGATIVES)
def test_a_hard_negatives(prev, ask):
    assert not {e.signal for e in s.detect(prev, T(ask, ts=6))} & {"REPHRASE", "RE_ASK"}, (ask,)


def test_a_lookback_needs_the_same_user_and_the_window():
    hist = [T("what is the capital of chad", ts=0, ch="chat", conv="chat:s"),
            T("and of niger?", ts=10, ch="chat", conv="chat:s")]
    assert "RE_ASK" in {e.signal for e in s.detect_window(hist, T("what is the capital of chad", ts=25, ch="chat",
                                                                    conv="chat:s"))}
    assert "RE_ASK" not in {e.signal for e in s.detect_window(hist, T("what is the capital of chad", ts=25, ch="chat",
                                                                        conv="chat:s", user="member-b"))}
    assert "RE_ASK" not in {e.signal for e in s.detect_window(hist, T("what is the capital of chad", ts=90, ch="chat",
                                                                        conv="chat:s"))}


def test_a_lookback_rephrase_needs_domain_evidence():
    """A conversational "tell me more about that" asked again around another question is NOT proved a struggle."""
    hist = [T("can you tell me some more information about that?", ts=0, path=s.PATH_BRAIN, dom="", tsd="", score=0.6),
            T("who painted the ceiling?", ts=15, path=s.PATH_BRAIN, dom="", tsd="", score=0.5)]
    cur = T("give me some more information about that.", ts=30, path=s.PATH_BRAIN, dom="", tsd="", score=0.6)
    assert "REPHRASE" not in {e.signal for e in s.detect_window(hist, cur)}


# ── (b) a number homophone heard for a word ──────────────────────────────────

B_POSITIVES = [("two on the hall light", "turn on the hall light"), ("ten the lights on", "turn the lights on"),
               ("two off the heater", "turn off the heater"), ("two on the porch light please", "turn on the porch light please"),
               ("then the volume down", "turn the volume down"), ("two off the fan", "turn off the fan")]
B_NEGATIVES = [("set two timers", "set ten timers"),                      # two numbers: a changed request
               ("turn on the light", "turn off the light"),               # polarity
               ("add two eggs", "add ten eggs"),
               ("two on the hall light", "turn on the hall light", "chat"),   # typed: not a mishearing
               ("two on the hall light", "turn on the hall light", "voice", 45.0),   # too late
               ("play two songs", "play some songs"),                     # "some" is not a sound-alike of "two"
               ("call for help", "call the help desk")]


@pytest.mark.parametrize("a,b", B_POSITIVES)
def test_b_number_homophone_mishear(a, b):
    prev = T(a, ts=0, path=s.PATH_BRAIN, dom="", tsd="", score=0.5, reply="Sorry?")
    assert "MISHEAR" in {e.signal for e in s.detect(prev, T(b, ts=10, dom="smart_home"))}, (a, b)


@pytest.mark.parametrize("case", B_NEGATIVES)
def test_b_hard_negatives(case):
    a, b, *rest = case
    ch = rest[0] if rest else "voice"
    gap = rest[1] if len(rest) > 1 else 10.0
    prev = T(a, ts=0, ch=ch, conv=f"{ch}:x")
    assert "MISHEAR" not in {e.signal for e in s.detect(prev, T(b, ts=gap, ch=ch, conv=f"{ch}:x"))}, (a, b)


# ── (c) "actually X, not Y" ──────────────────────────────────────────────────

C_POSITIVES = [
    ("I run a garbage shop in town.", "It's actually a garden shop, not a garbage shop."),
    ("my sister lives in Bristow", "it's Bristol not Bristow"),
    ("Add coffee to the list", "I wanted tea, not coffee."),
    ("remind me about the dentist on Tuesday", "Actually it was Thursday, not Tuesday."),
    ("I'm going to the bakery", "It's actually the bank, not the bakery. The bank."),
    ("book the table for six", "I said seven, not six."),
]
C_NEGATIVES = [
    ("what's on the list", "actually, can you also add milk?"),
    ("how was the trip", "it's not actually that bad"),
    ("what's on the list", "I want tea, not coffee."),                  # Y never came up
    ("are you hungry", "actually I'm not hungry"),
    ("shall I play music", "not now, thanks"),
    ("how was the walk", "it was cold, not freezing"),                    # Y new to the exchange
    ("tell me about whales", "actually that's not what I expected, wow"),
]


@pytest.mark.parametrize("a,b", C_POSITIVES)
def test_c_contrast_correction(a, b):
    got = {e.signal for e in s.detect(T(a, ts=0, path=s.PATH_BRAIN, reply="Okay."), T(b, ts=12, path=s.PATH_BRAIN))}
    assert "CORRECTION" in got, (a, b)


def test_c_the_wrong_word_may_come_from_zoes_reply():
    prev = T("add a drink to the list", ts=0, reply="Added coffee to the shopping list.")
    assert "CORRECTION" in {e.signal for e in s.detect(prev, T("I wanted tea, not coffee", ts=8))}


@pytest.mark.parametrize("a,b", C_NEGATIVES)
def test_c_hard_negatives(a, b):
    got = {e.signal for e in s.detect(T(a, ts=0, path=s.PATH_BRAIN, reply="Okay."), T(b, ts=12, path=s.PATH_BRAIN))}
    assert "CORRECTION" not in got, (a, b)


# ── (d) Skybridge recognised it, the router vetoed, the brain served ─────────

D_POSITIVES = [
    dict(sky="calendar", sim={"calendar": 0.81}),
    dict(sky="calendar", sim={"calendar": 0.5}, head="calendar"),          # the head's own top pick, under its gate
    dict(sky="timers", sim={"timers": 0.9}),
    dict(sky="lists", sim={"lists": 0.77}),
    dict(sky="weather", sim={"weather": 0.8}),
    dict(sky="reminders", sim={"reminders": 0.82}),
]
D_NEGATIVES = [
    dict(sky="weather", sim={"weather": 0.67}, head="chat"),               # a declarative that mentions rain
    dict(sky="calendar", sim={"calendar": 0.81}, path=s.PATH_FAST, reply="Here is the calendar."),   # the fast path served
    dict(sky="calendar", sim={"calendar": 0.81}, vetoed=False),            # not vetoed (a different path)
    dict(sky="people", sim={"people": 0.9}),                               # not a fast domain
    dict(sky="calendar", sim={"lists": 0.9}),                              # the witness speaks for another domain
    dict(sky="", sim={"calendar": 0.9}),
]


@pytest.mark.parametrize("kw", D_POSITIVES)
def test_d_skybridge_vetoed_fallthrough(kw):
    kw = {"vetoed": True, "path": s.PATH_BRAIN, **kw}
    t = T("schedule a call for nine tonight", dom="chat", tsd="chat", score=0.65, **kw)
    ev = [e for e in s.detect(None, t) if e.signal == "FALLTHROUGH"]
    assert ev and "skybridge_vetoed" in ev[0].reasons, kw


@pytest.mark.parametrize("kw", D_NEGATIVES)
def test_d_hard_negatives(kw):
    kw = {"vetoed": True, "path": s.PATH_BRAIN, **kw}
    t = T("schedule a call for nine tonight", dom="chat", tsd="chat", score=0.65, **kw)
    assert "FALLTHROUGH" not in {e.signal for e in s.detect(None, t)}, kw


# ── negative controls: break one new guard, a hard negative fires ────────────

def test_control_count_only_guard(monkeypatch):
    prev = T("tell me a riddle", ts=0, reply="What has keys but no locks?")
    assert "REPHRASE" not in {e.signal for e in s.detect(prev, T("what is it?", ts=5))}
    monkeypatch.setattr(s, "_COUNT_ONLY_RE", re.compile(r".*"))
    assert "REPHRASE" in {e.signal for e in s.detect(prev, T("what is it?", ts=5))}


def test_control_homophone_xor_guard(monkeypatch):
    prev, cur = T("set two timers", ts=0), T("set ten timers", ts=8)
    assert "MISHEAR" not in {e.signal for e in s.detect(prev, cur)}
    monkeypatch.setattr(s, "_NUMBER_HOMOPHONES", frozenset({"two"}))
    assert "MISHEAR" in {e.signal for e in s.detect(prev, cur)}


def test_control_contrast_needs_the_wrong_word_in_the_exchange(monkeypatch):
    prev, cur = T("what's on the list", ts=0, reply="Okay."), T("I want tea, not coffee.", ts=6)
    assert "CORRECTION" not in {e.signal for e in s.detect(prev, cur)}
    monkeypatch.setattr(s, "content_tokens", lambda toks: {"coffee"} | {t for t in toks if t not in s._STOP})
    assert "CORRECTION" in {e.signal for e in s.detect(prev, cur)}


def test_control_skybridge_second_witness(monkeypatch):
    t = T("it rained all over the shop", dom="chat", tsd="chat", score=0.6, path=s.PATH_BRAIN, sky="weather",
          vetoed=True, sim={"weather": 0.67}, head="chat")
    assert "FALLTHROUGH" not in {e.signal for e in s.detect(None, t)}
    monkeypatch.setattr(s, "SIM_DOMAIN_EVIDENCE", 0.5)
    assert "FALLTHROUGH" in {e.signal for e in s.detect(None, t)}


def test_control_lookback_domain_evidence(monkeypatch):
    hist = [T("can you tell me some more information about that?", ts=0, path=s.PATH_BRAIN, dom="", tsd="", score=0.6),
            T("who painted the ceiling?", ts=15, path=s.PATH_BRAIN, dom="", tsd="", score=0.5)]
    cur = T("give me some more information about that.", ts=30, path=s.PATH_BRAIN, dom="", tsd="", score=0.6)
    assert "REPHRASE" not in {e.signal for e in s.detect_window(hist, cur)}
    monkeypatch.setattr(s, "domains", lambda _t: {"chat-anything"})
    assert "REPHRASE" in {e.signal for e in s.detect_window(hist, cur)}


# ── the runtime: the new notes reach the turn, the history feeds the look-back ──

import time  # noqa: E402

import struggle_detector as sd  # noqa: E402


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.delenv("ZOE_STRUGGLE_DETECT", raising=False)
    sd.reset()
    be = sd.MemoryBackend()
    sd.set_backend(be)
    yield be
    sd.reset()
    sd.set_backend(None)


def _gated(head_top, scores):
    return {"domain": "chat", "score": 0.5, "routed": "chat", "scores": scores,
            "two_stage": {"domain": "chat", "head_top": head_top, "head_conf": 0.65, "gated": True}}


async def test_skybridge_veto_and_scores_reach_the_turn_and_make_the_fallthrough(fresh):
    text = "schedule a call for nine tonight"
    sd.note_route(text, _gated("calendar", {"calendar": 0.8, "chat": 0.7}), two_stage_active=True)
    sd.note_skybridge(text, "calendar", "create_event", "veto")
    sd.note_brain("voice-panel-hall-1", "flue", "ok")
    ev = await sd.process((time.time(), "voice", text, "Sure, done.", "member-a", "voice-panel-hall-1", "", False))
    assert [e.signal for e in ev] == ["FALLTHROUGH"] and "skybridge_vetoed" in ev[0].reasons


async def test_the_worker_keeps_a_short_history_for_the_lookback(fresh):
    now = time.time()
    ev = []
    for i, text in enumerate(("what is the capital of chad", "and of niger?", "what is the capital of chad")):
        ev = await sd.process((now - 30 + i * 10, "chat", text, "An answer.", "member-a", "chat-s1", "", False))
    assert [e.signal for e in ev] == ["RE_ASK"] and "earlier_turn" in ev[0].reasons
    assert len(sd._HISTORY["chat:chat-s1"]) == 3
    await sd.delete_user("member-a")
    assert "chat:chat-s1" not in sd._HISTORY


async def test_resolve_skybridge_request_notes_its_vote(fresh):
    import skybridge_service as sky

    veto = {"domain": "chat", "routed": "chat", "score": 0.5, "scores": {"chat": 0.5},
            "two_stage": {"tool": None, "domain": "chat", "args": {}, "shortlist": ["calendar"], "head_top": "chat",
                          "head_conf": 0.9, "gated": True, "ms": 1.0}}
    result = await sky.resolve_skybridge_request("show my calendar", "guest", router_decision=veto)
    assert result["handled"] is False and result.get("vetoed") is True
    _ts, text, dom, vetoed = sd._SKY[-1]
    assert (text, dom, vetoed) == ("show my calendar", "calendar", True)


_LOG = """\
2026-01-05T21:00:00+0000 INFO     routers.voice_tts: voice/turn_stream panel=hall-panel STT=1.10s transcript="Schedule a call for nine tonight."
2026-01-05T21:00:01+0000 INFO     zoe.router_head_shadow: router_two_stage {"actual_routed": "chat", "failed": false, "gated": true, "head_conf": 0.65, "two_stage_domain": "chat"}
2026-01-05T21:00:01+0000 WARNING  routers.voice_tts: ROUTER_SHADOW text="Schedule a call for nine tonight." -> routed=chat (best=chat 0.69) 40.0ms scores={'reminders': 0.81, 'calendar': 0.8, 'chat': 0.69}
2026-01-05T21:00:01+0000 INFO     routers.voice_tts: voice/command panel=hall-panel session=voice-panel-hall-panel-0a0b0c0d user=guest len=33 [identity: identified=None]
2026-01-05T21:00:01+0000 INFO     skybridge_service: SKYBRIDGE_GATE router=chat conf=0.6517 skybridge=calendar action=create_event decision=veto reason=router_chat
2026-01-05T21:00:01+0000 INFO     fast_tiers: INTENT_GATE lane=voice intent=calendar_create head=calendar@0.6517 head_reason=low_conf decision=veto reason=router_chat
2026-01-05T21:00:10+0000 INFO     brain_dispatch: BRAIN_LANE lane_attempted=flue lane_served=flue outcome=ok reason=- session=voice-panel-hall-panel-0a0b0c0d
"""


def test_the_offline_replay_parses_the_skybridge_vote_and_the_head(tmp_path):
    import importlib.util
    from datetime import datetime
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "struggle_replay_eval.py"
    spec = importlib.util.spec_from_file_location("struggle_replay_eval", path)
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    log = tmp_path / "zoe-data.app.log"
    log.write_text(_LOG, encoding="utf-8")
    turns = ev.parse([log], datetime(2026, 1, 1))
    t = turns[0]
    assert (t.sky_domain, t.sky_vetoed, t.head_top, t.sim_scores["calendar"]) == ("calendar", True, "calendar", 0.8)
    assert [[e.signal for e in evs] for _p, _t, evs in ev.run(turns)] == [["FALLTHROUGH"]]
