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
import logging
import os
import subprocess
import threading
import time
from typing import Mapping, Sequence

_log = logging.getLogger(__name__)

# Shared, small pool used ONLY for the quick blocking fork+exec (subprocess.Popen).
# Long blocking waits do NOT go here — see AsyncPipeProcess.wait() and _RUN_POOL —
# so a stuck process can't hold a spawn slot for its whole lifetime and starve new
# fork+execs.
_SPAWN_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="zoe-async-spawn"
)

# Separate, wider pool for run_to_completion(): that call holds its worker for the
# child's WHOLE lifetime, which for the background Hermes lane is up to
# HERMES_BACKGROUND_TIMEOUT_S (900s default). Sharing _SPAWN_POOL would let four
# concurrent background tasks occupy every spawn slot and stall unrelated chat/voice
# fork+execs behind them for 15 minutes.
_RUN_POOL_WIDTH = 16
_RUN_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=_RUN_POOL_WIDTH, thread_name_prefix="zoe-async-run"
)

# Default cap on how long a caller waits for a free _RUN_POOL worker. Bounds the
# queue explicitly instead of letting the executor absorb it invisibly; callers
# with their own latency budget pass queue_timeout=.
_QUEUE_WAIT_S = float(os.environ.get("ZOE_SUBPROCESS_QUEUE_WAIT_S", "30"))

# Slack past a child's own timeout before we conclude the WORKER (not the child)
# is wedged. subprocess.run kills the child at `timeout`, so exceeding this means
# the thread itself is stuck — see the backstop in run_to_completion.
_QUEUE_GRACE_S = 5.0

# Permits mirroring _RUN_POOL's width, so "acquired" means "a worker is free".
#
# A THREADING semaphore, not an asyncio one, and deliberately NOT per-loop: the
# pool it guards is global, so per-loop permits would hand each loop a full set
# over the same 16 workers — two loops could admit 32 jobs, and the second loop's
# `timeout + grace` backstop would start ticking while its child was still stuck
# behind the first loop's work. (asyncio.Semaphore binds to the loop that first
# awaits it, which is what pushed the earlier version per-loop; a threading
# semaphore is loop-agnostic and counts globally, which is what this needs.)
_RUN_SLOTS = threading.BoundedSemaphore(_RUN_POOL_WIDTH)
# Poll interval while waiting for a permit. Non-blocking acquire + await keeps
# the wait off the event-loop thread without needing a loop-bound primitive.
_SLOT_POLL_S = 0.05


class QueueTimeout(subprocess.TimeoutExpired):
    """Gave up waiting for a worker — the child NEVER STARTED.

    A distinct type rather than a flag or a magic timeout value, because callers
    must be able to tell "never ran" from "ran too long": they warrant different
    log messages and different recovery (retry later vs. investigate a hang).
    Discriminating on the `.timeout` value instead is silently wrong whenever a
    caller's queue and runtime budgets happen to coincide.

    Subclasses `subprocess.TimeoutExpired` so every existing
    `except subprocess.TimeoutExpired` handler keeps working unchanged.
    """


async def _acquire_slot(timeout: float) -> bool:
    """Wait up to `timeout` for a global run-pool permit. Never blocks the loop."""
    deadline = time.monotonic() + timeout
    while True:
        if _RUN_SLOTS.acquire(blocking=False):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(min(_SLOT_POLL_S, max(0.0, deadline - time.monotonic())))


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
        # Default executor, NOT _SPAWN_POOL: popen.wait() blocks for the process's
        # whole remaining lifetime, so parking it in the small spawn pool could
        # starve new fork+execs.
        return await loop.run_in_executor(None, self._popen.wait)


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

    # The child is alive now. If wiring the pipe transports fails (fd exhaustion,
    # OOM, or a loop-shutdown/cancellation race), the caller never gets a handle
    # to clean up — so kill+reap the child here rather than orphan it.
    try:
        # connect_read_pipe/connect_write_pipe wrap the already-open fds — no fork.
        # No explicit loop= args: this coroutine runs on the loop these objects
        # will use, and the loop parameter was removed from asyncio's high-level
        # APIs in 3.10; the constructors bind the running loop themselves.
        reader = asyncio.StreamReader()
        read_protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: read_protocol, popen.stdout)

        write_transport, write_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, popen.stdin
        )
        writer = asyncio.StreamWriter(write_transport, write_protocol, reader, loop)
    except BaseException:  # incl. CancelledError — never leak the child
        with contextlib.suppress(ProcessLookupError):
            popen.kill()
        with contextlib.suppress(Exception):
            await loop.run_in_executor(_SPAWN_POOL, popen.wait)
        raise

    return AsyncPipeProcess(popen, writer, reader)


