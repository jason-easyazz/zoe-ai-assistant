"""Checks for the current code/tool execution routing path.

The retired standalone execution service is intentionally not used here. Current
execution/tool activity is surfaced by the Pi/core brain as tool sentinels that
the chat router maps to AG-UI tool events.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = PROJECT_ROOT / "services" / "zoe-data"


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
