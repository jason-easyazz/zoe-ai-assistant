"""Unit checks for the production Zoe MCP stdio/dispatch surface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = PROJECT_ROOT / "services" / "zoe-data"


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


def _import_mcp_server():
    sys.path.insert(0, str(ZOE_DATA))
    try:
        import mcp_server
    except Exception as exc:
        pytest.skip(f"Cannot import production mcp_server.py dependencies: {exc}")
    return mcp_server


def test_stdio_tools_list_exposes_production_tools():
    response = _run_mcp_stdio({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    tools = response["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert "list_add_item" in tool_names
    assert "calendar_create_event" in tool_names


class _Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _DispatchDb:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT id FROM lists" in sql:
            return _Cursor([])
        return _Cursor([])


@pytest.mark.asyncio
async def test_execute_tool_dispatches_list_add_item_through_production_path(monkeypatch):
    mcp_server = _import_mcp_server()

    async def _noop_notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mcp_server, "_notify_ui", _noop_notify)
    db = _DispatchDb()

    result = await mcp_server._execute_tool(
        db,
        "list_add_item",
        {"list_type": "shopping", "list_name": "Groceries", "text": "bread"},
        actor_context={"user_id": "dispatch-user", "source": "transport_meta"},
    )

    assert result["status"] == "added"
    assert result["list"] == "Groceries"
    assert result["text"] == "bread"
    assert any("INSERT INTO lists" in sql for sql, _params in db.calls)
    assert any("INSERT INTO list_items" in sql for sql, _params in db.calls)
    list_insert = next(params for sql, params in db.calls if "INSERT INTO lists" in sql)
    assert list_insert[1] == "dispatch-user"


def test_tool_metadata_matches_dispatch_name():
    mcp_server = _import_mcp_server()

    list_add = next(tool for tool in mcp_server.TOOLS if tool["name"] == "list_add_item")
    schema = list_add["inputSchema"]
    assert schema["required"] == ["list_type", "text"]
    assert "text" in schema["properties"]
