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
import fcntl
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "on", "yes")
# If any MA call answered this recently, skip the /info probe on a wake.
_RECENT_UP_S = 30.0
_last_seen_up: float = 0.0
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
    global _last_seen_up
    _last_seen_up = time.monotonic()


async def _docker_cmd(*args: str, timeout: float = 20.0) -> tuple[int, str]:
    """Run `docker <args>` off the event loop; (returncode, combined output). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except Exception as exc:  # noqa: BLE001 — docker missing from PATH etc.
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return 124, "timeout"
    return proc.returncode or 0, (out or b"").decode(errors="replace").strip()


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


async def ensure_running(timeout_s: Optional[float] = None) -> bool:
    """Start the MA container if it is stopped and wait until HTTP is up.

    Returns True when MA is (now) serving, False when it could not be started
    within the bound. Flag off → True, nothing touched."""
    global _lock
    if not enabled():
        return True
    touch("activity")
    touch("inflight")
    if time.monotonic() - _last_seen_up < _RECENT_UP_S:
        return True
    if await _http_up():
        note_seen_up()
        return True
    t0 = time.monotonic()
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        lock_fh = None
        try:
            state_dir().mkdir(parents=True, exist_ok=True)
            lock_fh = open(state_dir() / "lock", "a+")
            await asyncio.to_thread(_flock, lock_fh, fcntl.LOCK_EX)
        except OSError as exc:  # no shared lock → still start; the inflight stamp covers us
            logger.debug("MA_REAP lock unavailable: %s", exc)
        try:
            if not await _container_running():
                logger.info("MA_REAP start container=%s", container_name())
                rc, out = await _docker_cmd("start", container_name())
                if rc != 0:
                    logger.warning("MA_REAP start failed rc=%s out=%s", rc, out[:200])
                    return False
            ok = await _wait_http(_start_timeout_s() if timeout_s is None else timeout_s)
        finally:
            if lock_fh is not None:
                try:
                    _flock(lock_fh, fcntl.LOCK_UN)
                    lock_fh.close()
                except OSError:
                    pass
    latency_ms = int((time.monotonic() - t0) * 1000)
    touch("inflight")
    if ok:
        note_seen_up()
        logger.info("MA_REAP start latency_ms=%d", latency_ms)
    else:
        logger.warning("MA_REAP start timeout latency_ms=%d (MA not serving yet)", latency_ms)
    return ok
