"""brain_dispatch routes every entry point (chat + voice) to the right brain."""
from __future__ import annotations

import pytest

import brain_dispatch as bd

pytestmark = pytest.mark.ci_safe


def test_use_core_brain_flag(monkeypatch):
    monkeypatch.delenv("ZOE_USE_CORE_BRAIN", raising=False)
    assert bd.use_core_brain() is True  # default ON (cutover)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    assert bd.use_core_brain() is False
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    assert bd.use_core_brain() is True


@pytest.mark.asyncio
async def test_oneshot_routes_to_core_by_default(monkeypatch):
    import zoe_core_client

    async def fake_core(msg, sid, uid="", **kw):
        return f"core:{msg}:{uid}"

    monkeypatch.setattr(zoe_core_client, "run_zoe_core", fake_core)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    assert await bd.brain_oneshot("hi", "s", "jason") == "core:hi:jason"


@pytest.mark.asyncio
async def test_oneshot_routes_to_legacy_when_off(monkeypatch):
    import zoe_agent

    async def fake_legacy(msg, sid, uid="family-admin", **kw):
        return f"legacy:{msg}"

    monkeypatch.setattr(zoe_agent, "run_zoe_agent", fake_legacy)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    assert await bd.brain_oneshot("hi", "s", "jason") == "legacy:hi"


@pytest.mark.asyncio
async def test_streaming_routes_to_core_by_default(monkeypatch):
    import zoe_core_client

    async def fake_stream(msg, sid, uid="", **kw):
        yield "a"
        yield "b"

    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_stream)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    chunks = [c async for c in bd.brain_streaming("hi", "s", "jason")]
    assert chunks == ["a", "b"]


@pytest.mark.asyncio
async def test_streaming_routes_to_legacy_when_off(monkeypatch):
    import zoe_agent

    async def fake_legacy_stream(msg, sid, uid="family-admin", **kw):
        yield f"legacy:{msg}"

    monkeypatch.setattr(zoe_agent, "run_zoe_agent_streaming", fake_legacy_stream)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    chunks = [c async for c in bd.brain_streaming("hi", "s", "jason")]
    assert chunks == ["legacy:hi"]
