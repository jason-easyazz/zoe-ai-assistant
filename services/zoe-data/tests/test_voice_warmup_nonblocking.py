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


def test_available_memory_mb_parses_memavailable(tmp_path, monkeypatch):
    from routers import voice_tts

    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          512000 kB\n"
        "Cached:          2048000 kB\n"
    )

    monkeypatch.setattr(voice_tts, "Path", lambda *_args, **_kw: fake_meminfo)
    assert voice_tts._available_memory_mb() == 8000


def test_available_memory_mb_falls_back_to_free_plus_buffers_cached(tmp_path, monkeypatch):
    from routers import voice_tts

    fake_meminfo = tmp_path / "meminfo"
    # No MemAvailable line — must fall back to MemFree + Buffers + Cached.
    fake_meminfo.write_text(
        "MemTotal:       8192000 kB\n"
        "MemFree:        2048000 kB\n"
        "Buffers:         512000 kB\n"
        "Cached:         1024000 kB\n"
    )

    monkeypatch.setattr(voice_tts, "Path", lambda *_args, **_kw: fake_meminfo)
    # (2_048_000 + 512_000 + 1_024_000) kB = 3_584_000 kB // 1024 = 3500 MB
    assert voice_tts._available_memory_mb() == 3500


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
    """A blocking tempfile create must not stall concurrent event-loop tasks.

    A heartbeat coroutine ticks every 20ms for the duration of the warmup while
    the offloaded ``_create_warmup_wav_path`` sleeps 0.3s. If that sleep ran on
    the event-loop thread instead of a worker thread, one heartbeat interval
    would balloon to ~0.3s; with the ``asyncio.to_thread`` offload every gap
    stays close to 20ms. This genuinely exercises concurrency (unlike awaiting a
    no-await coroutine, which would complete before the warmup task is even
    scheduled and pass regardless of blocking).
    """
    from routers import voice_tts

    def _slow_create() -> str:
        time.sleep(0.3)  # simulate slow disk I/O
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            return tmp.name

    def _quick_write(path: str, **_kwargs) -> None:
        return None

    async def _fake_worker(path: str) -> str:
        return ""

    async def _heartbeat(max_gap: list[float]) -> None:
        prev = time.monotonic()
        # Tick well past the 0.3s slow_create window.
        for _ in range(30):
            await asyncio.sleep(0.02)
            now = time.monotonic()
            max_gap[0] = max(max_gap[0], now - prev)
            prev = now

    async def _drive() -> float:
        max_gap = [0.0]
        warm = asyncio.create_task(voice_tts.warm_faster_whisper_worker())
        beat = asyncio.create_task(_heartbeat(max_gap))
        assert await warm is True
        await beat
        return max_gap[0]

    monkeypatch.delenv("ZOE_WHISPER_WARMUP", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", "0")
    monkeypatch.setattr(voice_tts, "_create_warmup_wav_path", _slow_create)
    monkeypatch.setattr(voice_tts, "_write_warmup_silence_wav", _quick_write)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)

    max_gap = asyncio.run(_drive())

    # With the offload the loop keeps ticking; a stall would show one ~0.3s gap.
    assert max_gap < 0.15, f"event loop stalled for {max_gap:.3f}s during warmup"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
