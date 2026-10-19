"""mcp_server's stdio mode fails LOUD when its DB pool cannot start — regression cover for the #960/#993/#995 ok:false class.

Root cause (2026-07-06): a stale rotated POSTGRES_URL baked into an agent's MCP config pre-empted bootstrap_runtime_env()'s
canonical .env load (a pre-set env key wins by design), so the spawned mcp_server.py failed pool init, limped on, and crashed
mid-call — the client saw a generic "Connection closed". Exiting non-zero puts the real cause in stderr.

(The intent router's mcporter subprocess that used to be this server's main client is retired — A3, 2026-10-19.)

Not marked ci_safe: needs the full zoe-data import chain (self-hosted lane runs this whole directory).
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
