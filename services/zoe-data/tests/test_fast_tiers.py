"""Unit tests for the channel-agnostic deterministic core (fast_tiers.resolve).

Pure logic — expert_dispatch / semantic_router / intent_router are monkeypatched,
so no DB, no embeddings, no network. Mirrors tests/test_fast_path.py.
"""
import asyncio
import types

import pytest

pytestmark = pytest.mark.ci_safe

fast_tiers = pytest.importorskip("fast_tiers")
fast_path = pytest.importorskip("fast_path")
expert_dispatch = pytest.importorskip("expert_dispatch")
semantic_router = pytest.importorskip("semantic_router")
intent_router = pytest.importorskip("intent_router")


def _run(coro):
    return asyncio.run(coro)


def _enable(monkeypatch):
    """Default happy-path stubs: dispatch enabled, router enabled."""
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)


def _fake_intent(name, slots=None):
    """A lightweight stand-in for intent_router.Intent (name + slots)."""
    return types.SimpleNamespace(name=name, slots=dict(slots or {}))


def _stub_detect(monkeypatch, intent):
    """Make detect_intent deterministically return `intent` (or None)."""
    monkeypatch.setattr(intent_router, "detect_intent",
                        lambda text, log_miss=False: intent)


def _detect_must_not_run(monkeypatch):
    """Wire detect_intent to blow up so we can assert Tier-0 was skipped."""
    def _boom(*a, **k):
        raise AssertionError("detect_intent should not be called")
    monkeypatch.setattr(intent_router, "detect_intent", _boom)


# --------------------------------------------------------------------------- #
# 1 + 3 + threading: profiles & explicit overrides reach dispatch.            #
# --------------------------------------------------------------------------- #
def test_chat_profile_applies_write_ok_false(monkeypatch):
    _enable(monkeypatch)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["write_ok"] = write_ok
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # chat profile = {run_tier0: True, allow_writes: False}. detect_intent misses
    # (None) so Tier-0 falls through to the router/dispatch.
    _stub_detect(monkeypatch, None)
    _run(fast_tiers.resolve("add milk", "u", "s", channel="chat",
                            router_decision={"domain": "lists", "score": 0.95}))
    assert seen["write_ok"] is False


def test_voice_profile_write_ok_true_and_tier0_runs_public_read(monkeypatch):
    _enable(monkeypatch)
    # voice profile now = {run_tier0: True, allow_writes: True}. A public read
    # intent (weather) short-circuits at Tier-0 with a finished answer; the
    # router/dispatch path is never reached.
    async def _dispatch_must_not_run(domain, text, ctx, *, write_ok=True):
        raise AssertionError("dispatch should not run on a Tier-0 hit")

    monkeypatch.setattr(expert_dispatch, "dispatch", _dispatch_must_not_run)
    _stub_detect(monkeypatch, _fake_intent("weather"))

    async def _fake_exec(intent, user_id):
        return "It's sunny, 22 degrees."

    monkeypatch.setattr(intent_router, "execute_intent", _fake_exec)
    out = _run(fast_tiers.resolve("what's the weather", "u", "s", channel="voice",
                                  router_decision={"domain": "weather", "score": 0.95}))
    assert out is not None
    assert out.tier == "tier0"
    assert out.intent == "weather"


def test_voice_defers_user_scoped_reads_to_brain(monkeypatch):
    # User-scoped reads (reminder_list, timer_status) must NOT short-circuit at
    # Tier-0 on voice — the voice B3/B4 scope gate runs after fast_tiers, so a
    # Tier-0 answer would bypass it. They fall through (here: to the router, which
    # we stub to a chat domain → None → brain lane).
    _enable(monkeypatch)

    async def _execute_must_not_run(intent, user_id):
        raise AssertionError("execute_intent must not run for a deferred read")

    monkeypatch.setattr(intent_router, "execute_intent", _execute_must_not_run)
    for intent_name in ("reminder_list", "timer_status"):
        _stub_detect(monkeypatch, _fake_intent(intent_name))
        out = _run(fast_tiers.resolve("what reminders do I have", "u", "s",
                                      channel="voice",
                                      router_decision={"domain": "chat", "score": 0.2}))
        assert out is None, f"voice should defer user-scoped read {intent_name}"


