import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mcp_server
import zoe_agent
from routers import system


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
    async def none_async(*args, **kwargs):
        return ""

    async def fake_llm_call(*args, **kwargs):
        return "", "escalate_to_hermes", {"reason": "deep work", "task": "Use Hermes"}

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
    monkeypatch.setattr(zoe_agent, "_llm_call", fake_llm_call)
    monkeypatch.setattr(zoe_agent, "_fire_memory_capture", lambda *_, **__: None)

    result = await zoe_agent.run_zoe_agent(
        "please hand this to hermes",
        "test-session",
        user_id="test-user",
    )

    assert result == "__ESCALATE_HERMES__:deep work|Use Hermes"


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
        "status": "queued",
        "task_id": 123,
        "result_endpoint": "/api/agent/tasks/123",
    }
    assert calls == [
        {
            "task": "Investigate routing",
            "user_id": "test-user",
            "session_id": "session-1",
            "request_depth": 2,
        }
    ]


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
