"""Kokoro idle CUDA-pool trim (ZOE_KOKORO_IDLE_TRIM_S) - scheduling logic only.

No model, no CUDA, no HTTP: torch is never imported (the trim body is stubbed).

Negative controls:
  * default (0) schedules NOTHING - the live behaviour is byte-identical;
  * the CPU fallback never schedules a trim (no CUDA pool to trim);
  * a second synth cancels the pending trim and re-arms it (a trim never fires mid-burst);
  * the trim is skipped when a synth holds the pipeline lock.
"""
import asyncio
import importlib.util
import pathlib
import sys

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup" / "kokoro_sidecar.py"


@pytest.fixture()
def ks():
    spec = importlib.util.spec_from_file_location("kokoro_sidecar_trim", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar_trim"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("kokoro_sidecar_trim", None)


def test_off_by_default_schedules_nothing(ks):
    loop = asyncio.new_event_loop()
    try:
        ks._device = "cuda"
        ks._schedule_idle_trim(loop)
        assert ks._IDLE_TRIM_S == 0 and ks._idle_trim_handle is None
    finally:
        loop.close()


def test_cpu_fallback_never_schedules(ks):
    loop = asyncio.new_event_loop()
    try:
        ks._IDLE_TRIM_S, ks._device = 2.0, "cpu"
        ks._schedule_idle_trim(loop)
        assert ks._idle_trim_handle is None
    finally:
        loop.close()


def test_rearm_cancels_previous_handle(ks):
    loop = asyncio.new_event_loop()
    try:
        ks._IDLE_TRIM_S, ks._device = 60.0, "cuda"
        ks._schedule_idle_trim(loop)
        first = ks._idle_trim_handle
        ks._schedule_idle_trim(loop)
        assert first.cancelled() and ks._idle_trim_handle is not first
        ks._idle_trim_handle.cancel()
    finally:
        loop.close()


def test_trim_skipped_while_pipeline_busy_and_runs_when_free(ks, monkeypatch):
    calls = []
    monkeypatch.setattr(ks, "_trim_cuda_cache", lambda: calls.append(1))

    async def go():
        ks._pipeline_lock = asyncio.Lock()
        async with ks._pipeline_lock:
            await ks._idle_trim()
        assert calls == []
        await ks._idle_trim()
        assert calls == [1]

    asyncio.run(go())
