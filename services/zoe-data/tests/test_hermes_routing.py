import asyncio
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mcp_server
import zoe_agent
from routers import chat as chat_router, system, voice_tts

pytestmark = pytest.mark.ci_safe


def _decode_agui_events(blocks):
    events = []
    for block in blocks:
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


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
async def test_force_hermes_uses_single_chat_run_and_persists_once(monkeypatch):
    saved_messages = []
    recorded_runs = []
    agui_runs = []
    persisted_candidates = []

    async def fake_save(session_id, role, content, user_id=None):
        saved_messages.append((session_id, role, content))

    async def fake_record_run_state(*args, **kwargs):
        recorded_runs.append((args, kwargs))

    async def fake_persist_agui(session_id, run_id, events):
        agui_runs.append((session_id, run_id, events))

    async def fake_empty(*args, **kwargs):
        return ""

    async def fake_hermes_events(*args, **kwargs):
        yield {"kind": "token", "text": "Hermes "}
        yield {"kind": "token", "text": "answer"}

    async def fake_persist(user_id, session_id, user_text, assistant_text):
        persisted_candidates.append((user_id, session_id, user_text, assistant_text))

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", fake_empty)
    monkeypatch.setattr(chat_router, "_save_chat_message", fake_save)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *_, **__: None)
    monkeypatch.setattr(chat_router, "_GUARDED_AUTO", False)
    monkeypatch.setattr(chat_router, "_safe_load_portrait", fake_empty)
    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", fake_empty)
    monkeypatch.setattr(chat_router, "_build_memory_context", fake_empty)
    monkeypatch.setattr(chat_router, "_iter_hermes_stream_events", fake_hermes_events)
    monkeypatch.setattr(chat_router, "_record_run_state", fake_record_run_state)
    monkeypatch.setattr(chat_router, "_persist_ag_ui_run", fake_persist_agui)
    monkeypatch.setattr(chat_router, "_persist_memory_candidates", fake_persist)

    blocks = [
        block
        async for block in chat_router.chat_stream_generator(
            "talk to hermes",
            "session-hermes",
            {"user_id": "user-1", "username": "Zoe"},
            force_agent="hermes",
        )
    ]
    await asyncio.sleep(0)

    events = _decode_agui_events(blocks)
    event_types = [event["type"] for event in events]
    assert event_types.count("RUN_STARTED") == 1
    assert event_types.count("RUN_FINISHED") == 1
    assert event_types.count("TEXT_MESSAGE_START") == 1
    assert event_types.count("TEXT_MESSAGE_END") == 1
    assert any(
        event["type"] == "CUSTOM"
        and event.get("name") == "zoe.run_meta"
        and event.get("value", {}).get("mode") == "hermes"
        for event in events
    )
    assert saved_messages == [
        ("session-hermes", "user", "talk to hermes"),
        ("session-hermes", "assistant", "Hermes answer"),
    ]
    assert persisted_candidates == [("user-1", "session-hermes", "talk to hermes", "Hermes answer")]
    assert recorded_runs[-1][1]["mode"] == "hermes"
    assert recorded_runs[-1][1]["response_text"] == "Hermes answer"
    assert agui_runs[-1][0] == "session-hermes"
    assert agui_runs[-1][2][-1]["type"] == "RUN_FINISHED"


