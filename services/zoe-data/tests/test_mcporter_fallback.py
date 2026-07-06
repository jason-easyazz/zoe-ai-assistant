"""The mcporter fallback path — regression cover for the #960/#993/#995 ok:false class.

Root cause (2026-07-06): a stale rotated POSTGRES_URL baked into
~/.mcporter/mcporter.json pre-empted bootstrap_runtime_env()'s canonical .env
load (a pre-set env key wins by design), so the spawned mcp_server.py failed
pool init, limped on, and crashed mid-call — mcporter reported a generic
"Connection closed", _run_mcporter returned None, and every executor-less
intent surfaced ok:false while the user heard "done".

Locks in the two repo-side guarantees:
1. mcp_server.py stdio mode exits non-zero (loudly) when pool init fails,
   instead of limping into a guaranteed mid-call crash.
2. _run_mcporter spawns off the event-loop thread (async_subprocess) and maps
   exit codes / stdout faithfully.

Not marked ci_safe: needs the full zoe-data import chain (self-hosted lane
runs this whole directory).
"""

import asyncio
import sys
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))


def test_stdio_server_exits_nonzero_when_pool_init_fails(monkeypatch):
    import db_pool
    import mcp_server

    async def boom():
        raise RuntimeError("password authentication failed (test)")

    # run_stdio_server does `from db_pool import init_pool` at call time, so
    # patching the module attribute is seen by the server.
    monkeypatch.setattr(db_pool, "init_pool", boom)
    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(mcp_server.run_stdio_server())
    assert excinfo.value.code == 1


def test_run_mcporter_returns_stdout_on_success():
    from intent_router import _run_mcporter

    out = asyncio.run(_run_mcporter("echo hello-fallback"))
    assert out == "hello-fallback"


def test_run_mcporter_returns_none_on_nonzero_exit():
    from intent_router import _run_mcporter

    assert asyncio.run(_run_mcporter("false")) is None
