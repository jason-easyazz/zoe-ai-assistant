"""Spawn subprocesses WITHOUT forking on the asyncio event-loop thread.

`asyncio.create_subprocess_exec` performs the fork+exec on the running loop
thread. On this service that has repeatedly deadlocked/stalled the whole FastAPI
process (see services/zoe-data/AGENTS.md "Background loops must not fork on the
event loop thread"; PR #947's multi-day outage; PR #975). This module centralises
the two safe patterns so callers never fork on the loop:

* `run_to_completion(...)` — for run-to-completion CLIs: does the whole
  spawn+communicate+timeout+kill inside a worker thread via `subprocess.run`.
* `spawn_pipe_process(...)` — for long-lived RPC processes we stream to/from:
  does the blocking fork+exec in a worker thread, then wraps the already-open
  pipe fds in asyncio's low-level pipe transports (`connect_read_pipe` /
  `connect_write_pipe` only wrap existing fds — no fork), exposing the familiar
  async stdin/stdout interface via `AsyncPipeProcess`.

NOTE: `zoe_core_client.py` carries a specialised private copy of the
`spawn_pipe_process` logic (its `_spawn_pi_process`, added by PR #975 on the hot
brain path). Migrating it onto this shared helper is a deliberate follow-up kept
out of this change to avoid touching the freshly-landed brain-spawn code.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import subprocess
from typing import Mapping, Sequence

# Shared, small: fork+exec is quick; this only bounds concurrent spawns, not the
# lifetime of the spawned processes.
_SPAWN_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="zoe-async-spawn"
)


class AsyncPipeProcess:
    """Async-stream wrapper around a `subprocess.Popen` spawned off the loop.

    Exposes the subset of `asyncio.subprocess.Process` callers rely on: `.stdin`
    (StreamWriter), `.stdout` (StreamReader), `.returncode`, `.terminate()`,
    `.kill()`, `.wait()`.
    """

    def __init__(
        self,
        popen: subprocess.Popen,
        stdin: "asyncio.StreamWriter | None",
        stdout: "asyncio.StreamReader | None",
    ) -> None:
        self._popen = popen
        self.stdin = stdin
        self.stdout = stdout

    @property
    def returncode(self) -> "int | None":
        return self._popen.poll()

    def terminate(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._popen.terminate()

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._popen.kill()

    async def wait(self) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_SPAWN_POOL, self._popen.wait)


async def spawn_pipe_process(
    cmd: Sequence[str],
    *,
    cwd: "str | None" = None,
    env: "Mapping[str, str] | None" = None,
) -> AsyncPipeProcess:
    """Fork+exec a long-lived RPC subprocess OFF the event-loop thread.

    stdin/stdout are pipes exposed as asyncio streams; stderr is discarded
    (mirrors the RPC callers, which only stream stdout events).
    """
    loop = asyncio.get_running_loop()

    def _blocking_popen() -> subprocess.Popen:
        return subprocess.Popen(
            list(cmd),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env) if env is not None else None,
        )

    popen = await loop.run_in_executor(_SPAWN_POOL, _blocking_popen)

    # connect_read_pipe/connect_write_pipe wrap the already-open fds — no fork.
    # No explicit loop= args: this coroutine runs on the loop these objects will
    # use, and the loop parameter was removed from asyncio's high-level APIs in
    # 3.10; the constructors bind the running loop themselves.
    reader = asyncio.StreamReader()
    read_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: read_protocol, popen.stdout)

    write_transport, write_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, popen.stdin
    )
    writer = asyncio.StreamWriter(write_transport, write_protocol, reader, loop)

    return AsyncPipeProcess(popen, writer, reader)


async def run_to_completion(
    cmd: Sequence[str],
    *,
    cwd: "str | None" = None,
    env: "Mapping[str, str] | None" = None,
    timeout: "float | None" = None,
) -> "subprocess.CompletedProcess[bytes]":
    """Run a subprocess to completion OFF the event-loop thread.

    The entire spawn + communicate + timeout + child-kill happens inside a
    worker thread via `subprocess.run`, so nothing forks on the loop. Raises
    `subprocess.TimeoutExpired` (the child is killed) on timeout; returns a
    `CompletedProcess` with `.returncode`, `.stdout`, `.stderr` (bytes).
    """
    loop = asyncio.get_running_loop()

    def _blocking_run() -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(
            list(cmd),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )

    return await loop.run_in_executor(_SPAWN_POOL, _blocking_run)