def test_voice_defers_people_and_memory_to_brain(monkeypatch):
    # On voice, people/memory must NOT be fast-pathed (slow + mis-stored questions
    # as facts on-device) — resolve() returns None so the turn falls to the brain.
    _enable(monkeypatch)

    async def _dispatch_must_not_run(domain, text, ctx, *, write_ok=True):
        raise AssertionError(f"expert_dispatch ran for deferred domain {domain!r}")

    monkeypatch.setattr(expert_dispatch, "dispatch", _dispatch_must_not_run)
    # Tier-0 runs on voice now, but detect_intent misses on this conversational
    # recall phrasing so it falls through to the (deferred) domain check.
    _stub_detect(monkeypatch, None)
    for domain in ("people", "memory"):
        out = _run(fast_tiers.resolve("do you remember my mum's name", "u", "s",
                                      channel="voice",
                                      router_decision={"domain": domain, "score": 0.95}))
        assert out is None, f"voice should defer {domain} to the brain"


def test_other_channels_still_fast_path_people(monkeypatch):
    # The defer list is voice-scoped: telegram still fast-paths people.
    _enable(monkeypatch)

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        return expert_dispatch.DispatchResult(domain=domain, reply="recalled")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # telegram runs Tier-0, so stub detect_intent to miss deterministically (don't
    # rely on the live implementation not matching) → falls through to dispatch.
    _stub_detect(monkeypatch, None)
    out = _run(fast_tiers.resolve("what is my dad's name", "u", "s", channel="telegram",
                                  router_decision={"domain": "people", "score": 0.95}))
    assert out is not None and out.domain == "people"


def test_explicit_kwargs_override_profile(monkeypatch):
    _enable(monkeypatch)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["write_ok"] = write_ok
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # chat profile would give allow_writes=False + run_tier0=True. Explicit kwargs
    # flip both: allow_writes=True reaches dispatch, run_tier0=False skips Tier-0.
    _detect_must_not_run(monkeypatch)
    _run(fast_tiers.resolve("add milk", "u", "s", channel="chat",
                            allow_writes=True, run_tier0=False,
                            router_decision={"domain": "lists", "score": 0.95}))
    assert seen["write_ok"] is True


def test_run_tier0_override_enables_tier0_on_voice(monkeypatch):
    # voice defaults run_tier0=False; explicit run_tier0=True must enable Tier-0.
    _enable(monkeypatch)
    _stub_detect(monkeypatch, _fake_intent("time_query"))

    async def _fake_exec(intent, user_id):
        return "It's 5 PM"

    monkeypatch.setattr(intent_router, "execute_intent", _fake_exec)
    out = _run(fast_tiers.resolve("what time is it", "u", "s", channel="voice",
                                  run_tier0=True))
    assert out is not None and out.tier == "tier0" and out.reply == "It's 5 PM"


# --------------------------------------------------------------------------- #
# 4: Tier-0 hit short-circuits before the semantic router.                    #
# --------------------------------------------------------------------------- #
def test_tier0_hit_short_circuits_router(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    _stub_detect(monkeypatch, _fake_intent("time_query"))

    async def _fake_exec(intent, user_id):
        return "It's 5 PM"

    monkeypatch.setattr(intent_router, "execute_intent", _fake_exec)

    # Router must never be consulted on a Tier-0 hit.
    def _route_boom(text):
        raise AssertionError("semantic_router.route should not be called")

    monkeypatch.setattr(semantic_router, "route", _route_boom)
    monkeypatch.setattr(semantic_router, "is_enabled",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("is_enabled should not be called")))

    out = _run(fast_tiers.resolve("what time is it", "u", "s", run_tier0=True))
    assert out is not None
    assert out.tier == "tier0"
    assert out.reply == "It's 5 PM"
    assert out.intent == "time_query"
    assert out.domain == "time"  # _TIER0_DOMAIN maps time_query → time


