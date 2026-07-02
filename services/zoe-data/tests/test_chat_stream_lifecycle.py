"""Lifecycle tests for chat_stream_generator: agent-task cancellation on client
disconnect (P2-B) and partial-reply persistence on a mid-stream error (P3-A).

These drive the REAL `routers.chat.chat_stream_generator`, stubbing only the
heavy I/O around the streaming core (DB, memory, portrait) and the brain/agent
runners — so the try/finally cancellation wrapper and the error-path persist run
exactly as in production. Importing `routers.chat` pulls the service modules, so
this is a Jetson-tier suite (like test_chat_persistence_contract), not CI-slim.

    python -m pytest services/zoe-data/tests/test_chat_stream_lifecycle.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from routers import chat as chat_router


def _quiet_common_gates(monkeypatch):
    """Neutralise everything between the generator's entry and the no-match
    brain/openclaw branch so the test reaches the code under test deterministically
    with no real DB / network / model I/O."""
    async def _anoop(*a, **k):
        return None

    # Persisted writes / bookkeeping the generator does up front and at the end.
    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", _anoop)
    monkeypatch.setattr(chat_router, "_record_run_state", _anoop)
    monkeypatch.setattr(chat_router, "_persist_ag_ui_run", _anoop)
    monkeypatch.setattr(chat_router, "_persist_memory_candidates", _anoop)
    monkeypatch.setattr(chat_router, "_check_frustration", lambda *a, **k: None)

    # Route straight to the no-match branch: no guarded-auto gate, tools disabled
    # (so the fast path / pi-hybrid / intent detection are all skipped), plain chat
    # (not research), no WhatsApp flow.
    monkeypatch.setattr(chat_router, "_GUARDED_AUTO", False)
    monkeypatch.setattr(chat_router, "_ALL_TOOLS_ENABLED", False)
    monkeypatch.setattr(chat_router, "_WHATSAPP_FLOW_ENABLED", False)
    monkeypatch.setattr(chat_router, "classify_query", lambda *a, **k: "chat")


def _user():
    return {"user_id": "jason", "role": "admin", "username": "jason"}


# ── P2-B: SSE client disconnect cancels the orphaned agent task ────────────────


@pytest.mark.asyncio
async def test_client_disconnect_cancels_agent_task(monkeypatch):
    """When the SSE generator is closed mid-flight (client disconnect), the
    long-running agent task must be cancelled — not left running orphaned holding
    the brain slot. Drives the no-match OpenClaw branch (run_openclaw_agent)."""
    _quiet_common_gates(monkeypatch)
    # OpenClaw branch (not the local-brain streaming branch).
    monkeypatch.setattr(chat_router, "_USE_LOCAL_BRAIN", False)

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(chat_router, "_safe_load_portrait", _empty)
    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", _empty)
    monkeypatch.setattr(chat_router, "_build_memory_context", _empty)
    monkeypatch.setattr(chat_router, "openclaw_user_message", lambda intent, msg: msg)

    started = asyncio.Event()
    cancelled = {"v": False}

    async def fake_agent(*a, **k):
        started.set()
        try:
            await asyncio.sleep(100)  # multi-minute browser/agent run
        except asyncio.CancelledError:
            cancelled["v"] = True
            raise
        return "should never finish"

    monkeypatch.setattr(chat_router, "run_openclaw_agent", fake_agent)

    # Fast heartbeats so the generator parks at the outer `yield hb` (task created
    # and running) within milliseconds instead of the production 4s cadence.
    async def fast_heartbeats(emit, task, *, phase_label="OpenClaw"):
        while not task.done():
            await asyncio.sleep(0.01)
            if task.done():
                break
            yield emit(chat_router.CustomEvent(name="zoe.run_log",
                                               value={"heartbeat": True, "message": "tick"}))

    monkeypatch.setattr(chat_router, "_iter_openclaw_heartbeats", fast_heartbeats)

    gen = chat_router.chat_stream_generator("tell me a story", "sess-disc", _user())

    # Pull events until the agent task has actually started running (and we've
    # surfaced a heartbeat, so the generator is suspended inside the try block).
    for _ in range(200):
        await gen.__anext__()
        if started.is_set():
            break
    assert started.is_set(), "agent task never started — test didn't reach the branch"

    # Simulate the client going away: closing the generator throws GeneratorExit.
    await gen.aclose()

    assert cancelled["v"] is True, "agent task was NOT cancelled on disconnect (orphaned)"


# ── P3-A: a mid-stream brain error persists the partial the user already saw ───


@pytest.mark.asyncio
async def test_midstream_error_persists_partial_flagged_truncated(monkeypatch):
    """If the brain stream raises after tokens were already streamed to the user,
    the error path must persist that partial (flagged truncated) so the next
    turn's history matches the screen — instead of silently dropping it."""
    _quiet_common_gates(monkeypatch)
    # Local-brain streaming branch.
    monkeypatch.setattr(chat_router, "_USE_LOCAL_BRAIN", True)

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", _empty)

    # History load uses `async for db in get_db()` — give it a trivial fake.
    class _Rows:
        async def fetchall(self):
            return []

    class _Db:
        async def execute(self, *a, **k):
            return _Rows()

    async def _fake_get_db():
        yield _Db()

    monkeypatch.setattr(chat_router, "get_db", _fake_get_db)

    # Capture every persisted chat message.
    saves: list[dict] = []

    async def fake_save(session_id, role, content, user_id=None, *, truncated=False):
        saves.append({"session_id": session_id, "role": role, "content": content,
                      "user_id": user_id, "truncated": truncated})

    monkeypatch.setattr(chat_router, "_save_chat_message", fake_save)

    # Brain streams two tokens to the user, then blows up mid-generation.
    async def fake_brain_streaming(*a, **k):
        yield "Hello"
        yield " world"
        raise RuntimeError("brain blew up mid-stream")

    monkeypatch.setattr(chat_router, "_brain_streaming", fake_brain_streaming)

    gen = chat_router.chat_stream_generator("tell me a story", "sess-err", _user())
    # The top-level except swallows the error and finishes the run, so draining
    # the generator completes normally.
    async for _ in gen:
        pass

    assistant_saves = [s for s in saves if s["role"] == "assistant"]
    assert assistant_saves, f"partial was dropped on error; saves={saves}"
    partial = assistant_saves[-1]
    assert partial["content"] == "Hello world", partial
    assert partial["truncated"] is True, "partial must be flagged truncated"


