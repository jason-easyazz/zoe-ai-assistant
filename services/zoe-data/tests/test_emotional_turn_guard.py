"""emotional_turn_guard - nothing unrelated rides on an emotional turn (ZOE_EMOTIONAL_TURN_GUARD=off|shadow|on, default off).

Pinned: the turn classifier (positives from the QA rounds, negatives incl. task turns and third-party feelings), the pure trim (the
first sentence is never dropped; unrequested weather / contact offer / "by the way" aside after it is; ASKED-for info is kept; a
non-emotional turn is untouched), the stream form equals the pure form for every way of cutting the reply, shadow is byte-identical,
off wraps nothing, and the seam defers the contact offer + the day brief on an emotional turn only in mode on (with negative
controls: off, shadow, a non-emotional turn).
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # fakes only - no DB, no model, no live service

import emotional_turn_guard as g

LONELY = "honestly I don't know anyone here yet and my partner's always at work. it's kinda lonely"
LONELY_REPLY = ("I'm here to listen if you want to talk more about that.\n\n"
                "As for the weather, it's 25 degrees and clear sky in town, with a top of 27 today.")
GUILT = "I feel like a terrible daughter, I can't get there to see her"
GUILT_REPLY = "That guilt is heavy when you can't be there. What would help most right now? Would you like me to add Mum as a contact?"
ASIDE_REPLY = "Moving somewhere new is a lot. Who do you miss most? By the way, your parcel arrives tomorrow."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(g.FLAG, raising=False)


# -- the classifier ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    LONELY, GUILT,
    "my mum is in hospital and I'm scared",
    "I'm so excited, I got the job!",
    "feeling a bit down today",
    "rough day, the dog barked all night",
    "my grandad passed away last week",
])
def test_emotional_turns(message):
    assert g.is_emotional_turn(message)


@pytest.mark.parametrize("message", [
    "", "what is the weather tomorrow", "set a timer for 12 minutes", "add milk to the shopping list",
    "I'm a nurse and my husband is a teacher",          # a self-disclosure, not a feeling (the doctrine owns it, not this guard)
    "how do I make baked pasta?",
])
def test_not_emotional_turns(message):
    assert not g.is_emotional_turn(message)


# -- the pure trim -----------------------------------------------------------------------------------------

def test_lonely_turn_loses_the_stale_weather():
    assert g.trim(LONELY_REPLY, LONELY) == "I'm here to listen if you want to talk more about that."
    assert g.dropped_kinds(LONELY_REPLY, LONELY) == ["weather"]


def test_contact_offer_and_aside_dropped_after_the_first_sentence():
    assert g.trim(GUILT_REPLY, GUILT) == "That guilt is heavy when you can't be there. What would help most right now?"
    assert g.trim(ASIDE_REPLY, LONELY) == "Moving somewhere new is a lot. Who do you miss most?"
    assert g.dropped_kinds(ASIDE_REPLY, LONELY) == ["aside"]


def test_first_sentence_is_never_dropped():
    reply = "As for the weather, it's 25 degrees. I'm sorry it's been so lonely."
    assert g.trim(reply, LONELY) == reply


def test_asked_for_info_is_kept():
    msg = "I'm so stressed, will it rain for the wedding tomorrow?"
    reply = "Wedding nerves are real. Tomorrow looks dry with a top of 24 degrees. Want a wet-weather plan anyway?"
    assert g.is_emotional_turn(msg)
    assert g.trim(reply, msg) == reply


def test_non_emotional_turn_is_untouched():
    msg = "what's on tomorrow?"
    reply = "Nothing on the calendar. By the way, it'll be sunny with a top of 27 degrees."
    assert g.trim(reply, msg) == reply


def test_negative_control_unrelated_sentences_on_an_emotional_turn_are_kept():
    reply = "That sounds really isolating. What did you used to do on weekends back home? Clubs can be a good start."
    assert g.trim(reply, LONELY) == reply
    assert g.dropped_kinds(reply, LONELY) == []


# -- the stream form ---------------------------------------------------------------------------------------

def _cuts(text: str):
    yield [text]
    yield list(text)
    for step in (2, 3, 5, 7, 11):
        yield [text[i:i + step] for i in range(0, len(text), step)]


@pytest.mark.parametrize("reply, message", [
    (LONELY_REPLY, LONELY), (GUILT_REPLY, GUILT), (ASIDE_REPLY, LONELY),
    ("Oh no. That's hard.", LONELY), ("One sentence only", LONELY), ("", LONELY),
    ("Hi.\n\nBy the way, it's sunny. And you?", LONELY),
])
def test_stream_equals_trim_for_every_cut(reply, message):
    want = g.trim(reply, message)
    lex = g.lexicon("en")
    for pieces in _cuts(reply):
        s = g.GuardStream(lex, g.asked_kinds(message, lex))
        got = "".join(s.feed(p) for p in pieces) + s.finish()
        assert got == want, pieces


def test_first_sentence_streams_without_waiting():
    lex = g.lexicon("en")
    s = g.GuardStream(lex, frozenset())
    assert s.feed("I'm here to") == "I'm here to"      # released at once: the first sound is not delayed
    assert s.feed(" listen.") == " listen."
    assert s.feed(" As for the weather,") == ""        # a later sentence is held until it ends


async def _run(deltas, message):
    async def inner():
        for d in deltas:
            yield d
    return [x async for x in g.filter_stream(inner(), message, "demo-a")]


def test_filter_stream_modes(monkeypatch, caplog):
    deltas = ["I'm here to listen.", "__TOOL__:recall_memory", "\n\nAs for the weather, ", "it's 25 degrees."]
    # off (default): nothing wraps, byte-identical
    assert asyncio.run(_run(deltas, LONELY)) == deltas
    # shadow: byte-identical, one label-only line
    monkeypatch.setenv(g.FLAG, "shadow")
    with caplog.at_level(logging.INFO, logger="emotional_turn_guard"):
        assert asyncio.run(_run(deltas, LONELY)) == deltas
    lines = [r.getMessage() for r in caplog.records if "EMOTIONAL_TURN_GUARD" in r.getMessage()]
    assert lines and "dropped=1 kinds=weather" in lines[-1] and "25" not in lines[-1]
    # on: the weather sentence is gone, the sentinel still passes
    monkeypatch.setenv(g.FLAG, "on")
    out = asyncio.run(_run(deltas, LONELY))
    assert "".join(o for o in out if not o.startswith("__TOOL__:")) == "I'm here to listen."
    assert "__TOOL__:recall_memory" in out
    # on, but a non-emotional turn: untouched
    assert asyncio.run(_run(deltas, "what's the weather?")) == deltas


def test_mode_parsing(monkeypatch):
    for raw, want in (("", "off"), ("off", "off"), ("shadow", "shadow"), ("on", "on"), ("1", "on"), ("bogus", "off")):
        monkeypatch.setenv(g.FLAG, raw)
        assert g.mode() == want


def test_holds_extras_only_in_mode_on(monkeypatch, caplog):
    assert not g.holds_extras(LONELY, "demo-a")                      # off
    monkeypatch.setenv(g.FLAG, "shadow")
    with caplog.at_level(logging.INFO, logger="emotional_turn_guard"):
        assert not g.holds_extras(LONELY, "demo-a")                  # shadow: logs, holds nothing
        assert not g.holds_extras(LONELY, "demo-a", log=False)
    assert sum("surface=extras" in r.getMessage() for r in caplog.records) == 1
    monkeypatch.setenv(g.FLAG, "on")
    assert g.holds_extras(LONELY, "demo-a")
    assert not g.holds_extras("add milk to the list", "demo-a")      # negative control


# -- the seam ----------------------------------------------------------------------------------------------

import zoe_flue_client as zc  # noqa: E402


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeClient:
    captured: dict = {}
    reply = "ok"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _FakeResponse({"result": {"text": type(self).reply}})


def _seam(monkeypatch, *, mode="on", reply="ok"):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")   # isolate this guard from the continuity deferral
    monkeypatch.delenv("ZOE_SEAM_RECALL_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_OFFER_INJECT", "1")
    monkeypatch.setenv(g.FLAG, mode)
    monkeypatch.setattr(_FakeClient, "captured", {})
    monkeypatch.setattr(_FakeClient, "reply", reply)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    calls = {"offer": 0, "brief": 0}

    async def fake_offer(uid):
        calls["offer"] += 1
        return ('[PENDING CONTACT OFFER — do not mention this block]\n- After answering, ask the user exactly: '
                '"Would you like me to add Alex (your partner) as a contact?"\n[END PENDING CONTACT OFFER]')

    import brief_first_turn

    async def fake_brief(message, uid, sid=""):
        calls["brief"] += 1
        return None

    monkeypatch.setattr(zc, "_pending_offer_block", fake_offer)
    monkeypatch.setattr(brief_first_turn, "prepare", fake_brief)
    return calls


async def _turn(message, user_id="demo-a"):
    out = [c async for c in zc.run_flue_brain_streaming(message, "s1", user_id)]
    return out, json.loads(_FakeClient.captured["content"])["message"]


def test_seam_defers_offer_and_brief_on_an_emotional_turn(monkeypatch, caplog):
    calls = _seam(monkeypatch)
    with caplog.at_level(logging.INFO, logger="zoe_flue_client"):
        _, wire = asyncio.run(_turn(LONELY))
    assert "Alex" not in wire and calls["offer"] == 0 and calls["brief"] == 0
    assert any("SEAM_OFFER user=demo-a deferred=1 reason=emotional" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("mode, message", [("off", LONELY), ("shadow", LONELY), ("on", "can you add milk to the list?")])
def test_seam_negative_controls_still_offer(monkeypatch, mode, message):
    calls = _seam(monkeypatch, mode=mode)
    _, wire = asyncio.run(_turn(message))
    assert "Alex" in wire and calls["offer"] == 1 and calls["brief"] == 1


def test_seam_drops_the_stale_weather_from_the_reply(monkeypatch):
    _seam(monkeypatch, reply=LONELY_REPLY)
    out, _ = asyncio.run(_turn(LONELY))
    assert "".join(out) == "I'm here to listen if you want to talk more about that."
    _seam(monkeypatch, mode="off", reply=LONELY_REPLY)
    out, _ = asyncio.run(_turn(LONELY))
    assert "".join(out) == LONELY_REPLY                       # off: byte-identical


def test_offer_ager_skips_an_emotional_turn_only_in_mode_on(monkeypatch):
    """latent_intent_detector reads the same rule (log=False): pinned at the source so a refactor cannot drop it."""
    import inspect

    import latent_intent_detector

    src = inspect.getsource(latent_intent_detector)
    assert "emotional_turn_guard.holds_extras(user_message, user_id, log=False)" in src
