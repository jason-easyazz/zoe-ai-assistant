"""Start-up warm-ups that move one-time first-use costs OFF the event loop (A6 step 2).

1. The HTTP client stack (``warm_http_stack_sync``).

The first ``httpx`` request a process makes pays one-time costs inside ``AsyncClient.send`` (the lazy imports of the
connection pool, HTTP/1.1 and async backends): the stall recorder caught it on the live box as a 1 s
``EVENT_LOOP_BLOCK top=brain_scheduler.py:send`` 8 s after a restart, on the brain readiness probe (``main._check_brain_ready``,
the first request after start). ``warm_http_stack_sync`` makes ONE throwaway GET through the same construction every caller
uses (a per-call ``httpx.AsyncClient``) to a throwaway HTTP server on 127.0.0.1 inside its OWN thread's event loop. Imports
are process-wide, so the real first request then finds them loaded. Never leaves the machine, no side effects, never raises.

2. The route tables (``prebuild_route_tables``). FastAPI 0.162 (the py312 pin) keeps every ``include_router`` as a lazy
``_IncludedRouter`` and builds its effective routes (dependants, pydantic ModelFields / TypeAdapters) on the FIRST request
that is matched against it: the live box logged it as a 1.3 s ``EVENT_LOOP_BLOCK top=- via=pydantic`` 10 s after a restart
(no zoe-data frame on the stack: routing runs before any endpoint). ``prebuild_route_tables`` asks every included router for
its effective routes once, in a thread; FastAPI caches them under its own lock. On a FastAPI without lazy includes it finds
nothing to do.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

WARM_TIMEOUT_S = 3.0


async def _once() -> bool:
    import httpx

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()

    srv = await asyncio.start_server(_serve, "127.0.0.1", 0)
    try:
        port = srv.sockets[0].getsockname()[1]
        async with httpx.AsyncClient(timeout=2.0) as client:
            return (await client.get(f"http://127.0.0.1:{port}/warm")).status_code == 204
    finally:
        srv.close()
        await srv.wait_closed()


def warm_http_stack_sync(timeout_s: float = WARM_TIMEOUT_S) -> bool:
    """Blocking (run it in a thread). True when the throwaway request completed; False (never an exception) otherwise, and
    callers simply pay today's lazy first-use cost."""
    try:
        return bool(asyncio.run(asyncio.wait_for(_once(), timeout_s)))
    except Exception as exc:  # noqa: BLE001 - a warm-up must never break start-up
        logger.info("http warm-up skipped (%s)", type(exc).__name__)
        return False


def prebuild_route_tables(app) -> int:
    """Blocking (run it in a thread). Build every lazily included router's effective routes now. Returns how many routers were
    built; never raises (a failure leaves FastAPI's own lazy path, which builds them on the first request as before)."""
    built = 0
    try:
        stack = list(getattr(app, "routes", ()) or ())
        while stack:
            route = stack.pop()
            candidates = getattr(route, "effective_candidates", None)
            if not callable(candidates):
                continue
            children = list(candidates())
            low = getattr(route, "effective_low_priority_routes", None)
            if callable(low):
                low()
            built += 1
            stack.extend(c for c in children if callable(getattr(c, "effective_candidates", None)))
    except Exception as exc:  # noqa: BLE001 - a warm-up must never break start-up
        logger.info("route table pre-build stopped (%s) after %d router(s)", type(exc).__name__, built)
    return built
