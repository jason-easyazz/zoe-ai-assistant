"""Live-activity strip: brain ``__TOOL__`` sentinel → panel ``activity`` frame.

The ``/ws/voice/`` brain lane (main.py websocket_voice) consumes brain
sentinels via ``routers.voice_tts._forward_voice_activity`` — sentinels are
never spoken, and tool start/result phases are forwarded to the touch panel as
lightweight ``{"type": "activity", "phase": ..., "tool": ...}`` frames.

Pure unit tests over the helpers — no live brain, no TTS, no FastAPI app run.

    python -m pytest services/zoe-data/tests/test_voice_activity_frames.py -v
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import json

import routers.voice_tts as v


def _sentinel(payload) -> str:
    return "__TOOL__:" + json.dumps(payload)


# ── _voice_activity_frame: frame shape ──────────────────────────────────────

def test_start_sentinel_becomes_start_frame():
    frame = v._voice_activity_frame(_sentinel({"phase": "start", "id": "t1", "name": "calendar"}))
    assert frame == {"type": "activity", "phase": "start", "tool": "calendar"}


def test_result_sentinel_becomes_result_frame():
    frame = v._voice_activity_frame(
        _sentinel({"phase": "result", "id": "t1", "name": "lists", "result": "3 items"})
    )
    assert frame == {"type": "activity", "phase": "result", "tool": "lists"}


def test_frame_carries_only_type_phase_tool_keys():
    frame = v._voice_activity_frame(
        _sentinel({"phase": "start", "id": "t1", "name": "calendar", "args": {"date": "2026-07-07"}})
    )
    assert set(frame.keys()) == {"type", "phase", "tool"}


# ── no args/result payload leakage ──────────────────────────────────────────

def test_args_and_results_never_cross_the_wire():
    secret_args = {"query": "jason private appointment"}
    secret_result = "Dr. Smith at 3pm about the thing"
    names: dict = {}
    start = v._voice_activity_frame(
        _sentinel({"phase": "start", "id": "t1", "name": "calendar", "args": secret_args}), names
    )
    result = v._voice_activity_frame(
        _sentinel({"phase": "result", "id": "t1", "result": secret_result}), names
    )
    for frame in (start, result):
        blob = json.dumps(frame)
        assert "private" not in blob
        assert "Dr. Smith" not in blob
        assert set(frame.keys()) == {"type", "phase", "tool"}


def test_args_phase_is_dropped_entirely():
    frame = v._voice_activity_frame(
        _sentinel({"phase": "args", "id": "t1", "name": "calendar", "args": {"q": "x"}})
    )
    assert frame is None


# ── result name resolution via the per-turn id→name map ─────────────────────

def test_result_without_name_resolves_via_tool_names_map():
    names: dict = {}
    v._voice_activity_frame(_sentinel({"phase": "start", "id": "t9", "name": "weather"}), names)
    frame = v._voice_activity_frame(_sentinel({"phase": "result", "id": "t9"}), names)
    assert frame == {"type": "activity", "phase": "result", "tool": "weather"}


def test_result_without_name_and_no_map_still_emits_frame():
    frame = v._voice_activity_frame(_sentinel({"phase": "result", "id": "t9"}))
    assert frame == {"type": "activity", "phase": "result", "tool": ""}


# ── malformed / non-tool sentinels ──────────────────────────────────────────

def test_malformed_sentinel_json_is_dropped():
    assert v._voice_activity_frame("__TOOL__:{not json") is None


def test_non_dict_sentinel_payload_is_dropped():
    assert v._voice_activity_frame('__TOOL__:["a","b"]') is None


def test_unknown_phase_is_dropped():
    assert v._voice_activity_frame(_sentinel({"phase": "banana", "id": "t1", "name": "x"})) is None


def test_start_without_name_is_dropped():
    assert v._voice_activity_frame(_sentinel({"phase": "start", "id": "t1"})) is None


def test_thinking_marker_is_not_an_activity_frame():
    assert v._voice_activity_frame("__THINKING__:pondering") is None


def test_plain_text_is_not_an_activity_frame():
    assert v._voice_activity_frame("Sure, checking your calendar now.") is None


# ── _forward_voice_activity: the ws-lane seam (mock websocket callback) ─────

class _MockWebSocket:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, frame: dict) -> None:
        if self.fail:
            raise RuntimeError("socket gone")
        self.sent.append(frame)


async def test_tool_sentinel_is_consumed_and_forwarded():
    ws = _MockWebSocket()
    names: dict = {}
    consumed = await v._forward_voice_activity(
        _sentinel({"phase": "start", "id": "t1", "name": "calendar", "args": {"q": "x"}}),
        ws.send_json,
        names,
    )
    assert consumed is True
    assert ws.sent == [{"type": "activity", "phase": "start", "tool": "calendar"}]


async def test_full_turn_start_then_result_frames_in_order():
    ws = _MockWebSocket()
    names: dict = {}
    deltas = [
        _sentinel({"phase": "start", "id": "t1", "name": "lists"}),
        _sentinel({"phase": "args", "id": "t1", "args": {"list": "shopping"}}),
        "You have three items. ",
        _sentinel({"phase": "result", "id": "t1", "result": "milk, eggs, bread"}),
    ]
    spoken = []
    for delta in deltas:
        if await v._forward_voice_activity(delta, ws.send_json, names):
            continue
        spoken.append(delta)
    assert ws.sent == [
        {"type": "activity", "phase": "start", "tool": "lists"},
        {"type": "activity", "phase": "result", "tool": "lists"},
    ]
    assert spoken == ["You have three items. "]  # only real speech reaches TTS


async def test_thinking_marker_consumed_but_not_forwarded():
    ws = _MockWebSocket()
    consumed = await v._forward_voice_activity("__THINKING__:hmm", ws.send_json, {})
    assert consumed is True
    assert ws.sent == []


async def test_malformed_sentinel_consumed_but_not_forwarded():
    ws = _MockWebSocket()
    consumed = await v._forward_voice_activity("__TOOL__:{broken", ws.send_json, {})
    assert consumed is True   # still never spoken
    assert ws.sent == []


async def test_plain_speech_is_not_consumed():
    ws = _MockWebSocket()
    consumed = await v._forward_voice_activity("Hello there!", ws.send_json, {})
    assert consumed is False
    assert ws.sent == []


async def test_send_failure_never_breaks_the_turn():
    ws = _MockWebSocket(fail=True)
    consumed = await v._forward_voice_activity(
        _sentinel({"phase": "start", "id": "t1", "name": "weather"}), ws.send_json, {}
    )
    assert consumed is True   # sentinel still filtered even when the send dies
