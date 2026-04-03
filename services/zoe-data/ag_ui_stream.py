"""
Canonical AG-UI SSE emission for zoe-data chat (ag-ui-protocol EventEncoder).

Zoe extensions use CUSTOM events with namespaced `name` values (e.g. zoe.session).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, List

from ag_ui.core import (
    CustomEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageChunkEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder


class AgRunRecorder:
    """Records JSON payloads actually sent on the wire (for persistence / tests)."""

    def __init__(self) -> None:
        self.events: List[dict[str, Any]] = []

    def emit(self, enc: EventEncoder, event) -> str:
        block = enc.encode(event)
        line = block.strip()
        if line.startswith("data: "):
            self.events.append(json.loads(line[6:]))
        return block


async def iter_text_message_chunks(
    enc: EventEncoder,
    recorder: AgRunRecorder,
    message_id: str,
    text: str,
    *,
    min_chars: int = 240,
    max_interval_s: float = 0.15,
) -> AsyncIterator[str]:
    """Batched TEXT_MESSAGE_CHUNK events (assistant role)."""
    if not text:
        return
    buf = ""
    last_flush = time.monotonic()
    last_idx = len(text) - 1
    for idx, ch in enumerate(text):
        buf += ch
        now = time.monotonic()
        newline_flush = ch == "\n" and len(buf) >= 48
        if buf and (
            len(buf) >= min_chars
            or newline_flush
            or (now - last_flush) >= max_interval_s
            or idx == last_idx
        ):
            ev = TextMessageChunkEvent(
                type=EventType.TEXT_MESSAGE_CHUNK,
                message_id=message_id,
                role="assistant",
                delta=buf,
            )
            yield recorder.emit(enc, ev)
            buf = ""
            last_flush = now
        if idx % 500 == 0:
            await asyncio.sleep(0)


def new_run_ids() -> tuple[str, str]:
    """Returns (run_id, assistant_message_id)."""
    return uuid.uuid4().hex, uuid.uuid4().hex[:12]
