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
    # W4-C2 moved the mapper verbatim to chat_stream_protocol.py; chat.py keeps a
    # permanent re-export shim. Pin the mapper's new home and chat's shim import.
    chat_source = (ZOE_DATA / "routers" / "chat.py").read_text()
    protocol_source = (ZOE_DATA / "chat_stream_protocol.py").read_text()

    # W4-C1 moved the zoe_core_client import behind brain_dispatch (chat.py no
    # longer imports run_zoe_core directly) — pin that seam, not the old import.
    assert "from brain_dispatch import" in chat_source
    dispatch_source = (ZOE_DATA / "brain_dispatch.py").read_text()
    assert "from zoe_core_client import run_zoe_core" in dispatch_source
    assert "from chat_stream_protocol import" in chat_source
    assert "def brain_tool_sentinel_events" in protocol_source
    assert "ToolCallStartEvent" in protocol_source
    assert "ToolCallArgsEvent" in protocol_source
    assert "ToolCallEndEvent" in protocol_source
    assert "ToolCallResultEvent" in protocol_source


def test_chat_router_uses_brain_tool_sentinel_events_in_streaming_path():
    chat_source = (ZOE_DATA / "routers" / "chat.py").read_text()

    # The shim import must precede the streaming-path use (the name resolves in
    # chat's module globals so tests can still monkeypatch it on routers.chat).
    import_pos = chat_source.index("from chat_stream_protocol import")
    streaming_use_pos = chat_source.index("for _tool_ev in brain_tool_sentinel_events(")
    assert import_pos < streaming_use_pos
