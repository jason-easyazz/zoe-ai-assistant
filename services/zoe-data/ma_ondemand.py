"""ma_ondemand — start Music Assistant on demand (flag-dark: ``ZOE_MA_IDLE_REAP``).

The START half of the Music Assistant idle reap (owner decision Q2, 2026-10-04:
"stop Music Assistant when idle, start it on demand"). The STOP half is the
``scripts/maintenance/ma_idle_reap.py`` timer. Same pattern as the LiveKit
on-demand lifecycle in ``routers/voice_livekit.py`` (docker start around a
usage signal), split across two processes because the reaper must keep working
while zoe-data is down or restarting.

Contract:
  - Flag off (the default) → ``ensure_running()`` returns True immediately and
    touches NOTHING (no docker, no files): byte-identical behaviour.
  - Flag on → ``music_service`` calls ``ensure_running()`` before a WAKE command
    (an explicit user intent: search, play, browse, connect a source). Read
    polls — the panel's 5 s now-playing refresh, the listening-journal observer
    — never wake MA, or the reap would be undone within seconds of happening.
  - ``ensure_running`` writes two stamps the reaper reads: ``activity`` (last
    music touch) and ``inflight`` (a request is starting MA right now), and
    holds the shared ``lock`` (``flock``) while it starts the container so the
    reaper can never ``docker stop`` between ``docker start`` and HTTP-up.
  - MA answers ``/info`` ~3.5 s after ``docker start`` (measured 2026-09-27 on
    this box); ``ZOE_MA_START_TIMEOUT_S`` (default 25) bounds the wait. The
    docker healthcheck is NOT the signal — its first tick can be 30 s out.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

import async_subprocess

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "on", "yes")
# If any MA call answered this recently, skip the /info probe on a wake — unless
# a request has FAILED since (note_seen_down) or the reaper's `stopped` stamp is
# newer than that answer: the panel's 5 s poll can leave a "seen up" that
# describes the container the reaper stopped a moment later.
_RECENT_UP_S = 30.0
# 0.0 (or None) = NO cached answer. Never compare the sentinel against the clock:
# time.monotonic() counts from boot, so within the first 30 s of uptime
# `monotonic() - 0.0 < _RECENT_UP_S` reads an EMPTY cache as "answered just now".
_last_seen_up: Optional[float] = 0.0        # monotonic, for the TTL
_last_seen_up_wall: Optional[float] = 0.0   # wall clock, compared against the stamp mtime
_lock: Optional[asyncio.Lock] = None


def enabled() -> bool:
    return os.environ.get("ZOE_MA_IDLE_REAP", "0").strip().lower() in _TRUTHY


def container_name() -> str:
    return os.environ.get("ZOE_MA_CONTAINER", "zoe-music-assistant").strip() or "zoe-music-assistant"


def state_dir() -> Path:
    return Path(os.environ.get("ZOE_MA_REAP_STATE_DIR", "~/.cache/zoe/ma-reap")).expanduser()


def _start_timeout_s() -> float:
    try:
        return float(os.environ.get("ZOE_MA_START_TIMEOUT_S", "25"))
    except (TypeError, ValueError):
        return 25.0


def _ma_url() -> str:
    return os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095").rstrip("/")


def touch(name: str) -> None:
    """Refresh a stamp file's mtime (best-effort; the reaper reads mtimes)."""
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.touch(exist_ok=True)
        os.utime(p, None)
    except OSError as exc:
        logger.debug("MA_REAP stamp %s failed: %s", name, exc)


def note_activity() -> None:
    """A music WRITE happened (play/transport/queue) — the reaper's idle clock resets."""
    if enabled():
        touch("activity")


def note_seen_up() -> None:
    global _last_seen_up, _last_seen_up_wall
    _last_seen_up = time.monotonic()
    _last_seen_up_wall = time.time()


def note_seen_down() -> None:
    """A request to MA failed — the cached "seen up" is stale; probe next time."""
    global _last_seen_up, _last_seen_up_wall
    _last_seen_up = 0.0
    _last_seen_up_wall = 0.0


