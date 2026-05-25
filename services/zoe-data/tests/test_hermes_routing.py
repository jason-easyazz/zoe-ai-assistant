import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mcp_server
import zoe_agent
from routers import system, voice_tts


def _patch_agent_context(monkeypatch):
    async def none_async(*args, **kwargs):
        return ""

    monkeypatch.setattr(zoe_agent, "_check_fast_response", lambda *_: None)
    monkeypatch.setattr(zoe_agent, "_chat_capability_shortcut", none_async)
    monkeypatch.setattr(zoe_agent, "_load_user_portrait", none_async)
    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", none_async)
    monkeypatch.setattr(zoe_agent, "_build_memory_context", none_async)
    monkeypatch.setattr(zoe_agent, "_load_open_loops", none_async)
    monkeypatch.setattr(zoe_agent, "_load_pending_suggestions", none_async)
    monkeypatch.setattr(zoe_agent, "_context_enhance", none_async)
    monkeypatch.setattr(zoe_agent, "_select_skills", lambda *_: set())
    monkeypatch.setattr(zoe_agent, "_build_tools", lambda *_: [])
    monkeypatch.setattr(zoe_agent, "_classify_tone", lambda *_: "")
    monkeypatch.setattr(zoe_agent, "_fire_memory_capture", lambda *_, **__: None)


@pytest.mark.asyncio
async def test_dispatch_escalate_to_hermes_returns_marker():
    result = await zoe_agent._dispatch_tool(
        "escalate_to_hermes",
        {"reason": "architecture review", "task": "Review Hermes routing"},
        user_id="test-user",
    )

    assert result == "__ESCALATE_HERMES__:architecture review|Review Hermes routing"


@pytest.mark.asyncio
async def test_run_zoe_agent_returns_hermes_escalation_marker(monkeypatch):
    async def fake_llm_call(*args, **kwargs):
        return "", "escalate_to_hermes", {"reason": "deep work", "task": "Use Hermes"}

    _patch_agent_context(monkeypatch)
    monkeypatch.setattr(zoe_agent, "_llm_call", fake_llm_call)

    result = await zoe_agent.run_zoe_agent(
        "please hand this to hermes",
        "test-session",
        user_id="test-user",
    )

    assert result == "__ESCALATE_HERMES__:deep work|Use Hermes"


@pytest.mark.asyncio
async def test_run_zoe_agent_streaming_yields_hermes_escalation_marker(monkeypatch):
    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"escalate_to_hermes","arguments":"{\\"reason\\": \\"deep work\\", \\"task\\": \\"Use Hermes\\"}"}}]}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *args, **kwargs):
            return FakeResponse()

    _patch_agent_context(monkeypatch)
    monkeypatch.setattr(zoe_agent.httpx, "AsyncClient", FakeClient)

    chunks = [
        chunk
        async for chunk in zoe_agent.run_zoe_agent_streaming(
            "please hand this to hermes",
            "test-session",
            user_id="test-user",
        )
    ]

    assert "__THINKING__:escalate_to_hermes" in chunks
    assert chunks[-1] == "__ESCALATE_HERMES__:deep work|Use Hermes"


def test_voice_hermes_marker_stays_foreground():
    is_background, reason, prompt = voice_tts._parse_voice_escalation_delta(
        "__ESCALATE_HERMES__:deep work|Use Hermes",
        "fallback prompt",
    )

    assert is_background is False
    assert reason == "deep work"
    assert prompt == "Use Hermes"


def test_voice_background_marker_queues_work():
    is_background, reason, prompt = voice_tts._parse_voice_escalation_delta(
        "__ESCALATE_BG__:long task|Run later",
        "fallback prompt",
    )

    assert is_background is True
    assert reason == "long task"
    assert prompt == "Run later"


@pytest.mark.asyncio
async def test_mcp_a2a_delegate_hermes_queues_background_task(monkeypatch):
    calls = []

    async def fake_enqueue(task, user_id, session_id=None, panel_id=None, request_depth=0, multica_issue_id=None):
        calls.append(
            {
                "task": task,
                "user_id": user_id,
                "session_id": session_id,
                "request_depth": request_depth,
            }
        )
        return 123

    monkeypatch.setitem(
        sys.modules,
        "background_runner",
        types.SimpleNamespace(enqueue_background_task=fake_enqueue),
    )
    monkeypatch.setitem(
        sys.modules,
        "a2a_client",
        types.SimpleNamespace(get_a2a_client=lambda: (_ for _ in ()).throw(AssertionError("A2A should not be used for Hermes"))),
    )
    monkeypatch.setattr(
        mcp_server,
        "_load_agents_registry",
        lambda: {"agents": {"hermes": {"base_url": "http://localhost:8642"}}},
    )

    result = await mcp_server._execute_tool(
        db=None,
        name="a2a_delegate",
        args={
            "_user_id": "test-user",
            "agent_name": "hermes",
            "task": "Investigate routing",
            "session_id": "session-1",
            "request_depth": 2,
        },
    )

    assert result == {
        "agent": "hermes",
        "result": {
            "status": "queued",
            "task_id": 123,
            "result_endpoint": "/api/agent/tasks/123",
        },
    }
    assert calls == [
        {
            "task": "Investigate routing",
            "user_id": "test-user",
            "session_id": "session-1",
            "request_depth": 2,
        }
    ]


def test_mcp_agents_registry_missing_file_fails_softly(monkeypatch):
    def fake_open(*args, **kwargs):
        raise FileNotFoundError("missing registry")

    monkeypatch.setattr("builtins.open", fake_open)

    assert mcp_server._load_agents_registry() == {"agents": {}, "squads": {}}


@pytest.mark.asyncio
async def test_system_delegate_to_hermes_queues_background_task(monkeypatch):
    calls = []

    async def fake_enqueue(task, user_id, session_id=None, panel_id=None, request_depth=0, multica_issue_id=None):
        calls.append(
            {
                "task": task,
                "user_id": user_id,
                "session_id": session_id,
                "request_depth": request_depth,
            }
        )
        return 456

    monkeypatch.setattr(
        system,
        "_load_registry",
        lambda: {"agents": {"hermes": {"base_url": "http://localhost:8642"}}},
    )
    monkeypatch.setitem(
        sys.modules,
        "background_runner",
        types.SimpleNamespace(enqueue_background_task=fake_enqueue),
    )
    monkeypatch.setitem(
        sys.modules,
        "a2a_client",
        types.SimpleNamespace(get_a2a_client=lambda: (_ for _ in ()).throw(AssertionError("A2A should not be used for Hermes"))),
    )

    result = await system.delegate_to_agent(
        {
            "agent_name": "hermes",
            "task": "Investigate routing",
            "session_id": "session-2",
            "request_depth": 1,
        },
        user={"user_id": "test-user"},
    )

    assert result == {
        "agent": "hermes",
        "result": {
            "status": "queued",
            "task_id": 456,
            "result_endpoint": "/api/agent/tasks/456",
        },
    }
    assert calls == [
        {
            "task": "Investigate routing",
            "user_id": "test-user",
            "session_id": "session-2",
            "request_depth": 1,
        }
    ]