async def run_to_completion(
    cmd: Sequence[str],
    *,
    cwd: "str | None" = None,
    env: "Mapping[str, str] | None" = None,
    timeout: "float | None" = None,
    merge_stderr: bool = False,
    queue_timeout: "float | None" = None,
) -> "subprocess.CompletedProcess[bytes]":
    """Run a subprocess to completion OFF the event-loop thread.

    The entire spawn + communicate + timeout + child-kill happens inside a
    worker thread via `subprocess.run`, so nothing forks on the loop. Raises
    `subprocess.TimeoutExpired` (the child is killed) on timeout; returns a
    `CompletedProcess` with `.returncode`, `.stdout`, `.stderr` (bytes).

    `merge_stderr=True` interleaves stderr into stdout (`stderr=STDOUT`), for
    callers that previously spawned with `stderr=asyncio.subprocess.STDOUT`;
    `.stderr` is then `None`.

    Waiting and running are SEPARATE budgets, because conflating them breaks one
    caller or the other:

    * `timeout` is how long the CHILD may run, measured from its actual start.
      Charging queue time against it would silently shorten real work — the
      background Hermes lane asks for 900s of `hermes`, not "900s from whenever
      I asked", and under contention it would have been killed early.
    * `queue_timeout` is how long to wait for a free worker before giving up
      (default `_QUEUE_WAIT_S`). Without it, queue time is unbounded and a
      latency-sensitive caller blocks for minutes behind long background work.

    Worst case is therefore `queue_timeout + timeout`, both explicit. Giving up
    while queued raises `QueueTimeout` before anything forks, so no child is
    orphaned and callers can distinguish "never started" from "ran too long" by
    TYPE — it subclasses `subprocess.TimeoutExpired`, so existing handlers are
    unaffected.
    """
    loop = asyncio.get_running_loop()
    if queue_timeout is None:
        queue_timeout = _QUEUE_WAIT_S

    def _blocking_run() -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(
            list(cmd),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            timeout=timeout,
        )

    # Bound the WAIT explicitly rather than letting the executor queue absorb it
    # invisibly. The semaphore mirrors the pool width, so holding a permit means
    # a worker is available — the job starts immediately once acquired and gets
    # its full `timeout`.
    if not await _acquire_slot(queue_timeout):
        _log.warning(
            "run_to_completion gave up queueing after %.1fs (pool saturated, %d workers): %s",
            queue_timeout, _RUN_POOL_WIDTH, cmd[0] if cmd else "?",
        )
        raise QueueTimeout(list(cmd), queue_timeout)

    # _RUN_POOL, NOT _SPAWN_POOL: this worker is held for the child's whole
    # lifetime, so parking it in the small spawn pool would starve new fork+execs.
    fut = loop.run_in_executor(_RUN_POOL, _blocking_run)
    released = False
    try:
        # shield on BOTH paths. Cancelling the caller cancels the Future but not
        # the thread, which still owns a live child — and an unshielded cancel
        # fires the done-callback immediately, handing a permit back while the
        # worker is still busy and over-admitting into a saturated pool. Shielded,
        # the permit returns only when the thread genuinely finishes.
        if timeout is None:
            result = await asyncio.shield(fut)
        else:
            result = await asyncio.wait_for(
                asyncio.shield(fut), timeout=timeout + _QUEUE_GRACE_S
            )
        released = True
        _RUN_SLOTS.release()
        return result
    except asyncio.TimeoutError:
        # subprocess.run's own timeout should already have killed the child, so
        # reaching here means the WORKER is wedged — e.g. a child stuck in
        # uninterruptible sleep, which no in-process mechanism can reclaim. Hand
        # the caller its TimeoutExpired but keep the permit held until the thread
        # actually finishes, so the semaphore keeps reflecting real capacity
        # rather than over-admitting into a pool that has no free worker.
        fut.add_done_callback(lambda _f: _RUN_SLOTS.release())
        released = True
        _log.error(
            "run_to_completion worker wedged past %.1fs+%.1fs grace; permit held "
            "until it exits (pool capacity reduced): %s",
            timeout, _QUEUE_GRACE_S, cmd[0] if cmd else "?",
        )
        raise subprocess.TimeoutExpired(list(cmd), timeout) from None
    except BaseException:
        # Includes CancelledError. Cancelling the CALLER does not stop the
        # worker thread — it still owns a live child — so releasing the permit
        # here would over-admit into a pool with no free worker, the same
        # accounting error as the wedged case. Hand the permit back only when
        # the thread actually finishes.
        if not released:
            fut.add_done_callback(lambda _f: _RUN_SLOTS.release())
        raise