def _recently_up() -> bool:
    """A cached "MA answered" that is < 30 s old and not contradicted by the
    reaper's `stopped` stamp. An EMPTY cache (0.0/None, the boot value and what
    note_seen_down leaves) is never "recent" — the TTL only applies to a real
    stamp, or the first wake within 30 s of host boot would skip the probe AND
    the `docker start` and fail (Codex P2)."""
    if not _last_seen_up or not _last_seen_up_wall:
        return False
    if time.monotonic() - _last_seen_up >= _RECENT_UP_S:
        return False
    try:  # the reaper touches `stopped` as it stops MA; newer than our answer = stale
        return (state_dir() / "stopped").stat().st_mtime < _last_seen_up_wall
    except OSError:
        return True


async def _docker_cmd(*args: str, timeout: float = 20.0) -> tuple[int, str]:
    """Run `docker <args>` OFF the event-loop thread; (returncode, combined output).

    Never raises. Goes through async_subprocess.run_to_completion — the whole
    fork+exec+communicate+kill happens in a worker thread — because a fork on
    FastAPI's loop thread can deadlock pre-exec and freeze every endpoint
    (services/zoe-data/AGENTS.md "Background loops must not fork on the event
    loop thread"; the 2026-06-29 outage). `timeout` bounds the CHILD; the pool
    wait is bounded separately (queue_timeout, short — a wake is latency-bound)."""
    try:
        res = await async_subprocess.run_to_completion(
            ["docker", *args], timeout=timeout, merge_stderr=True, queue_timeout=10.0)
    except subprocess.TimeoutExpired:  # incl. QueueTimeout (never started)
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001 — docker missing, shutting down, ...
        return 127, str(exc)
    return res.returncode or 0, (res.stdout or b"").decode(errors="replace").strip()


async def _container_running() -> bool:
    rc, out = await _docker_cmd("inspect", "-f", "{{.State.Running}}", container_name(), timeout=10)
    return rc == 0 and out.strip() == "true"


async def _http_up(timeout: float = 1.5) -> bool:
    """MA's unauthenticated /info answers 200 → the server is serving."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            return (await c.get(f"{_ma_url()}/info")).status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def _wait_http(timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if await _http_up():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.5)


def _flock(fh, flags: int) -> None:
    fcntl.flock(fh, flags)


@contextlib.asynccontextmanager
async def _file_lock():
    """The lock the reaper holds across stamp+`docker stop`. Blocking acquire,
    off the loop thread. Best-effort: if the file cannot be opened the wake
    proceeds unlocked (the inflight stamp still covers it)."""
    fh = None
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        fh = open(state_dir() / "lock", "a+")
        await asyncio.to_thread(_flock, fh, fcntl.LOCK_EX)
    except OSError as exc:
        logger.debug("MA_REAP lock unavailable: %s", exc)
    try:
        yield
    finally:
        if fh is not None:
            with contextlib.suppress(OSError):
                _flock(fh, fcntl.LOCK_UN)
                fh.close()


async def ensure_running(timeout_s: Optional[float] = None) -> bool:
    """Start the MA container if it is stopped and wait until HTTP is up.

    Returns True when MA is (now) serving, False when it could not be started
    within the bound. Flag off → True, nothing touched.

    EVERY decision — the cached "seen up", the /info probe and the start — runs
    under the shared file lock. The reaper takes that same lock for its
    stamp+stop, so a wake that arrives mid-stop waits, then re-reads the
    `stopped` stamp and probes instead of returning a cached answer that
    described the container the reaper just stopped."""
    global _lock
    if not enabled():
        return True
    touch("activity")
    touch("inflight")
    t0 = time.monotonic()
    started = False
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock, _file_lock():
        if _recently_up():
            return True
        if await _http_up():
            note_seen_up()
            return True
        if not await _container_running():
            logger.info("MA_REAP start container=%s", container_name())
            rc, out = await _docker_cmd("start", container_name())
            if rc != 0:
                logger.warning("MA_REAP start failed rc=%s out=%s", rc, out[:200])
                return False
            started = True
        ok = await _wait_http(_start_timeout_s() if timeout_s is None else timeout_s)
        if ok:
            note_seen_up()
            with contextlib.suppress(OSError):
                (state_dir() / "stopped").unlink()
    latency_ms = int((time.monotonic() - t0) * 1000)
    touch("inflight")
    if ok:
        logger.info("MA_REAP start latency_ms=%d started=%s", latency_ms, started)
    else:
        logger.warning("MA_REAP start timeout latency_ms=%d (MA not serving yet)", latency_ms)
    return ok
