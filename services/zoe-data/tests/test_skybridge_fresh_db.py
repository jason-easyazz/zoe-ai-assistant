"""Skybridge fast path must not reuse the request-scoped DB connection.

voice_command runs as a detached task on the turn_stream lane (raced since
#1106), so the request-scoped asyncpg connection can be released back to the
pool before the skybridge fast path queries it — every query then fails with
"cannot call Connection.fetchrow(): connection has been released back to the
pool" and the turn silently falls through to the slow legacy path.

The fix: voice_command omits db= so resolve_skybridge_request acquires a fresh
pooled connection itself.
"""
import inspect
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.ci_safe  # pure-logic; no models/DB


def test_resolve_skybridge_acquires_fresh_pool_conn_when_db_omitted(monkeypatch):
    import skybridge_service as ss

    fresh = object()
    seen = {}

    @asynccontextmanager
    async def _fake_ctx():
        yield fresh

    async def _fake_resolve_with_db(intent, user_id, db, *, context=None):
        seen["db"] = db
        return {"handled": True, "intent": intent, "spoken_summary": "ok", "cards": []}

    monkeypatch.setattr(ss, "get_db_ctx", _fake_ctx)
    monkeypatch.setattr(ss, "_resolve_with_db", _fake_resolve_with_db)

    import asyncio
    result = asyncio.run(ss.resolve_skybridge_request("show my calendar", "demo-user"))
    assert result.get("handled") is True
    assert seen["db"] is fresh, "db omitted must mean a fresh pooled connection"


def test_voice_command_does_not_forward_request_scoped_db_to_skybridge():
    """Source-level pin for the call site: reintroducing db=db here re-breaks
    the fast path only at runtime (and only on the detached-task lane), which
    is exactly how it went unnoticed — so fail fast at test time instead."""
    import routers.voice_tts as vt

    src = inspect.getsource(vt.voice_command)
    idx = src.find("resolve_skybridge_request(")
    assert idx != -1, "skybridge fast path call site not found in voice_command"
    call_region = src[idx : idx + 400]
    call_args = call_region[: call_region.find(")")]
    assert "db=" not in call_args, (
        "voice_command must NOT forward the request-scoped db to "
        "resolve_skybridge_request — it can already be released back to the "
        f"pool on the detached turn_stream lane: {call_args!r}"
    )
