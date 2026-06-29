"""Live code execution integration checks.

The HTTP tests require the code execution service on localhost. Unavailable
services skip visibly; reachable services must satisfy the expected behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = PROJECT_ROOT / "services" / "zoe-data"
CODE_EXECUTION_URL = "http://localhost:8010/execute"


async def _execute_or_skip(payload: dict) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(CODE_EXECUTION_URL, json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
        pytest.skip(f"Code execution service unavailable: {exc}")


@pytest.mark.asyncio
async def test_code_execution_service_runs_typescript():
    response = await _execute_or_skip(
        {
            "code": "console.log('Hello from code execution!');",
            "language": "typescript",
            "user_id": "test_user",
        }
    )

    assert response.status_code == 200, response.text[:300]
    result = response.json()
    assert result.get("success") is True, result.get("error") or result
    assert "Hello from code execution!" in result.get("output", "")


@pytest.mark.asyncio
async def test_mcp_tool_executes_via_code_execution_service():
    code = """
import * as zoeLists from './servers/zoe-lists';

const result = await zoeLists.addToList({
    list_name: 'test-list',
    task_text: 'Test task from code execution',
    priority: 'medium'
});

console.log(JSON.stringify(result));
"""

    response = await _execute_or_skip(
        {
            "code": code,
            "language": "typescript",
            "user_id": "test_user",
        }
    )

    assert response.status_code == 200, response.text[:300]
    result = response.json()
    assert result.get("success") is True, result.get("error") or result
    output = result.get("output", "")
    assert "success" in output.lower() or "added" in output.lower(), output


def test_chat_router_wires_core_brain_tool_event_mapping():
    chat_source = (ZOE_DATA / "routers" / "chat.py").read_text()

    assert "from zoe_core_client import run_zoe_core, run_zoe_core_streaming" in chat_source
    assert "def brain_tool_sentinel_events" in chat_source
    assert "ToolCallStartEvent" in chat_source
    assert "ToolCallArgsEvent" in chat_source
    assert "ToolCallEndEvent" in chat_source
    assert "ToolCallResultEvent" in chat_source


def test_chat_router_uses_brain_tool_sentinel_events_in_streaming_path():
    chat_source = (ZOE_DATA / "routers" / "chat.py").read_text()

    mapper_pos = chat_source.index("def brain_tool_sentinel_events")
    streaming_use_pos = chat_source.index("for _tool_ev in brain_tool_sentinel_events(")
    assert mapper_pos < streaming_use_pos
