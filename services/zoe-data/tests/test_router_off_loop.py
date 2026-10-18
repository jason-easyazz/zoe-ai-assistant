"""A1 (architecture deep dive 2026-10-18): the router never blocks the single event loop, and the lag gauge sees it.

``semantic_router.route`` is synchronous (bge-small embed + a blocking ``urlopen`` to the FunctionGemma sidecar, p50
~400 ms). zoe-data runs ONE uvicorn worker, so an inline call from an ``async def`` froze every other request, push
socket and voice stream for that long. Every async caller now runs it via ``asyncio.to_thread(_sr.route, text)``.

Behavioural proof: a fake 400 ms route runs while a concurrent ticker task wakes every 10 ms; the ticker's worst gap must
stay far below 400 ms. Each behavioural test carries its own negative control (the same harness around a direct sync
call shows the full 400 ms gap), so the instrument is proven able to see a block. The static test pins every turn-path
call site: an ``async def`` in those files that calls the sync router (or the sync helper) is a failure.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SERVICE = Path(__file__).resolve().parents[1]
BLOCK_S = 0.4
TICK_S = 0.01
# A 400 ms block shows as a >= ~390 ms gap; an unblocked loop on a busy CI box stays well under this.
MAX_OK_GAP_S = 0.2


async def _max_gap_while(coro) -> tuple[object, float]:
    """Run ``coro`` beside a 10 ms ticker; return (coro result, the ticker's worst gap in seconds)."""
    gaps: list[float] = []
    stop = asyncio.Event()

    async def _tick() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(TICK_S)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    ticker = asyncio.create_task(_tick())
    await asyncio.sleep(TICK_S * 3)  # the ticker is running before the call starts
    try:
        result = await coro
    finally:
        stop.set()
        await ticker
    return result, max(gaps)


def _slow_route(domain: str = "chat"):
    calls: list[str] = []

    def _route(text: str) -> dict:
        calls.append(text)
        time.sleep(BLOCK_S)  # the sidecar urlopen, in miniature
        return {"domain": domain, "routed": domain, "score": 0.9, "scores": {domain: 0.9}, "ms": BLOCK_S * 1000}

    return _route, calls


# --------------------------------------------------------------------------- #
# The instrument, and what the hop must preserve                             #
# --------------------------------------------------------------------------- #
async def test_negative_control_a_direct_sync_route_blocks_the_loop(monkeypatch):
    """The instrument works: the same harness around the OLD inline call sees the whole block."""
    import semantic_router

    route, _ = _slow_route()
    monkeypatch.setattr(semantic_router, "route", route)

    async def _inline():
        return semantic_router.route("what's on today")

    _, gap = await _max_gap_while(_inline())
    assert gap >= BLOCK_S * 0.9, f"negative control failed: an inline sync call showed only {gap:.3f}s"


async def test_the_thread_hop_keeps_contextvars(monkeypatch):
    """to_thread copies the context: per-turn ContextVars (home-control verdict, struggle notes) survive the hop."""
    import contextvars

    import semantic_router
    import stt_snap

    cv: contextvars.ContextVar[str] = contextvars.ContextVar("turn", default="unset")
    seen: list[str] = []
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "route", lambda text: seen.append(cv.get()) or {"domain": "chat"})
    cv.set("turn-1")
    await stt_snap.route_for("x")
    assert seen == ["turn-1"]


# --------------------------------------------------------------------------- #
# The turn-path callers                                                       #
# --------------------------------------------------------------------------- #
async def test_fast_tiers_resolve_routes_off_the_loop(monkeypatch):
    import expert_dispatch
    import fast_tiers
    import semantic_router

    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    route, calls = _slow_route("chat")
    monkeypatch.setattr(semantic_router, "route", route)
    out, gap = await _max_gap_while(fast_tiers.resolve("tell me a story", "u", "s", phase="domain"))
    assert calls == ["tell me a story"] and out is None  # chat -> the caller's brain lane, as before
    assert gap < MAX_OK_GAP_S, f"fast_tiers.resolve blocked the loop {gap:.3f}s"


async def test_stt_snap_route_for_is_off_the_loop(monkeypatch):
    import semantic_router
    import stt_snap

    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    route, calls = _slow_route("timers")
    monkeypatch.setattr(semantic_router, "route", route)
    out, gap = await _max_gap_while(stt_snap.route_for("cancel the timer"))
    assert calls == ["cancel the timer"] and out["domain"] == "timers"
    assert gap < MAX_OK_GAP_S, f"stt_snap.route_for blocked the loop {gap:.3f}s"


async def test_stt_snap_route_for_never_raises(monkeypatch):
    import semantic_router
    import stt_snap

    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)

    def _boom(text):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(semantic_router, "route", _boom)
    assert await stt_snap.route_for("x") is None
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)
    assert await stt_snap.route_for("x") is None


