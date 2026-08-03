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
"Once" means one FETCH SITE, not one block. The packet is emitted independently of
a caller-supplied `db_memory_context`: the voice path always supplies one, and only
the endpoint folds in pending-contact offers. Making the two mutually exclusive was
a regression caught in review — see
`test_voice_style_turn_still_reaches_the_for_prompt_endpoint`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio
import json
import types
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
    assert zc._memory_block(_PACKET) == (
        f"{zc._MEMORY_BLOCK_OPEN}\n{zc._MEMORY_USAGE_DIRECTIVE}\n\n"
        f"{_PACKET}\n{zc._MEMORY_BLOCK_CLOSE}"
    )

    composed = zc._compose_message(
        "add bread to my shopping list",
        history=None,
        db_memory_context=None,
        portrait=None,
        memory_packet="",
    )
    assert zc._MEMORY_USAGE_DIRECTIVE not in composed
    assert composed == "add bread to my shopping list"


def test_the_memory_block_is_delimited_on_both_sides():
    """memory.ts strips superseded blocks by these exact markers.

    Pi retains every user message it is sent, so an undelimited block accumulated
    one memory snapshot per turn. A drift here is silent: the strip would match
    nothing and every superseded snapshot would stay in the request.
    """
    import re

    import zoe_core_client as zc

    source = (_ZOE_DATA.parent / "zoe-core" / "extensions" / "memory.ts").read_text(
        encoding="utf-8"
    )
    for name, expected in (
        ("MEMORY_BLOCK_OPEN", zc._MEMORY_BLOCK_OPEN),
        ("MEMORY_BLOCK_CLOSE", zc._MEMORY_BLOCK_CLOSE),
    ):
        match = re.search(rf'{name} = "([^"]+)"', source)
        assert match, f"could not find {name} in memory.ts — did it move?"
        assert match.group(1) == expected, f"{name} drifted between the two runtimes"


def test_composed_memory_block_is_wrapped_in_the_markers():
    import zoe_core_client as zc

    block = zc._memory_block(_PACKET)
    assert block.startswith(f"{zc._MEMORY_BLOCK_OPEN}\n{zc._MEMORY_USAGE_DIRECTIVE}")
    assert block.endswith(f"{_PACKET}\n{zc._MEMORY_BLOCK_CLOSE}")
    assert zc._memory_block("") == ""


def test_the_two_copies_of_the_utterance_marker_are_byte_identical():
    """abilities.ts splits the composed prompt on this exact string.

    A drift here is silent and total: the marker would never be found, the split
    would fall back to the whole prompt, and disclosure would go back to being
    re-armed by replayed history on every turn.
    """
    import re

    import zoe_core_client as zc

    source = (_ZOE_DATA.parent / "zoe-core" / "extensions" / "abilities.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(r'UTTERANCE_MARKER = "([^"]+)"', source)
    assert match, "could not find UTTERANCE_MARKER in abilities.ts — did it move?"
    assert match.group(1) == zc._UTTERANCE_MARKER


def test_composed_prompt_puts_the_marker_before_the_utterance():
    import zoe_core_client as zc

    composed = zc._compose_message(
        "what time is it",
        history=[{"role": "user", "content": "add milk to my shopping list"}],
        db_memory_context=None,
        portrait="Jason",
        memory_packet=_PACKET,
    )
    assert composed.endswith(f"{zc._UTTERANCE_MARKER}\nwhat time is it")
    # The keyword that must NOT reach disclosure is present but ahead of the marker.
    assert "shopping list" in composed.split(zc._UTTERANCE_MARKER)[0]


def test_bare_message_is_unchanged_when_there_is_no_context():
    """No context blocks → no marker: the prompt stays byte-for-byte the message."""
    import zoe_core_client as zc

    assert (
        zc._compose_message(
            "what time is it",
            history=None,
            db_memory_context=None,
            portrait=None,
            memory_packet="",
        )
        == "what time is it"
    )


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


def test_caller_context_and_the_packet_are_independent():
    """Both blocks appear — the packet is NOT suppressed by db_memory_context.

    REGRESSION GUARD (found in review on #1615). Making them mutually exclusive
    looks like a tidy once-not-twice rule and silently breaks voice: see
    test_voice_style_turn_still_reaches_the_for_prompt_endpoint below.
    """
    import zoe_core_client as zc

    composed = zc._compose_message(
        "what's on tomorrow",
        history=None,
        db_memory_context="- explicitly supplied fact",
        portrait=None,
        memory_packet=_PACKET,
    )
    assert "- explicitly supplied fact" in composed
    assert _PACKET in composed, (
        "the for-prompt packet was suppressed by db_memory_context — that drops "
        "pending-contact offers, which only the endpoint produces"
    )


@pytest.mark.asyncio
async def test_voice_style_turn_still_reaches_the_for_prompt_endpoint(monkeypatch):
    """A voice turn supplies db_memory_context AND must still hit the endpoint.

    `_fold_pending_contact_offers` (routers/memories.py, flag
    ZOE_PERSON_SUGGEST_ENABLED) runs ONLY inside `memory_for_prompt`. The voice
    recall block (`_voice_brain_memory` → `_voice_recall_packet`) is built from
    MemoryService.search / zoe_memory_compose and never calls it, so skipping the
    fetch when db_memory_context is present drops every pending "Would you like me
    to add <name> as a contact?" offer from the voice path.
    """
    import zoe_core_client as zc

    calls: list[str] = []

    async def _fake_packet(message: str, user_id: str) -> str:
        calls.append(user_id)
        return _PACKET

    composed_seen: list[str] = []

    async def _fake_stream(self, message, *, timeout_s):
        # `message` is a compose FACTORY now — awaited inside the per-session lock
        # so a stalled recall can't be overtaken (see the ordering test below).
        composed_seen.append(message if isinstance(message, str) else await message())
        yield "ok"

    monkeypatch.setattr(zc, "_memory_packet_block", _fake_packet)
    monkeypatch.setattr(zc._ZoeCoreWorker, "stream", _fake_stream)

    chunks = [
        c
        async for c in zc.run_zoe_core_streaming(
            "add bread to my list",
            "voice-session",
            "family-admin",
            # Exactly what routers/voice_tts.py passes on every voice turn.
            db_memory_context="[What you remember]\n- likes oat milk",
            portrait="Jason",
            voice_mode=True,
        )
    ]

    assert chunks == ["ok"]
    assert calls == ["family-admin"], "the voice turn never called the for-prompt endpoint"
    assert len(composed_seen) == 1
    composed = composed_seen[0]
    assert "- likes oat milk" in composed, "voice recall block lost"
    assert _PACKET in composed, "for-prompt packet lost on the voice path"
    assert composed.endswith("add bread to my list")


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 2.0),
        ("", 2.0),
        ("   ", 2.0),
        ("5000", 5.0),
        ("1500.5", 1.5005),
        ("garbage", 2.0),
        ("2s", 2.0),
        ("0", 2.0),
        ("-100", 2.0),
        # float() parses these cleanly and they pass `> 0`, but
        # asyncio.wait_for(timeout=inf) waits forever — a typo would hang the lane.
        ("inf", 2.0),
        ("Infinity", 2.0),
        ("1e10000", 2.0),
        ("nan", 2.0),
    ],
)
def test_packet_timeout_falls_back_instead_of_raising(monkeypatch, raw, expected):
    """An operator typo must degrade to the default, never break the lane.

    A module-level `float(os.environ[...])` raised ValueError at IMPORT, before
    _memory_packet_block's fail-open handling could apply — so every core-brain
    request died instead of one turn continuing without memory.
    """
    import zoe_core_client as zc

    monkeypatch.delenv("ZOE_CORE_MEMORY_TIMEOUT_MS", raising=False)
    if raw is not None:
        monkeypatch.setenv("ZOE_CORE_MEMORY_TIMEOUT_MS", raw)
    assert zc._packet_timeout_s() == pytest.approx(expected)


