"""Two ways to drive the bridge without a model: a scripted agent and a recorded turn.

* ``ScriptedAgent``: a deterministic stand-in that emits the tool calls a real agent would for a
  few phrasings (including one a prompt-injected model might emit: a ``member`` argument).
  It has no model and proves the LOOP, not the model.
* ``replay_ndjson``: reads the sidecar's Seam-A stream (``services/zoe-data`` contract, see
  ``labs/flue-zoe-brain-2x/src/streaming.ts``): one JSON string per line, tool calls as
  ``__TOOL__:`` sentinels with ``phase`` start / args / result. Every ``args`` sentinel becomes
  one proposal. A ``result`` sentinel recorded from the OLD tools is ignored: the kernel's
  receipt summary is the only result the agent is ever given now.

Neither touches a store. Both end in ``BrainBridge.propose``.
"""
from __future__ import annotations

import json
import re

from .bridge import BrainBridge, Outcome, Turn

TOOL_PREFIX = "__TOOL__:"


def parse_stream(lines) -> list[tuple[str, dict]]:
    """(tool name, arguments) per ``args`` sentinel, in order. Text and thinking chunks are skipped."""
    calls = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        chunk = json.loads(line)
        if isinstance(chunk, str) and chunk.startswith(TOOL_PREFIX):
            frame = json.loads(chunk[len(TOOL_PREFIX):])
            if frame.get("phase") == "args":
                calls.append((frame["name"], frame.get("args") or {}))
    return calls


def replay_ndjson(bridge: BrainBridge, turn: Turn, lines) -> list[Outcome]:
    return [bridge.propose(turn, name, args) for name, args in parse_stream(lines)]


_PATTERNS = [
    (re.compile(r"add (?P<item>.+?) to (?:the |my )?(?P<list>\w+) list(?: for (?P<member>\w+))?$", re.I),
     "list_add"),
    (re.compile(r"remind me to (?P<note>.+?) in (?P<in_minutes>\d+) minutes?$", re.I), "remind_set"),
    (re.compile(r"set (?:my )?alarm (?:for|to) (?P<time>\d\d:\d\d)$", re.I), "alarm_set"),
]


class ScriptedAgent:
    def __init__(self, bridge: BrainBridge) -> None:
        self.bridge = bridge

    def calls(self, text: str) -> list[tuple[str, dict]]:
        for pattern, tool in _PATTERNS:
            m = pattern.match(text.strip())
            if m:
                args = {k: v for k, v in m.groupdict().items() if v is not None}
                if "in_minutes" in args:
                    args["in_minutes"] = int(args["in_minutes"])
                return [(tool, args)]
        return []

    def turn(self, turn: Turn, text: str) -> tuple[list[Outcome], str]:
        """Emit the calls, then answer from what the kernel said. No outcome: say so plainly."""
        outcomes = [self.bridge.propose(turn, name, args) for name, args in self.calls(text)]
        return outcomes, outcomes[0].say if outcomes else "I did not do anything."