# Static pin: no async def on the turn path calls the sync router. Names that resolve to the semantic_router module in
# these files, and the sync wrapper names that must be awaited.
_TURN_PATH_FILES = ("routers/voice_tts.py", "fast_tiers.py", "stt_snap.py", "main.py", "skybridge_service.py")
_ROUTER_ALIASES = {"_sr", "_sr_mc", "semantic_router", "sr"}


def _sync_router_calls_in_async(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []

    def _visit(node: ast.AST, in_async: bool, awaited: set[int]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                _visit(child, True, awaited)
                continue
            if isinstance(child, (ast.FunctionDef, ast.Lambda)):
                _visit(child, False, awaited)  # a nested SYNC def runs wherever it is called (to_thread is fine)
                continue
            if isinstance(child, ast.Await) and isinstance(child.value, ast.Call):
                awaited.add(id(child.value))
            if in_async and isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                f = child.func
                owner = f.value.id if isinstance(f.value, ast.Name) else ""
                if f.attr == "route" and owner in _ROUTER_ALIASES:
                    bad.append(f"{path.name}:{child.lineno} {owner}.route(...) inside async def")
                if f.attr == "route_for" and id(child) not in awaited:
                    bad.append(f"{path.name}:{child.lineno} {owner}.route_for(...) not awaited inside async def")
            _visit(child, in_async, awaited)

    _visit(tree, False, set())
    return bad


def test_no_async_turn_path_calls_the_sync_router():
    bad: list[str] = []
    for rel in _TURN_PATH_FILES:
        bad += _sync_router_calls_in_async(SERVICE / rel)
    assert not bad, "blocking router calls on the event loop (use asyncio.to_thread(_sr.route, text)):\n" + "\n".join(bad)


def test_static_pin_negative_control(tmp_path):
    """The static check sees the pre-fix shape (an inline ``_sr.route`` in an async def)."""
    src = tmp_path / "x.py"
    src.write_text(
        "async def voice_command(text):\n"
        "    import semantic_router as _sr\n"
        "    rr = _sr.route(text)\n"
        "    snap = _stt_snap.route_for(text)\n"
        "    ok = await asyncio.to_thread(_sr.route, text)\n"
        "    def _sync(t):\n"
        "        return _sr.route(t)\n"
        "    return rr\n",
        encoding="utf-8",
    )
    bad = _sync_router_calls_in_async(src)
    assert len(bad) == 2 and "_sr.route" in bad[0] and "route_for" in bad[1], bad


# --------------------------------------------------------------------------- #
# The event-loop lag gauge                                                    #
# --------------------------------------------------------------------------- #
async def test_lag_monitor_sees_a_block_and_warns(caplog):
    import event_loop_lag

    event_loop_lag.reset_for_test()
    task = asyncio.create_task(event_loop_lag.monitor(0.02))
    await asyncio.sleep(0.1)
    with caplog.at_level(logging.WARNING, logger="event_loop_lag"):
        time.sleep(BLOCK_S)  # the old inline router call
        await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    snap = event_loop_lag.snapshot()
    assert snap["window_max_s"] >= BLOCK_S * 0.8, snap
    assert snap["over_warn"] >= 1
    assert any("EVENT_LOOP_LAG" in r.getMessage() for r in caplog.records)
    sample = event_loop_lag.REGISTRY.get_sample_value("zoe_event_loop_lag_max_seconds")
    assert sample is not None and sample >= BLOCK_S * 0.8


async def test_lag_monitor_stays_quiet_when_the_router_is_off_loop(monkeypatch, caplog):
    import event_loop_lag
    import semantic_router
    import stt_snap

    route, _ = _slow_route()
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "route", route)
    event_loop_lag.reset_for_test()
    task = asyncio.create_task(event_loop_lag.monitor(0.02))
    await asyncio.sleep(0.05)
    with caplog.at_level(logging.WARNING, logger="event_loop_lag"):
        await stt_snap.route_for("x")
        await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    snap = event_loop_lag.snapshot()
    assert snap["samples"] >= 5 and snap["window_max_s"] < MAX_OK_GAP_S, snap
    assert snap["over_warn"] == 0
    assert not any("EVENT_LOOP_LAG" in r.getMessage() for r in caplog.records)


def test_lag_record_rate_limits_the_warning(caplog):
    import event_loop_lag

    event_loop_lag.reset_for_test()
    with caplog.at_level(logging.WARNING, logger="event_loop_lag"):
        event_loop_lag.record(0.5, now=1000.0)
        event_loop_lag.record(0.6, now=1001.0)   # same 10 s: counted, not logged
        event_loop_lag.record(0.7, now=1011.0)   # next window: logged
        event_loop_lag.record(-1.0, now=1012.0)  # clock noise clamps to 0
    msgs = [r for r in caplog.records if "EVENT_LOOP_LAG" in r.getMessage()]
    assert len(msgs) == 2
    assert event_loop_lag.snapshot()["over_warn"] == 3
