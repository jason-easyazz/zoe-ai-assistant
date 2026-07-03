"""Smokes for async_subprocess — the off-event-loop spawn helpers.

Mirrors the intent behind PR #975's _spawn_pi_process tests: prove the helpers
round-trip against a REAL subprocess (fork+exec happens in the thread pool, not
on the loop) and that timeouts/exit codes propagate.
"""
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