# ── Persisted flag must reflect save REALITY, not save scheduling ─────────────
# The normal-path save can fail (DB down, pool exhausted). If the reply were
# marked persisted the moment the save was *initiated*, a later error in the same
# stream would skip the truncated-partial fallback and the reply the user saw
# would silently vanish from history.


def _stub_brain_branch(monkeypatch):
    """Common setup driving the local-brain streaming branch with a fake brain."""
    _quiet_common_gates(monkeypatch)
    monkeypatch.setattr(chat_router, "_USE_LOCAL_BRAIN", True)

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(chat_router, "_mempalace_load_user_facts", _empty)

    class _Rows:
        async def fetchall(self):
            return []

    class _Db:
        async def execute(self, *a, **k):
            return _Rows()

    async def _fake_get_db():
        yield _Db()

    monkeypatch.setattr(chat_router, "get_db", _fake_get_db)

    async def fake_brain_streaming(*a, **k):
        yield "Hello"
        yield " world"

    monkeypatch.setattr(chat_router, "_brain_streaming", fake_brain_streaming)


@pytest.mark.asyncio
async def test_failed_normal_save_leaves_reply_recoverable(monkeypatch):
    """If the normal-path save FAILS, the reply must NOT be marked persisted:
    a later error in the same stream must still trigger the truncated-partial
    fallback so the reply the user saw isn't silently lost."""
    _stub_brain_branch(monkeypatch)

    saves: list[dict] = []

    async def failing_save(session_id, role, content, user_id=None, *, truncated=False):
        saves.append({"role": role, "content": content, "truncated": truncated})
        return False  # save did NOT land (e.g. DB failure — _save_chat_message swallows it)

    monkeypatch.setattr(chat_router, "_save_chat_message", failing_save)

    # Blow up AFTER the normal save path ran (completion bookkeeping), so the
    # error handler runs with the persisted flag already decided.
    async def exploding_record(run_id, session_id, user_id, *, status, **k):
        if status == "completed":
            raise RuntimeError("post-save failure")

    monkeypatch.setattr(chat_router, "_record_run_state", exploding_record)

    gen = chat_router.chat_stream_generator("tell me a story", "sess-savefail", _user())
    async for _ in gen:
        pass

    truncated = [s for s in saves if s["role"] == "assistant" and s["truncated"]]
    assert truncated, (
        f"reply marked persisted despite failed save — truncated fallback skipped; saves={saves}"
    )
    assert truncated[-1]["content"] == "Hello world", truncated


