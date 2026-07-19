"""Tool-calling smoke tests for the current Zoe runtime."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = PROJECT_ROOT / "services" / "zoe-data"
ZOE_API = "http://localhost:8000/api"


async def _post_or_skip(url: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient() as client:
            return await client.post(url, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
        pytest.skip(f"Live service unavailable for {url}: {exc}")


def _run_mcp_stdio(message: dict) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ZOE_DATA) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, str(ZOE_DATA / "mcp_server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.close()
    line = proc.stdout.readline()
    stderr = proc.stderr.read() if proc.stderr else ""
    returncode = proc.wait(timeout=10)
    assert returncode == 0, stderr
    assert line, stderr
    return json.loads(line)


def test_mcp_stdio_lists_tools():
    response = _run_mcp_stdio({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})

    tools = response["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert "list_add_item" in tool_names
    assert "calendar_list_events" in tool_names


def test_chat_router_maps_core_brain_tool_sentinels_to_ag_ui_events():
    # W4-C2 moved the sentinel→AG-UI mapper verbatim to chat_stream_protocol.py;
    # chat.py re-imports it (permanent re-export shim) and its streaming path
    # still calls it. Pin both the new home and the seam.
    chat_source = (ZOE_DATA / "routers" / "chat.py").read_text()
    protocol_source = (ZOE_DATA / "chat_stream_protocol.py").read_text()

    assert "def brain_tool_sentinel_events" in protocol_source
    assert "ToolCallStartEvent" in protocol_source
    assert "ToolCallArgsEvent" in protocol_source
    assert "ToolCallResultEvent" in protocol_source
    assert "from chat_stream_protocol import" in chat_source
    assert "for _tool_ev in brain_tool_sentinel_events(" in chat_source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Add bread to shopping list",
        "What tools do you have?",
        "Turn on the living room light",
    ],
)
async def test_chat_tool_calling_returns_structured_response(message):
    # Canonical route is POST /api/chat/ (router prefix "/api/chat" + route "/");
    # the slash-less URL 307-redirects, which httpx does not follow by default.
    response = await _post_or_skip(
        f"{ZOE_API}/chat/?stream=false",
        json={"message": message, "user_id": "test_user"},
        timeout=30.0,
    )

    assert response.status_code == 200, response.text[:300]
    result = response.json()
    assert isinstance(result.get("response"), str)
    assert result["response"].strip()
    assert isinstance(result.get("session_id"), str)
    assert result["session_id"].strip()
