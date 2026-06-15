"""Tests for ZOE-5799: faster-whisper startup warmup must not block the event loop.

These tests pin two non-blocking guarantees of ``warm_faster_whisper_worker``:

1. **Low-memory early return** — when available system memory is below the
   configured threshold, the warmup skips before touching the persistent
   worker (and therefore cannot stall startup while a model is being loaded).
2. **Sync I/O offload** — the synchronous temp-file creation and silence-wav
   write run in a worker thread via ``asyncio.to_thread`` rather than the
   event-loop thread, so a slow disk cannot stall zoe-data's :8000 bind.

The tests use the existing monkeypatch patterns from
``test_voice_transcribe.py`` so they stay CI-friendly.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Low-memory early return
# ---------------------------------------------------------------------------


def test_warmup_skips_when_available_memory_below_threshold(monkeypatch):
    from routers import voice_tts

    async def _unexpected_worker(path: str) -> str:
        raise AssertionError("worker should not be called under low memory")

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "1024")
    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: 256)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _unexpected_worker)

    assert asyncio.run(voice_tts.warm_faster_whisper_worker()) is False


def test_warmup_runs_when_available_memory_above_threshold(monkeypatch):
    from routers import voice_tts

    calls: list[str] = []

    async def _fake_worker(path: str) -> str:
        calls.append(path)
        return ""

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "512")
    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: 4096)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)

    assert asyncio.run(voice_tts.warm_faster_whisper_worker()) is True
    assert len(calls) == 1


def test_warmup_low_memory_guard_disabled_by_threshold_zero(monkeypatch):
    from routers import voice_tts

    calls: list[str] = []

    async def _fake_worker(path: str) -> str:
        calls.append(path)
        return ""

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "0")
    # Even with reported 0 MB the guard is disabled.
    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: 0)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)

    assert asyncio.run(voice_tts.warm_faster_whisper_worker()) is True
    assert len(calls) == 1


def test_warmup_low_memory_guard_falls_open_when_meminfo_unreadable(monkeypatch):
    from routers import voice_tts

    calls: list[str] = []

    async def _fake_worker(path: str) -> str:
        calls.append(path)
        return ""

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "1024")
    # Detector returns None on missing /proc/meminfo — guard must fall open.
    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: None)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)

    assert asyncio.run(voice_tts.warm_faster_whisper_worker()) is True
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# /proc/meminfo parser — direct unit tests
# ---------------------------------------------------------------------------


def test_available_memory_mb_parses_memavailable(tmp_path):
    from routers import voice_tts

    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          512000 kB\n"
        "Cached:          2048000 kB\n"
    )

    real_path = voice_tts.Path
    try:
        voice_tts.Path = lambda *_args, **_kw: fake_meminfo
        assert voice_tts._available_memory_mb() == 8000
    finally:
        voice_tts.Path = real_path


def test_available_memory_mb_falls_back_to_free_plus_buffers_cached(tmp_path):
    from routers import voice_tts

    fake_meminfo = tmp_path / "meminfo"
    # No MemAvailable line — must fall back to MemFree + Buffers + Cached.
    fake_meminfo.write_text(
        "MemTotal:       8192000 kB\n"
        "MemFree:        2048000 kB\n"
        "Buffers:         512000 kB\n"
        "Cached:         1024000 kB\n"
    )

    real_path = voice_tts.Path
    try:
        voice_tts.Path = lambda *_args, **_kw: fake_meminfo
        # (2_048_000 + 512_000 + 1_024_000) kB = 3_584_000 kB // 1024 = 3500 MB
        assert voice_tts._available_memory_mb() == 3500
    finally:
        voice_tts.Path = real_path


def test_should_skip_warmup_for_low_memory_returns_reason_string(monkeypatch):
    from routers import voice_tts

    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: 64)
    # Threshold read from env defaults to 1024 MB; report 64 MB → must skip.
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "1024")
    reason = voice_tts._should_skip_warmup_for_low_memory()
    assert reason is not None
    assert "64" in reason and "1024" in reason


def test_should_skip_warmup_for_low_memory_returns_none_when_healthy(monkeypatch):
    from routers import voice_tts

    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: 8192)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "1024")
    assert voice_tts._should_skip_warmup_for_low_memory() is None


def test_should_skip_warmup_for_low_memory_returns_none_when_unreadable(monkeypatch):
    from routers import voice_tts

    monkeypatch.setattr(voice_tts, "_available_memory_mb", lambda: None)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "1024")
    assert voice_tts._should_skip_warmup_for_low_memory() is None


# ---------------------------------------------------------------------------
# Sync I/O offload via asyncio.to_thread
# ---------------------------------------------------------------------------


def test_warmup_offloads_tempfile_create_and_wav_write_to_thread(monkeypatch):
    """The sync helpers must run on a non-main thread, proving the offload."""
    from routers import voice_tts

    main_thread_ident = threading.get_ident()
    observed_thread_idents: dict[str, int] = {}

    def _tracking_create() -> str:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            observed_thread_idents["create"] = threading.get_ident()
            return tmp.name

    def _tracking_write(path: str, **_kwargs) -> None:
        observed_thread_idents["write"] = threading.get_ident()

    async def _fake_worker(path: str) -> str:
        observed_thread_idents["worker"] = threading.get_ident()
        return ""

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "0")
    monkeypatch.setattr(voice_tts, "_create_warmup_wav_path", _tracking_create)
    monkeypatch.setattr(voice_tts, "_write_warmup_silence_wav", _tracking_write)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)

    # The actual asyncio.run thread is the main thread of the test, so any
    # helper executed inside asyncio.to_thread will be on a different ident.
    assert asyncio.run(voice_tts.warm_faster_whisper_worker()) is True
    assert observed_thread_idents.get("create") != main_thread_ident, (
        "tempfile creation must run in a worker thread, not the event loop"
    )
    assert observed_thread_idents.get("write") != main_thread_ident, (
        "silence-wav write must run in a worker thread, not the event loop"
    )


def test_warmup_does_not_block_event_loop_during_slow_io(monkeypatch):
    """A blocking tempfile create must not delay other event-loop tasks."""
    from routers import voice_tts

    loop_scheduled_at: list[float] = []

    def _slow_create() -> str:
        # Simulate slow disk I/O. If the warmup ran this on the event-loop
        # thread, the other coroutine below would be delayed past 0.3s.
        time.sleep(0.3)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            return tmp.name

    def _quick_write(path: str, **_kwargs) -> None:
        return None

    async def _fake_worker(path: str) -> str:
        return ""

    async def _ping_loop() -> None:
        # Should run nearly immediately because slow_create is offloaded.
        loop_scheduled_at.append(time.monotonic())

    async def _drive() -> float:
        task_a = asyncio.create_task(voice_tts.warm_faster_whisper_worker())
        # Schedule the ping at the same time — if warmup blocked the loop, the
        # ping would only get to run after the 0.3s sleep.
        await _ping_loop()
        await task_a
        return time.monotonic()

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "0")
    monkeypatch.setattr(voice_tts, "_create_warmup_wav_path", _slow_create)
    monkeypatch.setattr(voice_tts, "_write_warmup_silence_wav", _quick_write)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)

    started = time.monotonic()
    total = asyncio.run(_drive())
    ping_at = loop_scheduled_at[0]

    # If the slow_create ran on the event-loop thread, ping_at would be
    # roughly `started + 0.3`. With to_thread offload, ping_at should land
    # within tens of milliseconds of `started`.
    ping_delay = ping_at - started
    assert ping_delay < 0.1, f"ping was delayed by {ping_delay:.3f}s; warmup blocked the event loop"
    # Whole run will take at least 0.3s because of the slow helper.
    assert total - started >= 0.3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