@pytest.mark.asyncio
async def test_successful_normal_save_skips_truncated_fallback(monkeypatch):
    """Complement: when the normal-path save SUCCEEDS, a later error must not
    re-persist the reply as a truncated duplicate."""
    _stub_brain_branch(monkeypatch)

    saves: list[dict] = []

    async def ok_save(session_id, role, content, user_id=None, *, truncated=False):
        saves.append({"role": role, "content": content, "truncated": truncated})
        return True

    monkeypatch.setattr(chat_router, "_save_chat_message", ok_save)

    async def exploding_record(run_id, session_id, user_id, *, status, **k):
        if status == "completed":
            raise RuntimeError("post-save failure")

    monkeypatch.setattr(chat_router, "_record_run_state", exploding_record)

    gen = chat_router.chat_stream_generator("tell me a story", "sess-saveok", _user())
    async for _ in gen:
        pass

    assistant_saves = [s for s in saves if s["role"] == "assistant"]
    assert [s for s in assistant_saves if not s["truncated"]], f"normal save missing; saves={saves}"
    assert not [s for s in assistant_saves if s["truncated"]], (
        f"duplicate truncated save despite successful persist; saves={saves}"
    )


@pytest.mark.asyncio
async def test_save_chat_message_returns_false_on_db_failure(monkeypatch):
    """Unit guard: a DB failure inside _save_chat_message is swallowed (non-fatal
    to the stream) but reported as False so callers know the row never landed."""
    import sys
    import types

    class _Ctx:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    fake_db_pool = sys.modules.get("db_pool") or types.ModuleType("db_pool")
    monkeypatch.setattr(fake_db_pool, "get_db_ctx", lambda: _Ctx(), raising=False)
    monkeypatch.setitem(sys.modules, "db_pool", fake_db_pool)

    ok = await chat_router._save_chat_message("sess-1", "assistant", "reply", user_id="jason")
    assert ok is False
    # Empty content: nothing persisted → also False.
    assert await chat_router._save_chat_message("sess-1", "assistant", "   ") is False


@pytest.mark.asyncio
async def test_save_chat_message_records_truncated_in_metadata(monkeypatch):
    """Unit guard: the truncated flag lands in chat_messages.metadata alongside
    the user id (P3-A persistence carries the flag through to storage)."""
    import json
    import sys
    import types

    executed: list = []

    class _Db:
        async def execute(self, sql, params=()):
            executed.append((sql, params))

        async def execute_fetchall(self, sql, params=()):
            return [{"title": "New Chat"}]

        async def commit(self):
            pass

    class _Ctx:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *a):
            return False

    fake_db_pool = sys.modules.get("db_pool") or types.ModuleType("db_pool")
    monkeypatch.setattr(fake_db_pool, "get_db_ctx", lambda: _Ctx(), raising=False)
    monkeypatch.setitem(sys.modules, "db_pool", fake_db_pool)

    await chat_router._save_chat_message(
        "sess-1", "assistant", "partial reply", user_id="jason", truncated=True
    )

    insert = next((sql, p) for sql, p in executed if "INSERT INTO chat_messages" in sql)
    metadata = json.loads(insert[1][4])
    assert metadata == {"user_id": "jason", "truncated": True}
