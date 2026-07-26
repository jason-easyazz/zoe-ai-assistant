"""Smokes for async_subprocess — the off-event-loop spawn helpers.

Mirrors the intent behind PR #975's _spawn_pi_process tests: prove the helpers
round-trip against a REAL subprocess (fork+exec happens in the thread pool, not
on the loop) and that timeouts/exit codes propagate.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import subprocess
import sys

import pytest

from async_subprocess import (
    AsyncPipeProcess,
    run_to_completion,
    spawn_pipe_process,
)

# A tiny stdin->stdout RPC-ish echo: uppercases each line, flushes immediately.
_ECHO_UPPER = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    sys.stdout.write(line.upper())\n"
    "    sys.stdout.flush()\n"
)


@pytest.mark.asyncio
async def test_spawn_pipe_process_round_trips_streaming():
    proc = await spawn_pipe_process([sys.executable, "-c", _ECHO_UPPER])
    try:
        assert isinstance(proc, AsyncPipeProcess)
        proc.stdin.write(b"hello\n")
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        assert line == b"HELLO\n"
        # second turn on the same long-lived process
        proc.stdin.write(b"world\n")
        await proc.stdin.drain()
        assert (await proc.stdout.readline()) == b"WORLD\n"
        assert proc.returncode is None  # still alive
    finally:
        proc.terminate()
        await proc.wait()
    assert proc.returncode is not None  # exited after terminate


@pytest.mark.asyncio
async def test_spawn_pipe_process_env_and_cwd(tmp_path):
    script = "import os, sys; sys.stdout.write(os.getcwd()+'\\n'); sys.stdout.write(os.environ.get('ZTEST','')+'\\n'); sys.stdout.flush()"
    proc = await spawn_pipe_process(
        [sys.executable, "-c", script], cwd=str(tmp_path), env={"ZTEST": "on"}
    )
    try:
        cwd_line = (await proc.stdout.readline()).decode().strip()
        env_line = (await proc.stdout.readline()).decode().strip()
        # macOS/Linux may resolve symlinks in tmp; compare basenames to be safe.
        assert cwd_line.endswith(tmp_path.name)
        assert env_line == "on"
    finally:
        proc.terminate()
        await proc.wait()


@pytest.mark.asyncio
async def test_spawn_pipe_process_kills_child_if_wiring_fails(monkeypatch):
    """If pipe-transport wiring raises after the child is alive, the child must
    be killed+reaped, not orphaned (Greptile P1 on #987)."""
    import async_subprocess as mod

    created: list = []
    real_popen = subprocess.Popen

    def _spy_popen(*a, **k):
        p = real_popen(*a, **k)
        created.append(p)
        return p

    monkeypatch.setattr(mod.subprocess, "Popen", _spy_popen)

    async def _boom(*a, **k):
        raise RuntimeError("connect_read_pipe failed")

    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio.get_running_loop(), "connect_read_pipe", _boom)

    with pytest.raises(RuntimeError):
        await mod.spawn_pipe_process([sys.executable, "-c", "import time; time.sleep(30)"])

    assert created, "child should have been spawned before the failure"
    assert created[0].poll() is not None, "orphaned child — should have been killed+reaped"


@pytest.mark.asyncio
async def test_run_to_completion_returns_rc_and_streams():
    completed = await run_to_completion(
        [sys.executable, "-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)"]
    )
    assert completed.returncode == 3
    assert completed.stdout == b"out"
    assert completed.stderr == b"err"


@pytest.mark.asyncio
async def test_run_to_completion_times_out_and_kills_child():
    with pytest.raises(subprocess.TimeoutExpired):
        await run_to_completion(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.5,
        )


# ── spawn-pool starvation guard ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_long_run_to_completion_does_not_starve_spawn_pool():
    """A long run_to_completion must not hold a _SPAWN_POOL slot.

    The background Hermes lane runs run_to_completion() with a 900s timeout. If
    those shared the 4-worker _SPAWN_POOL, four concurrent background tasks would
    block every unrelated chat/voice fork+exec behind them. Occupy the whole
    _RUN_POOL-bound path with sleepers and prove a spawn still goes through.
    """
    import asyncio

    import async_subprocess

    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    # More concurrent long runs than _SPAWN_POOL has workers.
    n = async_subprocess._SPAWN_POOL._max_workers + 2
    long_runs = [
        asyncio.create_task(run_to_completion(sleeper, timeout=30)) for _ in range(n)
    ]
    try:
        # Let them all get scheduled into their pool.
        await asyncio.sleep(0.5)
        # A fresh fork+exec must still complete promptly, not queue behind them.
        proc = await asyncio.wait_for(
            run_to_completion([sys.executable, "-c", "print('ok')"], timeout=10),
            timeout=10,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == b"ok"
        # And the dedicated pipe-spawn path must be unblocked too.
        piped = await asyncio.wait_for(
            spawn_pipe_process([sys.executable, "-c", _ECHO_UPPER]), timeout=10
        )
        piped.stdin.write(b"hi\n")
        await piped.stdin.drain()
        assert (await asyncio.wait_for(piped.stdout.readline(), timeout=10)) == b"HI\n"
        piped.kill()
        await piped.wait()
    finally:
        for t in long_runs:
            t.cancel()
        await asyncio.gather(*long_runs, return_exceptions=True)


def test_run_to_completion_uses_its_own_pool():
    """Guard the invariant directly: the long-run path is not the spawn pool."""
    import async_subprocess

    assert async_subprocess._RUN_POOL is not async_subprocess._SPAWN_POOL
    assert (
        async_subprocess._RUN_POOL._max_workers
        > async_subprocess._SPAWN_POOL._max_workers
    )


@pytest.mark.asyncio
async def test_timeout_bounds_queue_wait_not_just_child_runtime():
    """A saturated pool must not blow past the caller's timeout budget.

    subprocess.run(timeout=) only counts the CHILD's runtime, so a call queued
    behind long-running work could block far past what the caller asked for.
    Saturate _RUN_POOL with sleepers, then assert a short-timeout call gives up
    close to its own budget instead of waiting for a worker to free up.
    """
    import asyncio
    import time

    import async_subprocess

    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    hogs = [
        asyncio.create_task(run_to_completion(sleeper, timeout=60))
        for _ in range(async_subprocess._RUN_POOL._max_workers + 2)
    ]
    try:
        await asyncio.sleep(0.5)  # let them occupy every worker
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            await run_to_completion([sys.executable, "-c", "print('hi')"], timeout=1)
        elapsed = time.monotonic() - started
        # Budget is 1s + the queue grace; anything near 60s means the outer
        # bound is missing and we waited for a worker instead.
        assert elapsed < 1 + async_subprocess._QUEUE_GRACE_S + 5, (
            f"waited {elapsed:.1f}s for a 1s-timeout call — queue time is unbounded"
        )
    finally:
        for t in hogs:
            t.cancel()
        await asyncio.gather(*hogs, return_exceptions=True)
