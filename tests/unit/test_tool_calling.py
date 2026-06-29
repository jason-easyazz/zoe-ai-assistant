"""Live tool-calling smoke tests.

These require the MCP and Zoe API services. Unavailable services skip visibly;
reachable services must satisfy real response assertions.
"""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

MCP_URL = "http://localhost:8003"
ZOE_API = "http://localhost:8000/api"


async def _post_or_skip(url: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient() as client:
            return await client.post(url, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
        pytest.skip(f"Live service unavailable for {url}: {exc}")


@pytest.mark.asyncio
async def test_mcp_server_lists_tools():
    response = await _post_or_skip(
        f"{MCP_URL}/tools/list",
        json={"_auth_token": "default", "_session_id": "default"},
        timeout=5.0,
    )

    assert response.status_code == 200, response.text[:300]
    tools_data = response.json()
    assert tools_data["total_tools"] > 0
    assert isinstance(tools_data["categories"], (dict, list))


@pytest.mark.asyncio
async def test_mcp_add_to_list_tool_executes():
    response = await _post_or_skip(
        f"{MCP_URL}/tools/add_to_list",
        json={
            "list_name": "test_shopping",
            "task_text": "test item",
            "priority": "medium",
            "_auth_token": "default",
            "_session_id": "default",
        },
        timeout=5.0,
    )

    assert response.status_code == 200, response.text[:300]
    result = response.json()
    assert result.get("success", True) is not False
    assert result.get("message") or result.get("result") or result.get("data")


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
    response = await _post_or_skip(
        f"{ZOE_API}/chat",
        json={"message": message, "user_id": "test_user"},
        timeout=30.0,
    )

    assert response.status_code == 200, response.text[:300]
    result = response.json()
    assert isinstance(result.get("response"), str)
    assert result["response"].strip()
    assert "response_time" in result
    assert "routing" in result
