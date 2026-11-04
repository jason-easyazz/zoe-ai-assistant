"""'what do you know about me' with a lead-in / 'now' / 'so far' answers the FACTS, not the portrait refusal (QA 2026-11-05).

Also pins the capture-lag honesty line and the 'N of these' grammar in ``provenance_answers.render_summary``.
"""
import asyncio
import sys
import types

import pytest

import intent_router as ir
import memory_capture_stats as mcs
import provenance_answers as pa

pytestmark = pytest.mark.ci_safe

UID = "demo_wdyk_00000001"
NOW = 1_791_500_000.0


@pytest.fixture(autouse=True)
def _clean():
    mcs.reset()
    yield
    mcs.reset()


def _known(n=2, inferred=0):
    items = [pa.Item(id=f"r{i}", text=f"User likes tea number {i}", epoch=NOW - 100, group="preferences",
                     inferred=i < inferred) for i in range(n)]
    return pa.Known(items=items, total=n)


@pytest.mark.parametrize("text", [
    "sorry, got distracted with the kids. what do you know about me now?",
    "what do you know about me",
    "so what do you know about me so far?",
])
def test_plain_know_about_me_routes_to_facts(text):
    i = ir.detect_intent(text)
    assert i is not None and i.name == "portrait_reveal" and i.slots.get("facts") is True


@pytest.mark.parametrize("text", [
    "how would you describe me", "what am I like", "what's your picture of me", "how well do you know me",
    "what do you understand about me",
])
def test_explicit_portrait_asks_stay_portrait(text):
    i = ir.detect_intent(text)
    assert i is not None and i.name == "portrait_reveal" and not i.slots.get("facts")


def test_negative_control_unrelated_text_is_not_portrait():
    i = ir.detect_intent("what do you know about the weather")
    assert i is None or i.name != "portrait_reveal"


async def _coro(v):
    return v


def _run_reveal(monkeypatch, portrait, facts):
    monkeypatch.setitem(sys.modules, "user_portrait", types.SimpleNamespace(load_portrait=lambda uid: _coro(portrait)))

    async def fake_handle(text, uid, *a, **k):
        return facts
    monkeypatch.setattr(pa, "handle", fake_handle)
    return lambda intent: asyncio.run(ir.execute_intent(intent, UID))


def test_facts_ask_returns_summary_even_with_no_portrait(monkeypatch):
    run = _run_reveal(monkeypatch, "", "Here's the short version. FACTS")
    assert run(ir.Intent("portrait_reveal", {"facts": True})) == "Here's the short version. FACTS"


def test_no_portrait_explicit_ask_falls_back_to_facts_not_refusal(monkeypatch):
    run = _run_reveal(monkeypatch, "", "FACTS")
    out = run(ir.Intent("portrait_reveal", {}))
    assert out == "FACTS" and "try asking" not in out


def test_explicit_portrait_ask_with_portrait_gets_portrait(monkeypatch):
    run = _run_reveal(monkeypatch, "You are kind.", "FACTS")
    assert "You are kind." in run(ir.Intent("portrait_reveal", {}))
    # negative control: the facts ask ignores the portrait
    assert run(ir.Intent("portrait_reveal", {"facts": True})) == "FACTS"


@pytest.mark.parametrize("voice", [True, False])
def test_in_flight_line_only_when_in_flight(voice):
    assert "still filing" not in pa.render_summary(_known(), voice=voice, now=NOW, user_id=UID)
    mcs.started(UID)
    out = pa.render_summary(_known(), voice=voice, now=NOW, user_id=UID)
    assert "I'm still filing a few things you just told me." in out
    mcs.completed(UID)
    assert "still filing" not in pa.render_summary(_known(), voice=voice, now=NOW, user_id=UID)
    # another user's in-flight capture does not leak in
    mcs.started("someone_else")
    assert "still filing" not in pa.render_summary(_known(), voice=voice, now=NOW, user_id=UID)


def test_in_flight_line_on_empty_summary_too():
    mcs.started(UID)
    assert "still filing" in pa.render_summary(pa.Known(), voice=True, now=NOW, user_id=UID)


@pytest.mark.parametrize("n,expect", [(1, "1 of these I worked out"), (3, "3 of these I worked out")])
def test_of_these_grammar(n, expect):
    out = pa.render_summary(_known(n=4, inferred=n), voice=False, now=NOW)
    assert expect in out and "theses" not in out
