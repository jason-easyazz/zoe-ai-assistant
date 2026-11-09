"""Kokoro idle trim - timer lifecycle, and the A/B harness's safety guards.

Negative controls:
  * a FIRED timer forgets its handle (nothing retains a spent TimerHandle);
  * the shutdown path cancels a pending trim and the callback never runs;
  * the harness refuses the live :10201 port, and its night-window check covers
    exactly 01:50-04:55 (inclusive start, exclusive end).
"""
import asyncio
import importlib.util
import pathlib
import sys
from datetime import datetime

import pytest

pytestmark = pytest.mark.ci_safe

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ks():
    module = _load("kokoro_sidecar_trim_lc", _ROOT / "scripts" / "setup" / "kokoro_sidecar.py")
    yield module
    sys.modules.pop("kokoro_sidecar_trim_lc", None)


def test_fired_timer_clears_its_handle_and_shutdown_cancels(ks, monkeypatch):
    ran = []

    async def fake_trim():
        ran.append(1)

    monkeypatch.setattr(ks, "_idle_trim", fake_trim)

    async def go():
        loop = asyncio.get_event_loop()
        ks._IDLE_TRIM_S, ks._device = 0.01, "cuda"
        ks._schedule_idle_trim(loop)
        assert ks._idle_trim_handle is not None
        await asyncio.sleep(0.1)
        assert ran == [1] and ks._idle_trim_handle is None
        ks._schedule_idle_trim(loop)
        pending = ks._idle_trim_handle
        ks._cancel_idle_trim()
        assert pending.cancelled() and ks._idle_trim_handle is None
        await asyncio.sleep(0.05)
        assert ran == [1]

    asyncio.run(go())


def test_ab_harness_refuses_live_port_and_night_window():
    h = _load("kokoro_trim_ab", _ROOT / "scripts" / "perf" / "measure_kokoro_idle_trim_ab.py")
    assert h.LIVE_PORT == 10201
    assert h.in_night_window(datetime(2026, 11, 8, 1, 50)) and h.in_night_window(datetime(2026, 11, 8, 4, 54))
    assert not h.in_night_window(datetime(2026, 11, 8, 1, 49)) and not h.in_night_window(datetime(2026, 11, 8, 4, 55))
    with pytest.raises(SystemExit):
        h.run_variant(0, h.LIVE_PORT, 0.0, 0.0, 0.0)