def test_no_module_level_float_of_the_timeout_env():
    """The parse must stay inside the function — an import-time float() is the bug."""
    source = (_ZOE_DATA / "zoe_core_client.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "ZOE_CORE_MEMORY_TIMEOUT_MS" not in stripped:
            continue
        assert not line[:1].strip() or "def " in stripped, (
            f"module-level read of the timeout env — parse it lazily instead: {line!r}"
        )


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


class _FakeStdin:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def write(self, data: bytes) -> None:
        self._sink.append(json.loads(data.decode())["message"])

    async def drain(self) -> None:
        return None


async def _yield_until(predicate, *, steps: int = 200) -> bool:
    """Advance the event loop until `predicate()` holds. No sleeps — `sleep(0)`
    just yields to the scheduler, so this is deterministic rather than timed."""
    for _ in range(steps):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


@pytest.mark.asyncio
async def test_same_session_turns_keep_submission_order_when_recall_stalls():
    """A stalled recall on the FIRST turn must not let a later turn overtake it.

    Composition (which awaits the memory fetch) runs INSIDE the per-session lock.
    Awaiting it in the caller instead let request B — submitted second, with fast
    recall — reach the worker lock while A was still fetching, and B's turn would
    be sent to the brain first. Before the seam owned recall this could not happen:
    memory.ts fetched inside the Pi process, i.e. with the lock already held.
    """
    import zoe_core_client as zc

    sent: list[str] = []
    worker = zc._ZoeCoreWorker.__new__(zc._ZoeCoreWorker)
    worker._lock = asyncio.Lock()
    worker.last_used = 0.0
    worker.proc = types.SimpleNamespace(
        stdin=_FakeStdin(sent), stdout=object(), returncode=None
    )

    async def _ensure_started() -> None:
        return None

    async def _read_turn(request_id: str, timeout_s: float):
        yield "ok"

    worker._ensure_started = _ensure_started
    worker._read_turn = _read_turn

    a_compose_entered = asyncio.Event()
    release_a = asyncio.Event()

    async def _compose_a() -> str:
        a_compose_entered.set()
        await release_a.wait()  # the stalled recall
        return "A"

    async def _compose_b() -> str:
        return "B"

    async def _drain(compose) -> None:
        async for _ in worker.stream(compose, timeout_s=5.0):
            pass

    task_a = asyncio.ensure_future(_drain(_compose_a))
    assert await _yield_until(a_compose_entered.is_set), "A never started composing"

    # B is submitted SECOND, and its recall is instant. Let it run as far as it
    # can — queued on the lock if A holds it, all the way to the brain if not.
    task_b = asyncio.ensure_future(_drain(_compose_b))
    assert await _yield_until(lambda: bool(worker._lock._waiters) or bool(sent)), (
        "B never got anywhere — the test would prove nothing"
    )

    # Now release A's stalled recall and let both finish.
    release_a.set()
    await asyncio.gather(task_a, task_b)

    # THE ASSERTION: submission order, not recall-completion order.
    assert sent == ["A", "B"], f"same-session turns were reordered: {sent}"


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
