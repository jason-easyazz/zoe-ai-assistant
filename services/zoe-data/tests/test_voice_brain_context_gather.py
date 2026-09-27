"""Pre-brain latency: the voice brain context is gathered CONCURRENTLY, keeps the
old per-loader error semantics, and is instrumented.

Before: ``voice_command`` awaited ``_load_voice_history`` → ``_voice_brain_memory``
→ ``_voice_domain_context`` one after another on every brain turn (both the
streaming and the non-streaming lane), so the pre-brain wait was their SUM.
They are independent reads, so ``_voice_brain_context`` gathers them and the
wait is their MAX. Pinned here:

* the three loaders genuinely overlap (every one starts before any finishes)
  and the wall time is ~max, not ~sum;
* one loader raising degrades ONLY that loader to the empty value it returns on
  failure — the other two still arrive, so the turn still has a reply to make;
* the ``pre_brain`` / ``memory_packet`` stage keys reach ``zoe_voice_stage_seconds``
  and the per-turn ``VOICE TIMING`` log line carries them;
* ``voice_command`` uses the helper in BOTH lanes (the serial awaits are gone).

Negative control for the overlap test: a serial reimplementation of the old
chain fails the same overlap assertion (see ``test_serial_chain_fails_overlap``).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time

import pytest

import routers.voice_tts as vt

pytestmark = pytest.mark.ci_safe

DELAY = 0.2


def _install_slow_loaders(monkeypatch, events, *, fail: str | None = None):
    def _loader(name, value):
        async def _run(*_a, **_k):
            events.append((name, "start", time.monotonic()))
            await asyncio.sleep(DELAY)
            events.append((name, "end", time.monotonic()))
            if fail == name:
                raise RuntimeError(f"{name} exploded")
            return value
        return _run

    monkeypatch.setattr(vt, "_load_voice_history", _loader("history", [{"role": "user", "content": "hi"}]))
    monkeypatch.setattr(vt, "_voice_brain_memory", _loader("memory", ("[What you remember]\n- tea", "About Jason")))
    monkeypatch.setattr(vt, "_voice_domain_context", _loader("domain", "[Your lists]\nmilk"))


def _assert_overlap(events):
    starts = [t for _, kind, t in events if kind == "start"]
    ends = [t for _, kind, t in events if kind == "end"]
    assert len(starts) == 3 and len(ends) == 3
    assert max(starts) < min(ends), f"loaders ran serially, not concurrently: {events}"


@pytest.mark.asyncio
async def test_the_three_loaders_run_concurrently(monkeypatch):
    events: list = []
    _install_slow_loaders(monkeypatch, events)
    t0 = time.monotonic()
    history, db_memory, portrait, domain, timings = await vt._voice_brain_context(
        "sess", "jason", "what's on my list", {"domain": "lists"}
    )
    wall = time.monotonic() - t0

    _assert_overlap(events)
    assert wall < DELAY * 2, f"gather wall {wall:.3f}s should be ~max ({DELAY}s), not the sum"
    assert history == [{"role": "user", "content": "hi"}]
    assert db_memory == "[What you remember]\n- tea"
    assert portrait == "About Jason"
    assert domain == "[Your lists]\nmilk"
    assert set(timings) == {"history", "memory", "domain", "memory_packet"}
    assert timings["memory_packet"] < DELAY * 2


@pytest.mark.asyncio
async def test_serial_chain_fails_overlap(monkeypatch):
    """NEGATIVE CONTROL: the pre-fix serial awaits fail the overlap assertion."""
    events: list = []
    _install_slow_loaders(monkeypatch, events)
    await vt._load_voice_history("sess", limit=3)
    await vt._voice_brain_memory("jason", "hi")
    await vt._voice_domain_context({"domain": "lists"}, "jason")
    with pytest.raises(AssertionError):
        _assert_overlap(events)


@pytest.mark.parametrize(
    "failing,expect",
    [
        ("history", {"history": [], "db_memory": "[What you remember]\n- tea", "domain": "[Your lists]\nmilk"}),
        ("memory", {"history": [{"role": "user", "content": "hi"}], "db_memory": None, "domain": "[Your lists]\nmilk"}),
        ("domain", {"history": [{"role": "user", "content": "hi"}], "db_memory": "[What you remember]\n- tea", "domain": None}),
    ],
)
@pytest.mark.asyncio
async def test_one_failing_loader_degrades_only_itself(monkeypatch, caplog, failing, expect):
    events: list = []
    _install_slow_loaders(monkeypatch, events, fail=failing)
    with caplog.at_level(logging.WARNING, logger=vt.logger.name):
        history, db_memory, portrait, domain, timings = await vt._voice_brain_context(
            "sess", "jason", "hello", {"domain": "lists"}
        )
    assert history == expect["history"]
    assert db_memory == expect["db_memory"]
    assert domain == expect["domain"]
    if failing == "memory":
        assert portrait is None
    assert f"{failing} load failed" in caplog.text
    # The turn can still be composed and sent to the brain.
    assert vt._merge_brain_context(db_memory, domain) is not None
    assert "memory_packet" in timings


@pytest.mark.asyncio
async def test_cancellation_is_never_swallowed(monkeypatch):
    async def _cancelled(*_a, **_k):
        raise asyncio.CancelledError()

    async def _ok(*_a, **_k):
        return None

    monkeypatch.setattr(vt, "_load_voice_history", _cancelled)
    monkeypatch.setattr(vt, "_voice_brain_memory", lambda *_a, **_k: _ok())
    monkeypatch.setattr(vt, "_voice_domain_context", _ok)
    with pytest.raises(asyncio.CancelledError):
        await vt._voice_brain_context("sess", "jason", "hi", None)


def _stage_count(stage: str) -> float:
    from voice_metrics import REGISTRY

    value = REGISTRY.get_sample_value("zoe_voice_stage_seconds_count", {"stage": stage})
    return value or 0.0


def test_pre_brain_and_memory_packet_stage_keys_are_recorded():
    before = {s: _stage_count(s) for s in ("pre_brain", "memory_packet")}
    vt._observe_pre_brain_stages(0.123)
    vt._observe_memory_packet_stage({"memory_packet": 0.045, "history": 0.01})
    for stage in ("pre_brain", "memory_packet"):
        assert _stage_count(stage) == before[stage] + 1, stage


def test_voice_timing_log_line(caplog):
    with caplog.at_level(logging.INFO, logger=vt.logger.name):
        vt._log_voice_timing(
            turn="t1", session_id="voice-panel-abc", path="stream", pre_brain_s=0.1234,
            ctx_timings={"memory_packet": 0.05, "history": 0.01, "memory": 0.05, "domain": 0.002},
            brain_ttft_s=0.21, llm_first_token_s=0.27,
        )
        vt._log_voice_timing(
            turn="t2", session_id="s", path="command", pre_brain_s=0.1,
            ctx_timings={}, brain_ttft_s=None, llm_first_token_s=None,
        )
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("VOICE TIMING")]
    assert lines[0] == (
        "VOICE TIMING turn=t1 session=voice-panel-abc path=stream pre_brain_ms=123 "
        "memory_packet_ms=50 (history=10 memory=50 domain=2) brain_ttft_ms=210 llm_first_token_ms=270 "
        "packet=eager"
    )
    assert "brain_ttft_ms=-1 llm_first_token_ms=-1" in lines[1]


def test_voice_timing_log_line_lazy_packet(caplog):
    """Lazy (Flue lane): a never-built packet logs 0 ms + ``packet=skipped``; one
    built on a failover hop logs its real cost + ``packet=lazy``."""
    with caplog.at_level(logging.INFO, logger=vt.logger.name):
        vt._log_voice_timing(
            turn="t3", session_id="s", path="stream", pre_brain_s=0.05,
            ctx_timings={}, brain_ttft_s=0.2, llm_first_token_s=0.2, packet_state="skipped",
        )
        vt._log_voice_timing(
            turn="t4", session_id="s", path="stream", pre_brain_s=0.05,
            ctx_timings={"memory_packet": 0.5, "history": 0.0, "memory": 0.5, "domain": 0.0},
            brain_ttft_s=0.9, llm_first_token_s=0.9, packet_state="lazy",
        )
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("VOICE TIMING")]
    assert "memory_packet_ms=0 (history=0 memory=0 domain=0)" in lines[0]
    assert lines[0].endswith("packet=skipped")
    assert "memory_packet_ms=500 (history=0 memory=500 domain=0)" in lines[1]
    assert lines[1].endswith("packet=lazy")


def test_voice_command_uses_the_concurrent_gather_in_both_lanes():
    src = inspect.getsource(vt.voice_command)
    # Both lanes go through _voice_brain_kwargs, whose loader is the concurrent
    # gather (eager, or lazy on the Flue lane).
    assert src.count("await _voice_brain_kwargs(") == 2, "stream + non-stream lanes"
    assert "await _voice_brain_context(" in inspect.getsource(vt._voice_brain_kwargs)
    assert src.count("_observe_pre_brain_stages(") == 2
    assert src.count("_log_voice_timing(") == 2
    for serial in ("await _load_voice_history(", "await _voice_brain_memory(", "await _voice_domain_context("):
        assert serial not in src, f"serial pre-brain await is back: {serial}"
