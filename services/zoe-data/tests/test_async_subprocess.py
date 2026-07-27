"""Smokes for async_subprocess — the off-event-loop spawn helpers.

Mirrors the intent behind PR #975's _spawn_pi_process tests: prove the helpers
round-trip against a REAL subprocess (fork+exec happens in the thread pool, not
on the loop) and that timeouts/exit codes propagate.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import contextlib
import subprocess
import sys
import threading

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


@pytest.fixture
def narrow_pool(monkeypatch):
    """Shrink _RUN_POOL to 2 workers for saturation tests.

    The invariants under test are about the pool being FULL, not about it being
    16 wide — and this suite runs on the box hosting the live brain, where a
    burst of concurrent children is a genuine hazard rather than a cost. Two
    workers reproduce saturation with a fraction of the processes.
    """
    import concurrent.futures

    import async_subprocess as mod

    narrow = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="zoe-test-run"
    )
    monkeypatch.setattr(mod, "_RUN_POOL", narrow)
    monkeypatch.setattr(mod, "_RUN_POOL_WIDTH", 2)
    monkeypatch.setattr(mod, "_RUN_SLOTS", threading.BoundedSemaphore(2))
    try:
        yield 2
    finally:
        narrow.shutdown(wait=False)


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

    # The sleepers must outlive the probe's deadline, or "didn't block" and
    # "blocked, but the blockers finished first" look identical and the test
    # passes even when run_to_completion IS parked in the spawn pool.
    _HOLD_S, _PROBE_DEADLINE_S = 6, 2.5
    sleeper = [sys.executable, "-c", f"import time; time.sleep({_HOLD_S})"]
    # More concurrent long runs than _SPAWN_POOL has workers.
    n = async_subprocess._SPAWN_POOL._max_workers + 1
    long_runs = [
        asyncio.create_task(run_to_completion(sleeper, timeout=_HOLD_S + 10))
        for _ in range(n)
    ]
    try:
        # Let them all get scheduled into their pool.
        await asyncio.sleep(0.5)
        # A fresh fork+exec must still complete promptly, not queue behind them.
        proc = await asyncio.wait_for(
            run_to_completion([sys.executable, "-c", "print('ok')"], timeout=10),
            timeout=_PROBE_DEADLINE_S,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == b"ok"
        # And the dedicated pipe-spawn path must be unblocked too.
        piped = await asyncio.wait_for(
            spawn_pipe_process([sys.executable, "-c", _ECHO_UPPER]),
            timeout=_PROBE_DEADLINE_S,
        )
        piped.stdin.write(b"hi\n")
        await piped.stdin.drain()
        assert (await asyncio.wait_for(piped.stdout.readline(), timeout=_PROBE_DEADLINE_S)) == b"HI\n"
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




# ── waiting and running are separate budgets ───────────────────────────────

@pytest.mark.asyncio
async def test_queue_wait_is_bounded_separately_from_runtime(narrow_pool):
    """A saturated pool must fail fast on the WAIT, without a child forking."""
    import asyncio

    import async_subprocess

    # Short-lived: cancelling a caller does NOT free its worker, so a long
    # sleeper would leak occupied permits into the next test.
    sleeper = [sys.executable, "-c", "import time; time.sleep(3)"]
    hogs = [
        asyncio.create_task(run_to_completion(sleeper, timeout=10))
        for _ in range(narrow_pool)
    ]
    try:
        await asyncio.sleep(0.5)  # every worker occupied
        spawned = []
        real_run = async_subprocess.subprocess.run

        def _spy(*a, **k):
            spawned.append(a[0])
            return real_run(*a, **k)

        async_subprocess.subprocess.run = _spy
        try:
            started = asyncio.get_running_loop().time()
            with pytest.raises(subprocess.TimeoutExpired):
                await run_to_completion(
                    [sys.executable, "-c", "print('x')"], timeout=900, queue_timeout=1
                )
            waited = asyncio.get_running_loop().time() - started
        finally:
            async_subprocess.subprocess.run = real_run
        # Gave up on the WAIT (~1s), not after the 900s runtime budget...
        assert waited < 5, f"queue wait not bounded: {waited:.1f}s"
        # ...and nothing forked, so no child was orphaned by giving up.
        assert spawned == []
    finally:
        # Let the children exit so their permits come back before the next test.
        await asyncio.gather(*hogs, return_exceptions=True)


@pytest.mark.asyncio
async def test_queue_time_does_not_shrink_the_child_budget(narrow_pool):
    """Queue time must NOT be charged against the child's runtime.

    The background Hermes lane asks for 900s of `hermes`, not '900s from
    whenever I asked'. Charging the wait against it would kill real work early
    under contention. Prove the child still gets its full budget after waiting.
    """
    import asyncio

    import async_subprocess

    # Occupy every worker briefly, so the job under test genuinely queues.
    blocker = [sys.executable, "-c", "import time; time.sleep(2)"]
    hogs = [
        asyncio.create_task(run_to_completion(blocker, timeout=10))
        for _ in range(narrow_pool)
    ]
    try:
        await asyncio.sleep(0.3)
        # 1.5s of runtime, queued behind ~2s of blockers. If the wait were
        # charged against it the child would be killed; it must succeed.
        proc = await run_to_completion(
            [sys.executable, "-c", "import time; time.sleep(1); print('survived')"],
            timeout=1.5,
            queue_timeout=20,
        )
        assert proc.stdout.strip() == b"survived"
    finally:
        await asyncio.gather(*hogs, return_exceptions=True)


def test_run_slots_are_global_not_per_loop(monkeypatch):
    """Permits must count across loops, because the pool they guard is global.

    A per-loop semaphore hands every loop a full set of permits over the SAME
    workers, so two loops can admit 2x the pool width. The second loop's
    timeout+grace backstop then starts ticking while its child is still stuck
    behind the first loop's work — it reports a wedged worker for a job that
    never got one.
    """
    import asyncio

    import async_subprocess as mod

    # Own semaphore, so the assertion doesn't depend on what the rest of the
    # suite happens to be holding.
    monkeypatch.setattr(mod, "_RUN_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(mod, "_RUN_POOL_WIDTH", 2)

    # Loop-agnostic: acquired from OUTSIDE any running loop.
    assert mod._RUN_SLOTS.acquire(blocking=False)
    assert mod._RUN_SLOTS.acquire(blocking=False)
    try:
        async def _probe():
            return await mod._acquire_slot(0.2)

        # A brand-new loop must still see the pool as full.
        assert asyncio.run(_probe()) is False, "a fresh loop got permits over a full pool"

        # And once a permit comes back, that same fresh-loop path succeeds —
        # proving the False above was exhaustion, not a broken acquire.
        mod._RUN_SLOTS.release()
        assert asyncio.run(_probe()) is True
    finally:
        with contextlib.suppress(ValueError):
            mod._RUN_SLOTS.release()
        with contextlib.suppress(ValueError):
            mod._RUN_SLOTS.release()


@pytest.mark.asyncio
async def test_cancelling_a_no_timeout_call_does_not_return_the_permit_early():
    """Cancelling the caller must not hand a permit back while the worker runs.

    Cancelling an unshielded `run_in_executor` future does NOT stop the thread —
    it still owns a live child — but it does fire the permit-release callback
    immediately, over-admitting into a pool with no free worker. The
    `timeout is None` path had this hole after the timeout path was shielded.
    """
    import asyncio

    import async_subprocess as mod

    monkey_sem = threading.BoundedSemaphore(1)
    orig = mod._RUN_SLOTS
    mod._RUN_SLOTS = monkey_sem
    try:
        task = asyncio.create_task(
            run_to_completion([sys.executable, "-c", "import time; time.sleep(3)"])
        )
        await asyncio.sleep(0.6)                      # let it acquire + start
        assert not monkey_sem.acquire(blocking=False), "permit not held while running"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)                      # child still running

        # The permit must STILL be held — the thread has not finished.
        assert not monkey_sem.acquire(blocking=False), (
            "permit released on cancel while the worker was still running"
        )
        # ...and it comes back once the child actually exits.
        for _ in range(60):
            if monkey_sem.acquire(blocking=False):
                monkey_sem.release()
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("permit never returned after the child exited")
    finally:
        mod._RUN_SLOTS = orig


def test_bad_env_value_does_not_crash_the_import():
    """A mistyped queue-wait env var must not take zoe-data down at startup.

    This module is imported during service startup, so a bare float() turns
    `ZOE_SUBPROCESS_QUEUE_WAIT_S=30s` into an unhandled ValueError and the whole
    API fails to boot over a typo.
    """
    import async_subprocess as mod

    assert mod._env_float("NOPE_UNSET", 12.5) == 12.5
    import os as _os
    _os.environ["ZTEST_QW"] = "30s"          # the typo
    try:
        assert mod._env_float("ZTEST_QW", 30.0) == 30.0
        _os.environ["ZTEST_QW"] = ""          # blank
        assert mod._env_float("ZTEST_QW", 30.0) == 30.0
        _os.environ["ZTEST_QW"] = "45"        # a good value still wins
        assert mod._env_float("ZTEST_QW", 30.0) == 45.0
    finally:
        _os.environ.pop("ZTEST_QW", None)


@pytest.mark.asyncio
async def test_permit_returns_if_the_executor_rejects_synchronously(monkeypatch):
    """A synchronous run_in_executor failure must not strand the permit.

    The permit is acquired BEFORE the submit. If the submit raises (pool shut
    down during teardown), there is no future to hang a release callback on, so
    an unguarded path silently shrinks pool capacity for the process's life.
    """
    import asyncio

    import async_subprocess as mod

    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(mod, "_RUN_SLOTS", sem)

    loop = asyncio.get_running_loop()
    def _boom(*a, **k):
        raise RuntimeError("cannot schedule new futures after shutdown")
    monkeypatch.setattr(loop, "run_in_executor", _boom)

    with pytest.raises(RuntimeError):
        await run_to_completion([sys.executable, "-c", "pass"], timeout=5)

    # Capacity must be intact, not permanently reduced.
    assert sem.acquire(blocking=False), "permit stranded by a synchronous submit failure"
    sem.release()


def test_queue_timeout_is_distinguishable_for_recovery_callers():
    """Callers that RECOVER after a timeout must be able to opt out on QueueTimeout.

    main.py's scheduled jobs run `docker rm -f` / `--recover` on TimeoutExpired,
    on the premise a child ran and was killed. QueueTimeout means NO child ever
    started — those handlers catch it first and skip recovery. This pins the
    ordering property that makes that possible: except QueueTimeout before
    except TimeoutExpired must win.
    """
    from async_subprocess import QueueTimeout

    caught = None
    try:
        raise QueueTimeout(["x"], 30)
    except QueueTimeout:
        caught = "queue"
    except subprocess.TimeoutExpired:
        caught = "child"
    assert caught == "queue"
