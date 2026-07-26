"""Unit tests for the channel-agnostic fast-path core (fast_path.resolve).

Pure logic — expert_dispatch/semantic_router are monkeypatched, so no DB/network.
"""
import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

fast_path = pytest.importorskip("fast_path")
expert_dispatch = pytest.importorskip("expert_dispatch")
semantic_router = pytest.importorskip("semantic_router")


def _run(coro):
    return asyncio.run(coro)


def test_none_when_dispatch_disabled(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: False)
    assert _run(fast_path.resolve("hi", "u", "s")) is None


def test_none_for_chat_domain_without_dispatch(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    called = {"n": 0}

    async def _should_not_call(*a, **k):
        called["n"] += 1
        return "X"

    monkeypatch.setattr(expert_dispatch, "dispatch", _should_not_call)
    out = _run(fast_path.resolve("hello", "u", "s",
                                 router_decision={"domain": "chat", "score": 0.9}))
    assert out is None
    assert called["n"] == 0  # chat domain short-circuits, never dispatches


def test_dispatches_for_real_domain_with_correct_ctx(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        seen["text"] = text
        seen["ctx"] = ctx
        return "RESULT"

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_path.resolve(
        "what's the weather", "jason", "sess",
        router_decision={"domain": "weather", "score": 0.95},
        extra_ctx={"db": None, "panel_id": "p1"},
    ))
    assert out == "RESULT"
    assert seen["domain"] == "weather"
    assert seen["text"] == "what's the weather"
    # ctx carries user/session/score plus the merged extra_ctx (byte-identical shape)
    assert seen["ctx"]["user_id"] == "jason"
    assert seen["ctx"]["session_id"] == "sess"
    assert seen["ctx"]["score"] == 0.95
    assert seen["ctx"]["db"] is None and seen["ctx"]["panel_id"] == "p1"


def test_routes_via_semantic_router_when_no_decision(monkeypatch):
    # router_decision=None → resolve() must consult semantic_router.route().
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "route", lambda text: {"domain": "weather", "score": 0.9})
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["domain"] = domain
        return "OK"

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    out = _run(fast_path.resolve("what's the weather", "u", "s"))  # no router_decision
    assert out == "OK" and seen["domain"] == "weather"


def test_none_when_semantic_router_disabled(monkeypatch):
    # Router off → don't embed/route, fall to the brain (return None).
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)
    called = {"routed": 0}
    monkeypatch.setattr(semantic_router, "route",
                        lambda text: called.__setitem__("routed", called["routed"] + 1) or {"domain": "weather"})
    assert _run(fast_path.resolve("hi", "u", "s")) is None
    assert called["routed"] == 0  # never routed when the router is disabled


def test_extra_ctx_cannot_overwrite_base_fields(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["ctx"] = ctx
        return "OK"

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    # A caller wrongly passes user_id/score in extra_ctx — base must still win.
    _run(fast_path.resolve(
        "x", "jason", "sess",
        router_decision={"domain": "weather", "score": 0.95},
        extra_ctx={"user_id": "ATTACKER", "score": 0.0, "db": None},
    ))
    assert seen["ctx"]["user_id"] == "jason"
    assert seen["ctx"]["score"] == 0.95
    assert seen["ctx"]["db"] is None  # genuinely-extra keys still pass through


def test_allow_writes_flag_threads_to_dispatch(monkeypatch):
    # Chat passes allow_writes=False so writes defer to the brain; voice keeps the
    # default True. Either way the flag must reach expert_dispatch.dispatch().
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["write_ok"] = write_ok
        return "OK"

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    _run(fast_path.resolve("add milk", "u", "s",
                           router_decision={"domain": "lists", "score": 0.95},
                           allow_writes=False))
    assert seen["write_ok"] is False
    _run(fast_path.resolve("add milk", "u", "s",
                           router_decision={"domain": "lists", "score": 0.95}))
    assert seen["write_ok"] is True  # default keeps writes on (voice)


def test_swallows_dispatch_errors(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)

    async def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(expert_dispatch, "dispatch", _boom)
    # A failing fast path must never break the turn — returns None (→ brain).
    out = _run(fast_path.resolve("x", "u", "s",
                                 router_decision={"domain": "weather", "score": 0.9}))
    assert out is None
