"""_voice_domain_context gives the brain calendar/lists/reminders data on deferral.

The brain used to say "I don't have access to your calendar" when a calendar
fragment fell through to it. This injects that domain's data — but ONLY for
calendar/lists/reminders, so the common chat/people brain turns stay fast.
"""
import asyncio

import pytest

import routers.voice_tts as v

pytestmark = pytest.mark.ci_safe


def _run(c):
    return asyncio.run(c)


@pytest.mark.parametrize("domain", [None, "chat", "people", "memory", "weather"])
def test_no_context_for_non_domain_turns(domain):
    # Common brain turns must pay nothing (no DB read, no latency).
    out = _run(v._voice_domain_context({"domain": domain}, "jason"))
    assert out is None


def test_none_router_decision():
    # Test the actual call pattern when semantic router is disabled
    out = _run(v._voice_domain_context(None, "jason"))
    assert out is None


def test_calendar_context_injected(monkeypatch):
    import intent_router as ir
    seen = {}

    async def _fake_execute(intent, user_id):
        seen["intent"] = intent.name
        seen["qualifier"] = intent.slots.get("qualifier")
        return "Dentist on Tuesday at 9am."

    monkeypatch.setattr(ir, "execute_intent", _fake_execute)
    out = _run(v._voice_domain_context({"domain": "calendar"}, "jason"))
    assert seen["intent"] == "calendar_show" and seen["qualifier"] == "this week"
    assert out == "[Your calendar this week]\nDentist on Tuesday at 9am."


def test_empty_summary_is_none(monkeypatch):
    import intent_router as ir

    async def _empty(intent, user_id):
        return ""

    monkeypatch.setattr(ir, "execute_intent", _empty)
    assert _run(v._voice_domain_context({"domain": "lists"}, "jason")) is None


def test_merge_brain_context():
    assert v._merge_brain_context(None, None) is None
    assert v._merge_brain_context("facts", None) == "facts"
    assert v._merge_brain_context(None, "[cal]\nx") == "[cal]\nx"
    assert v._merge_brain_context("facts", "[cal]\nx") == "facts\n\n[cal]\nx"
