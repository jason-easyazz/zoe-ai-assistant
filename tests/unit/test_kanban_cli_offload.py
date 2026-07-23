"""Regression tests: kanban CLI spawns must never fork on the event loop thread.

On 2026-06-29 a `hermes kanban` fork issued by the Multica poll loop via
asyncio.create_subprocess_exec deadlocked post-fork/pre-exec (child stuck on an
atfork lock, parent blocked reading the exec-status pipe) ON the event loop
thread. The wedged loop stopped accepting connections, so every zoe-data
endpoint — /health and /api/memories/for-prompt included — timed out until a
restart. These tests prove the CLI runner now executes off-loop and that the
awaiting coroutine stays bounded even when the spawn itself never returns.
"""

import asyncio
import subprocess
import threading
import time

import pytest

import executors.kanban_adapter as ka

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe



async def test_worktree_command_runs_off_the_event_loop(monkeypatch, tmp_path):
    """subprocess.run (and therefore fork()) must execute on a worker thread."""
    loop_thread = threading.get_ident()
    seen = {}
    real_run = subprocess.run

    def recording_run(*args, **kwargs):
        seen["thread"] = threading.get_ident()
        return real_run(*args, **kwargs)

    monkeypatch.setattr(ka.subprocess, "run", recording_run)
    adapter = ka.KanbanAdapter()
    out = await adapter._run_worktree_command(["echo", "hello"], cwd=tmp_path)
    assert out == "hello"
    assert seen["thread"] != loop_thread


async def test_run_kanban_cli_plumbing(monkeypatch):
    """_run still returns stdout and passes board args through the new runner.

    Pins the CLI backend explicitly: this test exercises the `hermes kanban`
    CLI plumbing, and it used to get there via the default. The default is now
    `executor`, which dispatches to Multica's Postgres instead — so without the
    pin this reaches for a `multica` database that does not exist in CI.
    """
    monkeypatch.setenv("ZOE_KANBAN_BACKEND", "hermes")
    monkeypatch.setattr(ka, "hermes_bin", lambda: "echo")
    monkeypatch.setattr(ka, "_board", lambda: "board-x")
    adapter = ka.KanbanAdapter()
    out = await adapter._run(["list"])
    assert out == "kanban --board board-x list"


async def test_worktree_command_timeout_raises_kanban_error(tmp_path):
    """A CLI that outlives its budget is killed and surfaces as KanbanCLIError."""
    adapter = ka.KanbanAdapter()
    t0 = time.monotonic()
    with pytest.raises(ka.KanbanCLIError, match="timed out"):
        await adapter._run_worktree_command(["sleep", "5"], cwd=tmp_path, timeout=0.3)
    assert time.monotonic() - t0 < 4.0


async def test_wedged_spawn_does_not_block_the_loop(monkeypatch, tmp_path):
    """Simulate the live failure: the spawn call itself never returns.

    subprocess.run()'s own timeout cannot fire if fork wedges before Popen()
    returns, so the coroutine-side wait_for must bound the caller — and the
    event loop must keep scheduling other tasks the whole time.
    """
    release = threading.Event()

    def wedged_run(*args, **kwargs):  # ignores its timeout, like a pre-exec deadlock
        release.wait(10.0)
        raise subprocess.TimeoutExpired(cmd="wedged", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(ka.subprocess, "run", wedged_run)
    monkeypatch.setattr(ka, "_CLI_WAIT_GRACE_S", 0.2)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks += 1

    adapter = ka.KanbanAdapter()
    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    try:
        with pytest.raises(ka.KanbanCLIError, match="timed out"):
            await adapter._run_worktree_command(["true"], cwd=tmp_path, timeout=0.1)
        elapsed = time.monotonic() - t0
        ticks_during_wedge = ticks  # snapshot before the heartbeat finishes on its own
        await hb
    finally:
        release.set()  # let the wedged worker thread exit
    assert elapsed < 5.0  # bounded despite the wedge (timeout + grace, not forever)
    # The load-bearing liveness proof: the loop scheduled other tasks WHILE the
    # spawn was wedged (>=1 tick inside the ~0.3s window; conservative so CI
    # scheduling jitter can't flake it). ticks == 20 afterwards is deterministic
    # since hb is awaited to completion — it only sanity-checks nothing cancelled it.
    assert ticks_during_wedge > 0
    assert ticks == 20
