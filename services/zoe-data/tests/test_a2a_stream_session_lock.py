"""The per-session lock is what makes the seam's turn-count invariant TRUE.

`services/zoe-core/extensions/abilities.ts` (`currentTurnIsReplayed`) rolls the
disclosure clock back by one when the replayed history ends on a user turn,
because `chat_stream_generator` persists the current user row
(`routers/chat.py:1617`) BEFORE it loads the window it replays (the
`ORDER BY created_at DESC LIMIT 12` at `routers/chat.py:2303-2309`). That makes
the current turn the last replayed entry, so it is credited once instead of
twice — without the roll-back every seeded domain decays a turn early.

The ordering is a property of ONE generator run. It survives a second turn on
the same session only if that turn cannot write rows in between. Interleaved:

    persist(B) → persist(A) → A replies, persists its assistant row → load(B)

B's replayed window now ends on an ASSISTANT row. The roll-back is positional and
cannot see past it (correctly — nothing distinguishes that from a history that
legitimately stops at a reply), so B's turn is counted twice.

`/api/chat/` never had this hole: its stream is wrapped in `_get_session_lock`.
The **A2A** stream endpoint (`routers/system.py::a2a_task_stream`) did — it called
`chat_stream_generator` directly, and its `session_id` is CALLER-supplied, so two
A2A agents (or one retrying agent) can name the same session deliberately. Both
callers now enter through `routers.chat.locked_chat_stream`.

These tests drive the real endpoint with a fake generator that records the two
ordered touches the invariant depends on. Deterministic: an `asyncio.Event` stall
and event-loop yields, never a timed sleep.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio

_A2A_USER = {"user_id": "a2a-agent", "role": "agent", "username": "a2a:tester"}


def _modules():
    chat = pytest.importorskip("routers.chat")
    system = pytest.importorskip("routers.system")
    return chat, system


class _Ledger:
    """Stands in for `chat_stream_generator`, recording only what the invariant needs.

    Per run: append the current user row (chat.py:1617), optionally stall, read the
    tail window (chat.py:2303-2309 — DESC LIMIT 12 reversed, i.e. the last 12 rows
    in insertion order), stream, then append the assistant row.
    """

    def __init__(self, stall_on: str) -> None:
        self.rows: list[tuple[str, str]] = []
        self.windows: dict[str, tuple[str, ...]] = {}
        self.entered: list[str] = []
        self.stall_on = stall_on
        self.gate = asyncio.Event()
        self.stalled = asyncio.Event()

    async def __call__(self, message, session_id, user, **kwargs):
        self.entered.append(message)
        self.rows.append(("user", message))
        if message == self.stall_on:
            self.stalled.set()
            await self.gate.wait()
        self.windows[message] = tuple(role for role, _ in self.rows[-12:])
        yield f"data: {message}\n\n"
        self.rows.append(("assistant", f"re: {message}"))


def _counts_current_turn_twice(window: tuple[str, ...]) -> bool:
    """`seedDisclosureState`'s roll-back condition, ported from abilities.ts.

    `state.turn = currentTurnIsReplayed(replayed) ? turns.length - 1 : turns.length`
    — the clock is rolled back only when the replayed block's LAST record is a user
    turn. Anything else credits the current turn as elapsed on top of the live pass
    that immediately follows it.
    """
    return not (window and window[-1] == "user")


async def _drain(response) -> list[str]:
    return [chunk async for chunk in response.body_iterator]


async def _run_pending() -> None:
    """Let every runnable callback run, with no wall-clock wait.

    `asyncio.sleep(0)` is a scheduling yield, not a delay: after this returns, a
    task that is not BLOCKED has made all the progress it can. So "A never entered"
    means A is genuinely waiting on the lock, not merely slower.
    """
    for _ in range(50):
        await asyncio.sleep(0)


def _request(system, task: str, session_id: str):
    return system._A2ATaskRequest(task=task, caller="tester", session_id=session_id)


async def test_a2a_stream_serialises_two_turns_on_one_session(monkeypatch):
    """The tail of each turn's replayed window is that turn's OWN user row."""
    chat, system = _modules()
    ledger = _Ledger(stall_on="B")
    monkeypatch.setattr(chat, "chat_stream_generator", ledger)
    session = "a2a-shared-session-locked"
    monkeypatch.setattr(chat, "_SESSION_LOCKS", {}, raising=False)

    resp_b = await system.a2a_task_stream(_request(system, "B", session), _A2A_USER)
    b_task = asyncio.create_task(_drain(resp_b))
    await ledger.stalled.wait()  # B holds the lock, its user row is persisted

    resp_a = await system.a2a_task_stream(_request(system, "A", session), _A2A_USER)
    a_task = asyncio.create_task(_drain(resp_a))
    await _run_pending()
    assert ledger.entered == ["B"], "A entered the generator while B held the session lock"

    ledger.gate.set()
    assert await b_task == ["data: B\n\n"]
    assert await a_task == ["data: A\n\n"]

    assert ledger.rows == [
        ("user", "B"),
        ("assistant", "re: B"),
        ("user", "A"),
        ("assistant", "re: A"),
    ], "the two turns interleaved their writes"
    for turn in ("A", "B"):
        assert ledger.windows[turn][-1] == "user", f"{turn}'s replayed window did not end on a user turn"
        assert not _counts_current_turn_twice(ledger.windows[turn]), f"{turn}'s turn was counted twice"


async def test_NEGATIVE_CONTROL_without_the_lock_the_interleave_double_counts(monkeypatch):
    """Revert the lock and the same schedule reproduces the mis-count.

    A fresh lock per acquisition is exactly what the pre-fix A2A path had: an
    acquire that never contends. Nothing else in the test changes.
    """
    chat, system = _modules()
    ledger = _Ledger(stall_on="B")
    monkeypatch.setattr(chat, "chat_stream_generator", ledger)
    monkeypatch.setattr(chat, "_get_session_lock", lambda session_id: asyncio.Lock())
    session = "a2a-shared-session-unlocked"

    resp_b = await system.a2a_task_stream(_request(system, "B", session), _A2A_USER)
    b_task = asyncio.create_task(_drain(resp_b))
    await ledger.stalled.wait()

    resp_a = await system.a2a_task_stream(_request(system, "A", session), _A2A_USER)
    a_task = asyncio.create_task(_drain(resp_a))
    await _run_pending()
    assert ledger.entered == ["B", "A"], "the control is not controlling — A never got in"
    assert await a_task == ["data: A\n\n"]  # A runs to completion INSIDE B's turn

    ledger.gate.set()
    await b_task

    assert ledger.rows == [
        ("user", "B"),
        ("user", "A"),
        ("assistant", "re: A"),
        ("assistant", "re: B"),
    ], "the control did not actually interleave the writes"
    assert ledger.windows["B"][-1] == "assistant", "B's window did not pick up the interleaved reply"
    assert _counts_current_turn_twice(ledger.windows["B"]), (
        "the interleaved window no longer demonstrates the double-count the lock prevents"
    )


async def test_a2a_stream_rejects_a_contending_turn_with_session_busy(monkeypatch):
    """Contention yields the SAME `session_busy` RUN_ERROR /api/chat/ emits — never a
    second, interleaved run. Timeout forced to 0 so the assertion costs no wall time."""
    chat, system = _modules()
    entered: list[str] = []

    async def _never_reached(message, session_id, user, **kwargs):
        entered.append(message)
        yield "data: unreachable\n\n"

    monkeypatch.setattr(chat, "chat_stream_generator", _never_reached)
    monkeypatch.setattr(chat, "_SESSION_LOCK_TIMEOUT_S", 0)
    monkeypatch.setattr(chat, "_SESSION_LOCKS", {}, raising=False)
    session = "a2a-shared-session-busy"

    held = chat._get_session_lock(session)
    await held.acquire()
    try:
        resp = await system.a2a_task_stream(_request(system, "A", session), _A2A_USER)
        chunks = await _drain(resp)
    finally:
        held.release()

    assert entered == [], "a contending A2A turn reached the generator anyway"
    assert any("session_busy" in chunk for chunk in chunks), chunks
