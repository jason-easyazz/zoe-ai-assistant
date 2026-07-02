#!/usr/bin/env python3
"""Unit coverage for the recall_memory event-stream scorer (LAB-ONLY, offline).

Proves the scorer counts a genuine recall_memory tool firing across the event
shapes Flue (or a drifted adapter) can emit, and never counts a non-recall event
or a mere text mention. No network — pure synthetic payloads.

Run standalone (no pytest needed):
    python3 parity/test_recall_reliability.py
Also pytest-discoverable (test_* functions).
"""
from __future__ import annotations

import sys

from recall_reliability import _iter_events, any_tool_fired, recall_fired


# ── recall_fired: genuine firings COUNT across shapes ────────────────────────

def test_list_tool_start_toolname():
    # The canonical Flue Node shape: a list with a `tool_start` carrying toolName.
    assert recall_fired([{"type": "tool_start", "toolName": "recall_memory"}]) is True


def test_list_terminal_tool_event():
    # The terminal `tool` event also carries toolName.
    assert recall_fired([{"type": "tool", "toolName": "recall_memory", "isError": False}]) is True


def test_object_wrapping_events_array():
    # History returned as an object wrapping an `events` array, not a bare list.
    hist = {"events": [{"type": "tool_start", "toolName": "recall_memory"}]}
    assert recall_fired(hist) is True


def test_object_wrapping_messages_array():
    hist = {"messages": [{"type": "tool", "toolName": "recall_memory"}]}
    assert recall_fired(hist) is True


def test_tool_call_type_with_name_field():
    # OpenAI-ish wire shape: type `tool_call`, name under `name`.
    assert recall_fired([{"type": "tool_call", "name": "recall_memory"}]) is True


def test_function_call_type_with_tool_name_field():
    assert recall_fired([{"type": "function_call", "tool_name": "recall_memory"}]) is True


def test_case_and_separator_insensitive_type():
    assert recall_fired([{"type": "Tool-Start", "toolName": "recall_memory"}]) is True
    assert recall_fired([{"type": "TOOL_CALL", "name": "RECALL_MEMORY"}]) is True


def test_counts_among_other_events():
    hist = [
        {"type": "run_start", "runId": "r1"},
        {"type": "turn_request", "turnId": "t1"},
        {"type": "tool_start", "toolName": "recall_memory"},
        {"type": "tool", "toolName": "recall_memory", "isError": False},
    ]
    assert recall_fired(hist) is True


# ── recall_fired: non-firings DO NOT count ───────────────────────────────────

def test_other_tool_does_not_count():
    assert recall_fired([{"type": "tool_start", "toolName": "get_time"}]) is False


def test_text_mention_does_not_count():
    # A reply/message that merely *mentions* recall_memory is not a tool firing.
    hist = [
        {"type": "text_delta", "text": "I would call recall_memory for that."},
        {"type": "message_end", "message": {"role": "assistant", "content": "recall_memory"}},
    ]
    assert recall_fired(hist) is False


def test_empty_and_unshaped_history():
    assert recall_fired([]) is False
    assert recall_fired({}) is False
    assert recall_fired({"error": "boom"}) is False  # object with no events/messages
    assert recall_fired(None) is False
    assert recall_fired("recall_memory") is False  # a bare string is not events


def test_tool_event_without_name_does_not_count():
    assert recall_fired([{"type": "tool_start"}]) is False


# ── any_tool_fired: collects real tool names across shapes ────────────────────

def test_any_tool_fired_collects_names():
    hist = [
        {"type": "tool_start", "toolName": "recall_memory"},
        {"type": "tool_call", "name": "get_time"},
        {"type": "text_delta", "text": "recall_memory mentioned"},
    ]
    assert any_tool_fired(hist) == {"recall_memory", "get_time"}


def test_any_tool_fired_object_shape():
    hist = {"events": [{"type": "function_call", "tool_name": "get_weather"}]}
    assert any_tool_fired(hist) == {"get_weather"}


def test_iter_events_skips_non_dicts():
    assert list(_iter_events([1, "x", {"type": "tool", "toolName": "recall_memory"}])) == [
        {"type": "tool", "toolName": "recall_memory"}
    ]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:  # noqa: PERF203
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
