"""A6 step 2: the two start-up stalls the stall recorder caught after a restart move off the event loop.

* HTTP: the first httpx request of a process imports the connection pool / HTTP/1.1 / async backend modules inside
  ``AsyncClient.send``. Proof is deterministic, not a timing guess: in a fresh process, the first real request after
  ``warm_http_stack_sync`` (run in a thread) loads NO new module on the loop; the negative control without it loads several.
* Routes: FastAPI builds an included router's route table (pydantic fields) on the first request matched against it.
  After ``prebuild_route_tables`` (in a thread) the first request finds it built; the control without it builds it on the loop.
* Failure injected (raises, hangs): start-up and the first request still work, nothing raises.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

import startup_warmups

pytestmark = pytest.mark.ci_safe

SERVICE = Path(__file__).resolve().parents[1]
_HTTP_CHILD = r"""
import asyncio, sys
sys.path.insert(0, sys.argv[1])
async def handle(r, w):
    await r.readuntil(b"\r\n\r\n"); w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"); await w.drain(); w.close()
async def main():
    import httpx, startup_warmups
    if sys.argv[2] == "warm":
        assert await asyncio.to_thread(startup_warmups.warm_http_stack_sync)
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    before = set(sys.modules)
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"http://127.0.0.1:{srv.sockets[0].getsockname()[1]}/health")
    srv.close()
    print(r.status_code, len(set(sys.modules) - before))
asyncio.run(main())
"""


def _first_request_new_modules(mode: str) -> tuple[int, int]:
    r = subprocess.run([sys.executable, "-c", _HTTP_CHILD, str(SERVICE), mode], capture_output=True, text=True,
                       timeout=120, env={"PATH": "/usr/bin:/bin"})
    status, new = r.stdout.strip().splitlines()[-1].split()
    return int(status), int(new)


def test_after_the_http_warm_up_the_first_request_loads_nothing_on_the_loop():
    assert _first_request_new_modules("warm") == (200, 0)


def test_negative_control_without_it_the_first_request_imports_on_the_loop():
    status, new = _first_request_new_modules("cold")
    assert status == 200 and new >= 3, new


from pydantic import BaseModel  # noqa: E402


class Body(BaseModel):  # module level: the file uses postponed annotations, so FastAPI must resolve "Body" by name
    text: str
    count: int = 0


def _app():
    from fastapi import APIRouter, FastAPI

    router = APIRouter()

    @router.post("/echo")
    async def echo(body: Body) -> dict:
        return {"n": body.count}

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


def _included(app):
    return [r for r in app.routes if callable(getattr(r, "effective_candidates", None))]


async def _post(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return (await c.post("/api/echo", json={"text": "x", "count": 2})).json()


async def test_route_tables_are_built_before_the_first_request():
    app = _app()
    lazy = _included(app)
    if not lazy:
        pytest.skip("this FastAPI builds route tables at include time: nothing is lazy")
    assert all(r._effective_candidates_version is None for r in lazy)       # negative control: still lazy
    assert await asyncio.to_thread(startup_warmups.prebuild_route_tables, app) == len(lazy)
    built = [r._effective_candidates_version for r in lazy]
    assert None not in built
    assert await _post(app) == {"n": 2}
    assert [r._effective_candidates_version for r in lazy] == built        # the first request built nothing new


async def test_negative_control_without_the_prebuild_the_first_request_builds_the_table():
    app = _app()
    lazy = _included(app)
    if not lazy:
        pytest.skip("this FastAPI builds route tables at include time: nothing is lazy")
    assert await _post(app) == {"n": 2}
    assert all(r._effective_candidates_version is not None for r in lazy)   # built ON the request


async def test_a_failing_prebuild_never_raises_and_the_first_request_still_works(monkeypatch):
    app = _app()
    for r in _included(app):
        monkeypatch.setattr(r, "effective_candidates", lambda: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    assert await asyncio.to_thread(startup_warmups.prebuild_route_tables, app) == 0
    monkeypatch.undo()
    assert await _post(app) == {"n": 2}


def test_a_failing_or_hanging_http_warm_up_returns_false_within_its_bound(monkeypatch):
    async def boom():
        raise OSError("no loopback")

    monkeypatch.setattr(startup_warmups, "_once", boom)
    assert startup_warmups.warm_http_stack_sync() is False

    async def hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(startup_warmups, "_once", hang)
    t0 = time.monotonic()
    assert startup_warmups.warm_http_stack_sync(timeout_s=0.3) is False
    assert time.monotonic() - t0 < 2.0


async def test_the_readiness_probe_runs_even_when_the_warm_up_fails(monkeypatch):
    import main

    calls = []

    async def ready(timeout_s=2.0):
        calls.append(timeout_s)
        return {"ok": True}

    def boom(*a, **k):
        raise RuntimeError("warm-up broke")

    monkeypatch.setattr(main, "_check_brain_ready", ready)
    monkeypatch.setattr(startup_warmups, "warm_http_stack_sync", boom)
    await asyncio.wait_for(main._wait_for_brain_startup(), 5)
    assert calls == [2.0]

    monkeypatch.setattr(startup_warmups, "WARM_TIMEOUT_S", 0.2)
    monkeypatch.setattr(startup_warmups, "warm_http_stack_sync", lambda *a, **k: time.sleep(3))
    t0 = time.monotonic()
    await asyncio.wait_for(main._wait_for_brain_startup(), 5)              # a hung warm-up costs at most its bound
    assert calls == [2.0, 2.0] and time.monotonic() - t0 < 1.5