# --------------------------------------------------------------------------- #
# 5: Tier-0 miss (non-whitelisted intent) falls through to router/dispatch.   #
# --------------------------------------------------------------------------- #
def test_tier0_miss_falls_through(monkeypatch):
    _enable(monkeypatch)
    # A write/non-whitelisted intent → Tier-0 returns None, router/dispatch runs.
    _stub_detect(monkeypatch, _fake_intent("calendar_create"))
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return expert_dispatch.DispatchResult(domain=domain, reply="DISPATCHED")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("book a dentist appt", "u", "s", run_tier0=True,
                                  router_decision={"domain": "calendar", "score": 0.95}))
    assert out is not None and out.reply == "DISPATCHED"
    assert seen["domain"] == "calendar"


def test_tier0_none_intent_falls_through(monkeypatch):
    # detect_intent returns None → Tier-0 misses, router/dispatch runs.
    _enable(monkeypatch)
    _stub_detect(monkeypatch, None)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["n"] = seen.get("n", 0) + 1
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("ramble on about nothing", "u", "s", run_tier0=True,
                                  router_decision={"domain": "weather", "score": 0.95}))
    assert out is not None and seen["n"] == 1


# --------------------------------------------------------------------------- #
# 6: Tier-0 with a "raw" slot is deferred (falls through).                    #
# --------------------------------------------------------------------------- #
def test_tier0_raw_slot_deferred(monkeypatch):
    _enable(monkeypatch)
    # A whitelisted read intent BUT with a "raw" slot → still needs extraction,
    # so Tier-0 defers and we fall through to the router/dispatch path.
    _stub_detect(monkeypatch, _fake_intent("list_show", {"raw": "the shopping list"}))

    def _exec_boom(*a, **k):
        raise AssertionError("execute_intent must not run for a raw-slot intent")

    monkeypatch.setattr(intent_router, "execute_intent", _exec_boom)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("show the shopping list", "u", "s", run_tier0=True,
                                  router_decision={"domain": "lists", "score": 0.95}))
    assert out is not None and out.reply == "OK"
    assert seen["domain"] == "lists"


def test_tier0_empty_reply_falls_through(monkeypatch):
    # Whitelisted read intent but execute_intent returns blank → defer, fall through.
    _enable(monkeypatch)
    _stub_detect(monkeypatch, _fake_intent("time_query"))

    async def _fake_exec(intent, user_id):
        return "   "

    monkeypatch.setattr(intent_router, "execute_intent", _fake_exec)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["n"] = seen.get("n", 0) + 1
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("what time is it", "u", "s", run_tier0=True,
                                  router_decision={"domain": "time", "score": 0.95}))
    assert out is not None and seen["n"] == 1


# --------------------------------------------------------------------------- #
# 7: Ambiguity margin check.                                                  #
# --------------------------------------------------------------------------- #
def test_margin_ambiguous_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_MARGIN", "0.05")

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        raise AssertionError("dispatch must not run when ambiguous")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # top1 - top2 = 0.02 < 0.05 → ambiguous → None (→ brain).
    out = _run(fast_tiers.resolve("hmm", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.9,
                                                   "scores": {"weather": 0.90, "lists": 0.88}}))
    assert out is None


def test_margin_clear_dispatches(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_MARGIN", "0.05")
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # top1 - top2 = 0.40 >= 0.05 → confident → dispatch runs.
    out = _run(fast_tiers.resolve("what's the weather", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.9,
                                                   "scores": {"weather": 0.90, "lists": 0.50}}))
    assert out is not None and seen["domain"] == "weather"


def test_margin_zero_disables_check(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_MARGIN", "0")
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # Margin 0 disables the check: even near-tied scores dispatch.
    out = _run(fast_tiers.resolve("hmm", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.9,
                                                   "scores": {"weather": 0.90, "lists": 0.895}}))
    assert out is not None and seen["domain"] == "weather"


# --------------------------------------------------------------------------- #
# 8: result.tier defaults to "tier1.5" when dispatch leaves it unset.         #
# --------------------------------------------------------------------------- #
def test_tier_defaults_to_tier1_5(monkeypatch):
    _enable(monkeypatch)

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")  # no tier

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("what's the weather", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.95}))
    assert out is not None and out.tier == "tier1.5"


def test_existing_tier_preserved(monkeypatch):
    # If dispatch already set a tier, resolve() must not overwrite it.
    _enable(monkeypatch)

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        return expert_dispatch.DispatchResult(domain=domain, reply="OK", tier="tier0")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("what's the weather", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.95}))
    assert out is not None and out.tier == "tier0"


