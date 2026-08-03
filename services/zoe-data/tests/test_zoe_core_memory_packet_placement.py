"""Where the core lane's memory packet rides, and that it rides exactly once.

`services/zoe-core/extensions/memory.ts` used to compose the
`/api/memories/for-prompt` packet onto the SYSTEM PROMPT every turn. Because the
packet is keyed on the user's message it changed on essentially every turn, so
the reusable llama.cpp prefix ended at the last byte of SOUL.md and the whole
conversation was re-prefilled (`--cache-reuse` is off for Gemma's shared-KV + SWA
attention, so an exact common prefix is the only reuse path).

Directive + packet now ride in the user message, folded in by `_compose_message` —
past every cacheable byte, and still AHEAD of the user's own words. The system
prompt is left as pure SOUL.md.

Two rules there were established by measurement, not taste. Both were checked
against `test_zoe_core_client.py::test_tool_action_dispatches`, same box, 15 runs
each, against a 14/15 baseline:

* **The directive never appears without a packet.** Keeping it unconditional on
  the system prompt — the obvious way to make it static and therefore cacheable —
  scored 6/15. Told to lead with what it remembers when it remembers nothing, the
  4B brain chats instead of calling its tools. Dropping the directive from that
  same build restored 15/15, so the coupling is the cause, not the placement.
* **`message` stays last.** Pi's own tail slot (a `custom` message, which Pi
  appends AFTER the user message) scored 9/15 and costs the request its recency
  position for nothing the seam does not already give.

The final shape measures 14/15 — baseline.

ONE injection site, never two:

* `_worker_env` sets `ZOE_CORE_MEMORY_SEAM=1`, which makes `memory.ts` contribute
  nothing at all. Standalone runs (the `pi` CLI, `bench/`,
  `services/zoe-core/test`) leave it unset and keep fetching for themselves, so
  the agent still works with no zoe-data driving it.
* A caller-supplied `db_memory_context` suppresses the seam's own fetch, so
  `routers/chat.py`'s `ZOE_CHAT_INJECT_DB_MEMORY` escape hatch cannot double up.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from pathlib import Path

_ZOE_DATA = Path(__file__).resolve().parents[1]
_MEMORY_EXT = _ZOE_DATA.parent / "zoe-core" / "extensions" / "memory.ts"

_PACKET = "## What I know about you\n- the dog is named Pixel [mem:t1]"


def test_worker_env_claims_the_injection_for_the_seam():
    import zoe_core_client as zc

    env = zc._worker_env("family-admin")
    assert env["ZOE_CORE_MEMORY_SEAM"] == "1", (
        "without this the extension ALSO composes the packet onto the system "
        "prompt — the same facts twice, and the KV prefix broken again"
    )
    assert env["ZOE_CORE_USER_ID"] == "family-admin"


def test_packet_rides_in_the_user_message_and_the_request_stays_last():
    import zoe_core_client as zc

    composed = zc._compose_message(
        "add bread to my shopping list",
        history=[{"role": "user", "content": "hi"}],
        db_memory_context=None,
        portrait="Jason, lives in Geraldton",
        memory_packet=_PACKET,
    )
    # Directive + packet, exactly the bytes the extension used to put on the
    # system prompt, moved verbatim into the user message.
    assert f"{zc._MEMORY_USAGE_DIRECTIVE}\n\n{_PACKET}" in composed
    # The measured contract: nothing may come after the user's own words.
    assert composed.endswith("add bread to my shopping list")
    assert composed.index(_PACKET) < composed.index("add bread to my shopping list")


def test_the_directive_never_appears_without_a_packet():
    """MEASURED: an unconditional directive scored 6/15 vs a 14/15 baseline."""
    import zoe_core_client as zc

    assert zc._memory_block("") == ""
    assert zc._memory_block("   \n ") == ""
    assert zc._memory_block(_PACKET) == f"{zc._MEMORY_USAGE_DIRECTIVE}\n\n{_PACKET}"

    composed = zc._compose_message(
        "add bread to my shopping list",
        history=None,
        db_memory_context=None,
        portrait=None,
        memory_packet="",
    )
    assert zc._MEMORY_USAGE_DIRECTIVE not in composed
    assert composed == "add bread to my shopping list"


def test_the_two_copies_of_the_usage_directive_are_byte_identical():
    """The constant lives in both runtimes (different processes), so it can drift.

    Extracts the string literals from memory.ts's `MEMORY_USAGE_DIRECTIVE = [...]
    .join(" ")` and compares the joined result with the python copy.
    """
    import ast
    import re

    import zoe_core_client as zc

    source = _MEMORY_EXT.read_text(encoding="utf-8")
    match = re.search(
        r"MEMORY_USAGE_DIRECTIVE = \[(.*?)\]\.join\(\" \"\)", source, re.DOTALL
    )
    assert match, "could not find MEMORY_USAGE_DIRECTIVE in memory.ts — did it move?"
    # The literals are JS double-quoted strings with \" escapes; python's literal
    # syntax agrees for this subset, so ast.literal_eval parses them faithfully.
    parts = [
        ast.literal_eval(line.strip().rstrip(","))
        for line in match.group(1).strip().splitlines()
        if line.strip()
    ]
    assert len(parts) >= 4, f"parsed too few lines: {parts!r}"
    assert " ".join(parts) == zc._MEMORY_USAGE_DIRECTIVE, (
        "the TS and python copies of MEMORY_USAGE_DIRECTIVE have drifted — the "
        "brain would be told different things depending on which lane ran"
    )


def test_caller_supplied_context_wins_and_suppresses_the_packet():
    """`db_memory_context` and the fetched packet must never BOTH appear."""
    import zoe_core_client as zc

    composed = zc._compose_message(
        "what's on tomorrow",
        history=None,
        db_memory_context="- explicitly supplied fact",
        portrait=None,
        memory_packet=_PACKET,
    )
    assert "- explicitly supplied fact" in composed
    assert _PACKET not in composed, "double-injected: caller context AND the fetched packet"
    assert composed.count("[What you remember]") == 1


def test_no_packet_means_no_block():
    import zoe_core_client as zc

    composed = zc._compose_message(
        "what's on tomorrow",
        history=None,
        db_memory_context=None,
        portrait=None,
        memory_packet="",
    )
    assert composed == "what's on tomorrow"
    assert "[What you remember]" not in composed


@pytest.mark.asyncio
async def test_memory_packet_block_fails_closed_for_an_unknown_user():
    """No acting user → never another user's memories, and no import attempted."""
    import zoe_core_client as zc

    for user in ("", "   ", None):
        assert await zc._memory_packet_block("what's on tomorrow", user) == ""


@pytest.mark.asyncio
async def test_memory_packet_block_fails_open_when_recall_breaks(monkeypatch):
    """A memory failure must never break a turn — it degrades to no packet."""
    import sys
    import types

    import zoe_core_client as zc

    async def _boom(**_kwargs):
        raise RuntimeError("memory service is down")

    stub = types.ModuleType("routers.memories")
    stub.memory_for_prompt = _boom
    stub._PROMPT_PACKET_MAX_FACTS = 8
    monkeypatch.setitem(sys.modules, "routers.memories", stub)

    assert await zc._memory_packet_block("what's on tomorrow", "family-admin") == ""


@pytest.mark.asyncio
async def test_memory_packet_block_passes_an_explicit_limit(monkeypatch):
    """Called directly (not through FastAPI), an omitted `limit` would be the
    Query() descriptor object rather than an int — silently wrong."""
    import sys
    import types

    import zoe_core_client as zc

    seen: dict = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return {"packet": _PACKET}

    stub = types.ModuleType("routers.memories")
    stub.memory_for_prompt = _capture
    stub._PROMPT_PACKET_MAX_FACTS = 8
    monkeypatch.setitem(sys.modules, "routers.memories", stub)

    packet = await zc._memory_packet_block("x" * 900, "family-admin")
    assert packet == _PACKET
    assert isinstance(seen.get("limit"), int), "limit must be an explicit int"
    assert seen["limit"] == 8
    assert len(seen["message"]) == 500, "message slice must match the extension's 500 chars"
    assert seen["_"] is None, "the internal-token dependency must be bypassed in-process"


def test_memory_extension_hands_the_block_to_the_seam():
    """Structural tripwire that runs in the slim GitHub lane, where node may be
    too old to execute the TypeScript at all (the node suite proves behaviour)."""
    source = _MEMORY_EXT.read_text(encoding="utf-8")
    assert "seamOwnsPacket" in source, "the seam handoff is gone"
    assert 'MEMORY_SEAM_ENV = "ZOE_CORE_MEMORY_SEAM"' in source, (
        "the flag name drifted from the one _worker_env sets"
    )
    assert "if (seamOwnsPacket()) return;" in source, (
        "the extension composes something in seam mode again — anything it adds "
        "to the system prompt is a byte the KV prefix cannot reuse"
    )
