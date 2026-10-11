"""fast_tiers.resolve(phase=...): the conversation half and the domain half run once each, in the documented order.

The voice lane runs ``phase="conversation"`` at the top of the turn (before its cards and keyword intents, as chat does) and
``phase="domain"`` where its Tier-1.5 call always was. The split must not run a tier twice (the provenance tier NUMBERS the turn)
and must not skip one.

Break-the-fix: make the wrapper ignore ``phase`` -> the "domain" test sees the provenance tier run again and goes red.
"""
import pytest

pytestmark = pytest.mark.ci_safe

import fast_tiers


@pytest.fixture
def calls(monkeypatch):
    log = []

    def spy(name, result=None):
        async def fn(*a, **k):
            log.append(name)
            return result

        return fn

    monkeypatch.setattr(fast_tiers, "_distress_tier", spy("distress"))
    monkeypatch.setattr(fast_tiers, "_provenance_tier", spy("provenance"))
    monkeypatch.setattr(fast_tiers, "_feedback_tier", spy("feedback"))
    monkeypatch.setattr(fast_tiers, "_conversation_quality_tier", spy("correction"))
    monkeypatch.setattr(fast_tiers, "_pull_tier", spy("pull"))
    monkeypatch.setattr(fast_tiers, "_person_half_tier", spy("person_half"))
    monkeypatch.setattr(fast_tiers, "_identity_tier", spy("identity"))
    monkeypatch.setattr(fast_tiers, "_ask_to_remember_tier", spy("remember"))
    monkeypatch.setattr(fast_tiers, "_tier0", spy("tier0"))

    async def no_hop(*a, **k):
        return False

    monkeypatch.setattr(fast_tiers, "_hop_owns_turn", no_hop)
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "1")
    import self_model

    async def no_self(*a, **k):
        log.append("self_model")
        return None

    monkeypatch.setattr(self_model, "tier", no_self)
    return log


def _router(domain="weather"):
    return {"domain": domain, "score": 0.9, "scores": {domain: 0.9}, "two_stage": True}


async def test_full_run_is_conversation_then_domain(calls):
    await fast_tiers.resolve("what is the weather", "demo_user", "s", channel="chat", router_decision=_router("chat"))
    assert calls == ["distress", "feedback", "provenance", "self_model", "correction", "pull", "person_half", "identity", "remember",
                     "tier0"]


async def test_conversation_phase_stops_before_the_domain_tiers(calls):
    res = await fast_tiers.resolve("what is the weather", "demo_user", "s", channel="voice", router_decision=_router(),
                                   phase="conversation")
    assert res is None
    assert "tier0" not in calls
    assert calls[:4] == ["distress", "feedback", "provenance", "self_model"] and "remember" in calls


async def test_domain_phase_does_not_repeat_the_conversation_tiers(calls):
    await fast_tiers.resolve("what is the weather", "demo_user", "s", channel="voice", router_decision=_router("chat"), phase="domain")
    assert calls == ["tier0"]
    for conv in ("distress", "provenance", "feedback", "self_model", "correction", "pull", "person_half", "remember"):
        assert conv not in calls


async def test_the_two_halves_together_equal_the_full_run(calls):
    await fast_tiers.resolve("hello", "demo_user", "s", channel="voice", router_decision=_router("chat"), phase="conversation")
    first = list(calls)
    calls.clear()
    await fast_tiers.resolve("hello", "demo_user", "s", channel="voice", router_decision=_router("chat"), phase="domain")
    split = first + list(calls)
    calls.clear()
    await fast_tiers.resolve("hello", "demo_user", "s", channel="voice", router_decision=_router("chat"))
    assert split == list(calls)


async def test_a_conversation_tier_answer_ends_the_conversation_phase(calls, monkeypatch):
    import expert_dispatch as xd

    async def answers(*a, **k):
        calls.append("correction")
        return xd.DispatchResult(domain="memory", reply="Got it.", intent="correction_swap", tier="correction")

    monkeypatch.setattr(fast_tiers, "_conversation_quality_tier", answers)
    res = await fast_tiers.resolve("x", "demo_user", "s", channel="voice", phase="conversation")
    assert res is not None and res.reply == "Got it." and "pull" not in calls