# --------------------------------------------------------------------------- #
# 9: domain "chat" or None from the router → None.                            #
# --------------------------------------------------------------------------- #
def test_chat_domain_returns_none(monkeypatch):
    _enable(monkeypatch)
    called = {"n": 0}

    async def _fake_dispatch(*a, **k):
        called["n"] += 1
        return expert_dispatch.DispatchResult(domain="chat", reply="X")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("hello", "u", "s",
                                  router_decision={"domain": "chat", "score": 0.9}))
    assert out is None and called["n"] == 0


def test_none_domain_returns_none(monkeypatch):
    _enable(monkeypatch)
    called = {"n": 0}

    async def _fake_dispatch(*a, **k):
        called["n"] += 1
        return expert_dispatch.DispatchResult(domain="x", reply="X")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("hello", "u", "s",
                                  router_decision={"score": 0.9}))  # no domain key
    assert out is None and called["n"] == 0


# --------------------------------------------------------------------------- #
# 10: enable flags.                                                           #
# --------------------------------------------------------------------------- #
def test_none_when_dispatch_disabled(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: False)
    out = _run(fast_tiers.resolve("hi", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.9}))
    assert out is None


def test_none_when_semantic_router_disabled(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)
    called = {"routed": 0}

    def _route(text):
        called["routed"] += 1
        return {"domain": "weather", "score": 0.9}

    monkeypatch.setattr(semantic_router, "route", _route)
    # No router_decision → resolve consults is_enabled() (False) → None,
    # and route() is never called.
    out = _run(fast_tiers.resolve("hi", "u", "s"))
    assert out is None and called["routed"] == 0


def test_routes_via_semantic_router_when_no_decision(monkeypatch):
    # router_decision=None → resolve() consults semantic_router.route().
    _enable(monkeypatch)
    monkeypatch.setattr(semantic_router, "route",
                        lambda text: {"domain": "weather", "score": 0.9})
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("what's the weather", "u", "s"))
    assert out is not None and seen["domain"] == "weather"


# --------------------------------------------------------------------------- #
# 11: errors are swallowed → None (a turn is never broken).                   #
# --------------------------------------------------------------------------- #
def test_swallows_dispatch_errors(monkeypatch):
    _enable(monkeypatch)

    async def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(expert_dispatch, "dispatch", _boom)
    out = _run(fast_tiers.resolve("x", "u", "s",
                                  router_decision={"domain": "weather", "score": 0.9}))
    assert out is None


def test_swallows_router_errors(monkeypatch):
    _enable(monkeypatch)

    def _route_boom(text):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(semantic_router, "route", _route_boom)
    out = _run(fast_tiers.resolve("x", "u", "s"))
    assert out is None


def test_tier0_error_swallowed_falls_through(monkeypatch):
    # A Tier-0 internal error must not break the turn — it falls through to the
    # router/dispatch lane (detect_intent raising is caught inside _tier0).
    _enable(monkeypatch)

    def _detect_boom(*a, **k):
        raise RuntimeError("intent exploded")

    monkeypatch.setattr(intent_router, "detect_intent", _detect_boom)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_tiers.resolve("what's the weather", "u", "s", run_tier0=True,
                                  router_decision={"domain": "weather", "score": 0.95}))
    assert out is not None and seen["domain"] == "weather"


# --------------------------------------------------------------------------- #
# 12: back-compat shim.                                                       #
# --------------------------------------------------------------------------- #
def test_fast_path_resolve_is_fast_tiers_resolve():
    assert fast_path.resolve is fast_tiers.resolve


def test_turn_outcome_alias_is_dispatch_result():
    assert fast_tiers.TurnOutcome is expert_dispatch.DispatchResult


# --------------------------------------------------------------------------- #
# Extra: profile_for helper.                                                  #
# --------------------------------------------------------------------------- #
def test_profile_for_returns_copy():
    p = fast_tiers.profile_for("chat")
    assert p == {"run_tier0": True, "allow_writes": False}
    p["allow_writes"] = True
    assert fast_tiers.CHANNEL_PROFILES["chat"]["allow_writes"] is False  # copy, not ref


def test_profile_for_unknown_channel_empty():
    assert fast_tiers.profile_for("nope") == {}
    assert fast_tiers.profile_for(None) == {}
