"""Lightweight ordering checks for AG-UI chat SSE (no live OpenClaw)."""
import pytest
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

pytestmark = pytest.mark.ci_safe


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


def test_brain_tool_sentinels_emit_canonical_tool_events_in_order():
    """A brain turn carrying __TOOL__ sentinels (start → args → result) maps to
    canonical AG-UI events: STEP_STARTED, TOOL_CALL_START, TOOL_CALL_ARGS,
    TOOL_CALL_END, TOOL_CALL_RESULT, STEP_FINISHED — in that order, with the args
    delta and result content carried through and the same tool_call_id throughout.

    Drives the SAME mapping chat.py uses (brain_tool_sentinel_events) so this
    contract test exercises production code, not a re-implementation."""
    import json as _json

    from routers.chat import brain_tool_sentinel_events

    enc = EventEncoder()
    rec = AgRunRecorder()
    _, mid = new_run_ids()
    tool_names: dict[str, str] = {}

    sentinels = [
        "__TOOL__:" + _json.dumps({"phase": "start", "id": "tc-1", "name": "web_search"}),
        "__TOOL__:" + _json.dumps({"phase": "args", "id": "tc-1", "name": "web_search",
                                   "args": {"query": "weekend weather"}}),
        # result sentinel intentionally omits name — step must still finish as web_search
        "__TOOL__:" + _json.dumps({"phase": "result", "id": "tc-1",
                                   "result": "Saturday is sunny, 24 degrees."}),
    ]
    for s in sentinels:
        for ev in brain_tool_sentinel_events(s, assistant_message_id=mid, tool_names=tool_names):
            rec.emit(enc, ev)

    types = [e["type"] for e in rec.events]
    assert types == [
        "STEP_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "STEP_FINISHED",
    ], types

    by_type = {e["type"]: e for e in rec.events}
    # Same tool_call_id threads through every tool event.
    tcid = by_type["TOOL_CALL_START"]["toolCallId"]
    assert tcid == "tc-1"
    assert by_type["TOOL_CALL_ARGS"]["toolCallId"] == tcid
    assert by_type["TOOL_CALL_END"]["toolCallId"] == tcid
    assert by_type["TOOL_CALL_RESULT"]["toolCallId"] == tcid
    # Args delta and result content survive the mapping.
    assert _json.loads(by_type["TOOL_CALL_ARGS"]["delta"]) == {"query": "weekend weather"}
    assert by_type["TOOL_CALL_RESULT"]["content"] == "Saturday is sunny, 24 degrees."
    assert by_type["TOOL_CALL_START"]["toolCallName"] == "web_search"
    # Step opens and closes under the same name even though result omitted it.
    assert by_type["STEP_STARTED"]["stepName"] == "web_search"
    assert by_type["STEP_FINISHED"]["stepName"] == "web_search"


def test_brain_tool_sentinel_malformed_is_skipped():
    """A malformed __TOOL__ sentinel yields no events and never raises."""
    from routers.chat import brain_tool_sentinel_events

    tool_names: dict[str, str] = {}
    out = list(brain_tool_sentinel_events("__TOOL__:not json", assistant_message_id="m", tool_names=tool_names))
    assert out == []
    # A phase with a missing id is skipped too.
    import json as _json
    s = "__TOOL__:" + _json.dumps({"phase": "start", "name": "x"})  # no id
    assert list(brain_tool_sentinel_events(s, assistant_message_id="m", tool_names=tool_names)) == []
