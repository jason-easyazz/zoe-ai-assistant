"""Tests for panel_* MCP tool definitions and execution paths."""

import asyncio
import sys
import os
import json
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mcp_server
from mcp_server import TOOLS


def _tool(name: str) -> dict | None:
    return next((t for t in TOOLS if t["name"] == name), None)


def test_panel_navigate_exists():
    t = _tool("panel_navigate")
    assert t is not None
    assert "url" in t["inputSchema"]["properties"]
    assert "url" in t["inputSchema"]["required"]


def test_panel_clear_exists():
    t = _tool("panel_clear")
    assert t is not None


def test_panel_show_fullscreen_exists():
    t = _tool("panel_show_fullscreen")
    assert t is not None
    assert "image_base64" in t["inputSchema"]["required"]


def test_panel_announce_exists():
    t = _tool("panel_announce")
    assert t is not None
    assert "message" in t["inputSchema"]["required"]


def test_panel_request_auth_exists():
    t = _tool("panel_request_auth")
    assert t is not None
    assert "panel_id" in t["inputSchema"]["required"]
    assert "action_context" in t["inputSchema"]["required"]


def test_panel_check_auth_exists():
    t = _tool("panel_check_auth")
    assert t is not None
    assert "challenge_id" in t["inputSchema"]["required"]


def test_panel_set_mode_exists():
    t = _tool("panel_set_mode")
    assert t is not None
    schema = t["inputSchema"]["properties"]["mode"]
    assert "ambient" in schema["enum"]
    assert "listening" in schema["enum"]


def test_all_panel_tools_have_descriptions():
    panel_tools = [t for t in TOOLS if t["name"].startswith("panel_")]
    assert len(panel_tools) >= 6, f"Expected at least 6 panel tools, got {len(panel_tools)}"
    for t in panel_tools:
        assert t.get("description"), f"Tool {t['name']} is missing a description"


class _RecordingDb:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return _Cursor(None)


@pytest.mark.asyncio
async def test_create_evolution_proposal_stores_contract_snapshot(monkeypatch):
    async def fake_sync_evolution_proposal_to_multica(**_kwargs):
        return "multica-issue-123"

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(sync_evolution_proposal_to_multica=fake_sync_evolution_proposal_to_multica),
    )
    db = _RecordingDb()

    result = await mcp_server._execute_tool(
        db=db,
        name="create_evolution_proposal",
        args={
            "_user_id": "jason",
            "title": "Improve recurring task recall",
            "description": "Zoe missed a recurring task twice and should create a reviewed improvement proposal.",
            "evidence": "chat:recurring-task-miss",
            "proposal_type": "intent_pattern",
        },
    )

    assert result["ok"] is True
    assert result["multica_issue_id"] == "multica-issue-123"
    assert result["contract_schema"] == "zoe_evolution_proposal"
    assert len(db.calls) == 2
    insert_sql, insert_args = db.calls[0]
    update_sql, update_args = db.calls[1]
    assert "target_patterns" in insert_sql
    assert "target_patterns" in update_sql

    insert_contract = json.loads(insert_args[4])
    update_contract = json.loads(update_args[1])
    assert insert_contract["schema"] == "zoe_evolution_proposal"
    assert insert_contract["proposal"]["status"] == "pending_approval"
    assert insert_contract["proposal"]["signals"][0]["signal_type"] == "repeated_failure"
    assert insert_contract["proposal"]["approval_gate"]["allowed_to_execute"] is False
    assert update_contract["proposal"]["multica_issue_id"] == "multica-issue-123"


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Db:
    def __init__(self, row):
        self._row = row

    async def execute(self, *_args, **_kwargs):
        return _Cursor(self._row)


@pytest.mark.asyncio
async def test_panel_ssh_exec_happy_path_uses_logger_without_logging_command(monkeypatch):
    db = _Db(
        {
            "ip_address": "192.168.1.61",
            "ssh_user": "pi",
            "ssh_key_path": "/tmp/test-key",
            "ssh_port": 2222,
        }
    )
    proc_calls = {}
    log_lines = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        proc_calls["args"] = args
        proc_calls["kwargs"] = kwargs
        return _Proc()

    def fake_log_info(msg, *args):
        log_lines.append(msg % args)

    monkeypatch.setattr(mcp_server.os.path, "exists", lambda path: path == "/tmp/test-key")
    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(mcp_server._mcp_log, "info", fake_log_info)

    result = await mcp_server._execute_tool(
        db=db,
        name="panel_ssh_exec",
        args={
            "_user_id": "test-user",
            "panel_id": "zoe-touch-pi",
            "command": "echo super-secret-token",
            "timeout": 5,
        },
    )

    assert result == {
        "panel_id": "zoe-touch-pi",
        "command": "echo super-secret-token",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
    }
    assert proc_calls["args"] == (
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-p", "2222",
        "-i", "/tmp/test-key",
        "pi@192.168.1.61",
        "echo super-secret-token",
    )
    assert proc_calls["kwargs"] == {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    assert log_lines == ["panel_ssh_exec: panel=zoe-touch-pi ip=192.168.1.61"]
    assert "super-secret-token" not in log_lines[0]


@pytest.mark.asyncio
async def test_panel_ssh_exec_timeout_kills_process(monkeypatch):
    db = _Db(
        {
            "ip_address": "192.168.1.61",
            "ssh_user": "pi",
            "ssh_key_path": "/tmp/test-key",
            "ssh_port": 22,
        }
    )
    proc = None

    class _Proc:
        def __init__(self):
            self.killed = False

        async def communicate(self):
            return b"", b""

        def kill(self):
            self.killed = True

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        nonlocal proc
        proc = _Proc()
        return proc

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mcp_server.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(mcp_server.asyncio, "wait_for", fake_wait_for)

    result = await mcp_server._execute_tool(
        db=db,
        name="panel_ssh_exec",
        args={
            "_user_id": "test-user",
            "panel_id": "zoe-touch-pi",
            "command": "uptime",
            "timeout": 9,
        },
    )

    assert result == {
        "error": "SSH command timed out after 9s",
        "panel_id": "zoe-touch-pi",
    }
    assert proc is not None
    assert proc.killed is True


@pytest.mark.asyncio
async def test_panel_ssh_exec_subprocess_error_returns_tool_error(monkeypatch):
    db = _Db(
        {
            "ip_address": "192.168.1.61",
            "ssh_user": "pi",
            "ssh_key_path": "/tmp/test-key",
            "ssh_port": 22,
        }
    )

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        raise RuntimeError("ssh unavailable")

    monkeypatch.setattr(mcp_server.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await mcp_server._execute_tool(
        db=db,
        name="panel_ssh_exec",
        args={
            "_user_id": "test-user",
            "panel_id": "zoe-touch-pi",
            "command": "uptime",
        },
    )

    assert result == {
        "error": "SSH exec failed: ssh unavailable",
        "panel_id": "zoe-touch-pi",
    }
