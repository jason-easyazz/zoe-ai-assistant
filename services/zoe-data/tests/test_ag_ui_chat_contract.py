"""Lightweight ordering checks for AG-UI chat SSE (no live OpenClaw)."""
import asyncio
import uuid

from ag_ui_stream import AgRunRecorder, iter_text_message_chunks, new_run_ids
from ag_ui.core import (
    CustomEvent,
    EventType,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder


def test_text_stream_start_chunk_end():
    async def _run():
        enc = EventEncoder()
        rec = AgRunRecorder()
        _, mid = new_run_ids()
        chunks: list[str] = []
        rec.emit(
            enc,
            TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=mid, role="assistant"),
        )
        async for line in iter_text_message_chunks(enc, rec, mid, "Hello\nWorld", min_chars=3, max_interval_s=0):
            chunks.append(line)
        rec.emit(enc, TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=mid))
        return chunks, rec.events

    chunks, events = asyncio.run(_run())
    assert chunks
    assert events[0]["type"] == "TEXT_MESSAGE_START"
    assert events[-1]["type"] == "TEXT_MESSAGE_END"
    assert any(e["type"] == "TEXT_MESSAGE_CHUNK" for e in events)


def test_run_lifecycle_types():
    enc = EventEncoder()
    rec = AgRunRecorder()
    run_id, mid = new_run_ids()
    sid = "session_test"
    tcid = uuid.uuid4().hex[:12]
    rec.emit(enc, RunStartedEvent(type=EventType.RUN_STARTED, thread_id=sid, run_id=run_id))
    rec.emit(
        enc,
        CustomEvent(name="zoe.session", value={"sessionId": sid, "messageId": mid}),
    )
    rec.emit(
        enc,
        ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            tool_call_id=tcid,
            tool_call_name="zoe-data.list_show",
            parent_message_id=mid,
        ),
    )
    rec.emit(enc, ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id=tcid, delta="{}"))
    rec.emit(enc, ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tcid))
    rec.emit(
        enc,
        ToolCallResultEvent(
            type=EventType.TOOL_CALL_RESULT,
            message_id=mid,
            tool_call_id=tcid,
            content="ok",
            role="tool",
        ),
    )
    rec.emit(enc, RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=sid, run_id=run_id))
    types = [e["type"] for e in rec.events]
    assert types[0] == "RUN_STARTED"
    assert "TOOL_CALL_START" in types
    assert types[-1] == "RUN_FINISHED"
