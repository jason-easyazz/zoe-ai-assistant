"""Wave A — brain UI-tool results emit the data-filled card (zoe.ui_component)."""
import json

import pytest

from routers.chat import brain_tool_card_events

pytestmark = pytest.mark.ci_safe


def _tool(phase, name="", tid="t1", **extra):
    return "__TOOL__:" + json.dumps({"phase": phase, "id": tid, "name": name, **extra})


async def _collect(sentinel, **kw):
    out = []
    async for ev in brain_tool_card_events(sentinel, **kw):
        out.append(ev)
    return out


def _fake_resolve(cards, handled=True):
    async def _r(query, user_id, **kw):
        return {"handled": handled, "cards": cards}
    return _r


@pytest.mark.asyncio
async def test_calendar_result_emits_card(monkeypatch):
    import skybridge_service
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request",
                        _fake_resolve([{"card_id": "c1", "content": {"source": "calendar_show"}}]))
    evs = await _collect(_tool("result", "calendar"), user_id="u1", tool_names={}, emitted_domains=set())
    assert len(evs) == 1
    assert evs[0].name == "zoe.ui_component"
    assert evs[0].value["type"] == "calendar"
    assert evs[0].value["card"]["card_id"] == "c1"


@pytest.mark.asyncio
async def test_name_resolved_from_tool_names_map(monkeypatch):
    import skybridge_service
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", _fake_resolve([{"x": 1}]))
    # result sentinel omits the name → resolved via tool_names[id]
    evs = await _collect(_tool("result", "", tid="t9"), user_id="u1", tool_names={"t9": "lists"}, emitted_domains=set())
    assert len(evs) == 1 and evs[0].value["type"] == "list"


@pytest.mark.asyncio
async def test_dedup_same_domain_twice(monkeypatch):
    import skybridge_service
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", _fake_resolve([{"x": 1}]))
    emitted = set()
    first = await _collect(_tool("result", "calendar"), user_id="u1", tool_names={}, emitted_domains=emitted)
    second = await _collect(_tool("result", "calendar"), user_id="u1", tool_names={}, emitted_domains=emitted)
    assert len(first) == 1 and len(second) == 0


@pytest.mark.asyncio
async def test_non_ui_domain_emits_nothing():
    evs = await _collect(_tool("result", "notes"), user_id="u1", tool_names={}, emitted_domains=set())
    assert evs == []


@pytest.mark.asyncio
async def test_start_phase_emits_nothing():
    evs = await _collect(_tool("start", "calendar"), user_id="u1", tool_names={}, emitted_domains=set())
    assert evs == []


@pytest.mark.asyncio
async def test_unhandled_resolve_emits_nothing(monkeypatch):
    import skybridge_service
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", _fake_resolve([], handled=False))
    evs = await _collect(_tool("result", "weather"), user_id="u1", tool_names={}, emitted_domains=set())
    assert evs == []


@pytest.mark.asyncio
async def test_resolver_exception_is_swallowed(monkeypatch):
    import skybridge_service
    async def _boom(query, user_id, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", _boom)
    evs = await _collect(_tool("result", "calendar"), user_id="u1", tool_names={}, emitted_domains=set())
    assert evs == []


@pytest.mark.asyncio
async def test_failed_first_does_not_suppress_later_success(monkeypatch):
    # Greptile P1: a first failed/empty result for a domain must NOT mark it
    # emitted and block a later successful result in the same turn.
    import skybridge_service
    calls = {"n": 0}
    async def flaky(query, user_id, **kw):
        calls["n"] += 1
        return {"handled": False, "cards": []} if calls["n"] == 1 else {"handled": True, "cards": [{"x": 1}]}
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", flaky)
    emitted = set()
    first = await _collect(_tool("result", "calendar"), user_id="u1", tool_names={}, emitted_domains=emitted)
    second = await _collect(_tool("result", "calendar"), user_id="u1", tool_names={}, emitted_domains=emitted)
    assert first == [] and len(second) == 1