@pytest.mark.asyncio
async def test_force_hermes_surfaces_progress_events(monkeypatch):
    async def fake_empty(*args, **kwargs):
        return ""

    async def fake_record_run_state(*args, **kwargs):
        return None

    async def fake_hermes_events(*args, **kwargs):
        yield {
            "kind": "progress",
            "event": "hermes.tool.progress",
            "payload": {"tool": "browser", "message": "Opening docs"},
        }
        yield {"kind": "token", "text": "Done"}

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", fake_empty)
    monkeypatch.setattr(chat_router, "_save_chat_message", fake_empty)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *_, **__: None)
    monkeypatch.setattr(chat_router, "_GUARDED_AUTO", False)
    monkeypatch.setattr(chat_router, "_safe_load_portrait", fake_empty)
    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", fake_empty)
    monkeypatch.setattr(chat_router, "_build_memory_context", fake_empty)
    monkeypatch.setattr(chat_router, "_iter_hermes_stream_events", fake_hermes_events)
    monkeypatch.setattr(chat_router, "_record_run_state", fake_record_run_state)
    monkeypatch.setattr(chat_router, "_persist_ag_ui_run", fake_empty)
    monkeypatch.setattr(chat_router, "_persist_memory_candidates", fake_empty)

    blocks = [
        block
        async for block in chat_router.chat_stream_generator(
            "talk to hermes",
            "session-hermes-progress",
            {"user_id": "user-1", "username": "Zoe"},
            force_agent="hermes",
        )
    ]

    events = _decode_agui_events(blocks)
    assert any(
        event["type"] == "STATE_SNAPSHOT"
        and event.get("snapshot", {}).get("phase") == "hermes_tool"
        and event.get("snapshot", {}).get("detail") == "browser: Opening docs"
        for event in events
    )
    assert any(
        event["type"] == "CUSTOM"
        and event.get("name") == "zoe.run_log"
        and event.get("value", {}).get("source") == "hermes"
        for event in events
    )


@pytest.mark.asyncio
async def test_force_hermes_error_records_hermes_mode(monkeypatch):
    recorded_runs = []
    agui_runs = []

    async def fake_record_run_state(*args, **kwargs):
        recorded_runs.append((args, kwargs))

    async def fake_empty(*args, **kwargs):
        return ""

    async def fake_hermes_events(*args, **kwargs):
        raise RuntimeError("hermes down")
        yield {}

    async def fake_persist_agui(session_id, run_id, events):
        agui_runs.append((session_id, run_id, events))

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", fake_empty)
    monkeypatch.setattr(chat_router, "_save_chat_message", fake_empty)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *_, **__: None)
    monkeypatch.setattr(chat_router, "_GUARDED_AUTO", False)
    monkeypatch.setattr(chat_router, "_safe_load_portrait", fake_empty)
    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", fake_empty)
    monkeypatch.setattr(chat_router, "_build_memory_context", fake_empty)
    monkeypatch.setattr(chat_router, "_iter_hermes_stream_events", fake_hermes_events)
    monkeypatch.setattr(chat_router, "_record_run_state", fake_record_run_state)
    monkeypatch.setattr(chat_router, "_persist_ag_ui_run", fake_persist_agui)

    blocks = [
        block
        async for block in chat_router.chat_stream_generator(
            "talk to hermes",
            "session-hermes-error",
            {"user_id": "user-1", "username": "Zoe"},
            force_agent="hermes",
        )
    ]

    events = _decode_agui_events(blocks)
    assert any(event["type"] == "RUN_ERROR" for event in events)
    assert recorded_runs[-1][1]["mode"] == "hermes"
    assert recorded_runs[-1][1]["status"] == "error"
    assert "hermes down" in recorded_runs[-1][1]["response_text"]
    assert agui_runs[-1][0] == "session-hermes-error"


@pytest.mark.asyncio
async def test_force_hermes_approval_rejection_records_hermes_mode(monkeypatch):
    recorded_runs = []

    async def fake_record_run_state(*args, **kwargs):
        recorded_runs.append((args, kwargs))

    async def fake_empty(*args, **kwargs):
        return ""

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", fake_empty)
    monkeypatch.setattr(chat_router, "_save_chat_message", fake_empty)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *_, **__: None)
    monkeypatch.setattr(chat_router, "_resolve_approval", fake_empty)
    monkeypatch.setattr(chat_router, "_record_run_state", fake_record_run_state)
    monkeypatch.setattr(chat_router, "_persist_ag_ui_run", fake_empty)

    blocks = [
        block
        async for block in chat_router.chat_stream_generator(
            "/approve deadbeef",
            "session-hermes-approval",
            {"user_id": "user-1", "username": "Zoe"},
            force_agent="hermes",
        )
    ]

    events = _decode_agui_events(blocks)
    assert any(event["type"] == "RUN_ERROR" for event in events)
    assert recorded_runs[-1][1]["mode"] == "hermes"
    assert recorded_runs[-1][1]["status"] == "error"


