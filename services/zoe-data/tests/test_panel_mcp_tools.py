"""Tests for panel_* MCP tool definitions."""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