@pytest.mark.asyncio
async def test_zoe_agent_hermes_escalation_stays_in_parent_agui_run(monkeypatch):
    saved_messages = []
    recorded_runs = []
    agui_runs = []
    persisted_candidates = []

    async def fake_save(session_id, role, content, user_id=None, **kwargs):
        saved_messages.append((session_id, role, content))
        # Production _save_chat_message returns True on a committed row (#920);
        # the stream raises "assistant reply save failed" on a falsy return.
        return True

    async def fake_record_run_state(*args, **kwargs):
        recorded_runs.append((args, kwargs))

    async def fake_persist_agui(session_id, run_id, events):
        agui_runs.append((session_id, run_id, events))

    async def fake_empty(*args, **kwargs):
        return ""

    async def fake_zoe_stream(*args, **kwargs):
        yield "__ESCALATE_HERMES__:deep work|Hermes task"

    async def fake_hermes_events(*args, **kwargs):
        yield {"kind": "token", "text": "Deep "}
        yield {"kind": "token", "text": "answer"}

    async def fake_persist(user_id, session_id, user_text, assistant_text):
        persisted_candidates.append((user_id, session_id, user_text, assistant_text))

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", fake_empty)
    monkeypatch.setattr(chat_router, "_save_chat_message", fake_save)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *_, **__: None)
    monkeypatch.setattr(chat_router, "_GUARDED_AUTO", False)
    monkeypatch.setattr(chat_router, "_ALL_TOOLS_ENABLED", False)
    monkeypatch.setattr(chat_router, "_USE_ZOE_AGENT", True)
    # This test validates the legacy zoe_agent Hermes-escalation contract, so pin
    # the brain to zoe_agent: the cutover routes to zoe-core by default, which has
    # no Hermes-escalation signal yet (delegation abilities tracked separately).
    # The routing guards branch on _USE_LOCAL_BRAIN (an import-time constant), so
    # patch it explicitly — patching only its inputs has no effect post-import.
    monkeypatch.setattr(chat_router, "_USE_ZOE_CORE", False)
    monkeypatch.setattr(chat_router, "_USE_LOCAL_BRAIN", True)
    monkeypatch.setattr(chat_router, "classify_query", lambda *_: "general")
    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", fake_empty)
    monkeypatch.setattr(chat_router, "_safe_load_portrait", fake_empty)
    monkeypatch.setattr(chat_router, "run_zoe_agent_streaming", fake_zoe_stream)
    monkeypatch.setattr(chat_router, "_iter_hermes_stream_events", fake_hermes_events)
    monkeypatch.setattr(chat_router, "_record_run_state", fake_record_run_state)
    monkeypatch.setattr(chat_router, "_persist_ag_ui_run", fake_persist_agui)
    monkeypatch.setattr(chat_router, "_persist_memory_candidates", fake_persist)
    monkeypatch.setitem(
        sys.modules,
        "pending_suggestions",
        types.SimpleNamespace(
            list_active=fake_empty,
            ui_components_for_suggestions=lambda *_: [],
        ),
    )

    blocks = [
        block
        async for block in chat_router.chat_stream_generator(
            "please escalate",
            "session-escalate",
            {"user_id": "user-1", "username": "Zoe"},
        )
    ]
    await asyncio.sleep(0)

    events = _decode_agui_events(blocks)
    event_types = [event["type"] for event in events]
    assert event_types.count("RUN_STARTED") == 1
    assert event_types.count("RUN_FINISHED") == 1
    assert event_types.count("TEXT_MESSAGE_START") == 1
    assert event_types.count("TEXT_MESSAGE_END") == 1
    assert any(
        event["type"] == "STATE_SNAPSHOT"
        and event.get("snapshot", {}).get("phase") == "hermes"
        for event in events
    )
    assert saved_messages == [
        ("session-escalate", "user", "please escalate"),
        ("session-escalate", "assistant", "Deep answer"),
    ]
    assert persisted_candidates == [("user-1", "session-escalate", "please escalate", "Deep answer")]
    assert recorded_runs[-1][1]["mode"] == "chat"
    assert recorded_runs[-1][1]["response_text"] == "Deep answer"
    assert agui_runs[-1][0] == "session-escalate"
    assert agui_runs[-1][2][-1]["type"] == "RUN_FINISHED"


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
        lambda: (_ for _ in ()).throw(AssertionError("Hermes should not require agents_registry.yml")),
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
        lambda: (_ for _ in ()).throw(AssertionError("Hermes should not require agents_registry.yml")),
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
