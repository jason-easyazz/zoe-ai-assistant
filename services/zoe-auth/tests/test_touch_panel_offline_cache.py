"""The embedded quick-auth manager must not sync from itself.

`touch_panel.quick_auth.QuickAuthManager` was written for a panel-side daemon talking to a
remote auth server; inside zoe-auth its `server_url` IS this service, and the background
sync called `/api/admin/sync-data` (admin permission) with no credential — a logged 401
every five minutes forever and a cache that never filled. The offline cache is now off
unless `ZOE_TOUCH_PANEL_OFFLINE_CACHE=1`.
"""
from __future__ import annotations

import asyncio

import pytest

import touch_panel.quick_auth as qa


class _NoCache:
    def __init__(self): self.sync_calls = 0
    def is_sync_stale(self): return True
    def get_cache_stats(self): return {}


@pytest.fixture(autouse=True)
def _fresh_managers(monkeypatch):
    monkeypatch.setattr(qa, "_auth_managers", {})
    # the real cache manager creates its SQLite file under the container path (/app)
    monkeypatch.setattr(qa.cache_manager, "get_cache", lambda device_id: _NoCache())


def _spawned(monkeypatch, env_value):
    created = []
    if env_value is None:
        monkeypatch.delenv("ZOE_TOUCH_PANEL_OFFLINE_CACHE", raising=False)
    else:
        monkeypatch.setenv("ZOE_TOUCH_PANEL_OFFLINE_CACHE", env_value)

    def _record(coro):
        created.append(coro)
        coro.close()
        return None

    async def _make():
        monkeypatch.setattr(qa.asyncio, "create_task", _record)
        return qa.get_quick_auth_manager("unit-panel", "lab")

    mgr = asyncio.run(_make())
    return mgr, created


def test_default_does_not_start_the_self_sync_loop(monkeypatch):
    mgr, created = _spawned(monkeypatch, None)
    assert mgr.config.offline_enabled is False
    assert created == []


def test_explicit_zero_keeps_it_off(monkeypatch):
    mgr, created = _spawned(monkeypatch, "0")
    assert mgr.config.offline_enabled is False and created == []


def test_opt_in_starts_the_loop(monkeypatch):
    mgr, created = _spawned(monkeypatch, "1")
    assert mgr.config.offline_enabled is True
    assert len(created) == 1


# --- with the cache off, the server's verdict is final ---------------------------

class _CacheWithSession(_NoCache):
    """A cache that still holds a session (e.g. written before the flag was turned off)."""
    def __init__(self):
        super().__init__(); self.writes = []
    def get_cached_session(self, session_id):
        import types
        return types.SimpleNamespace(user_id="asya", session_id=session_id, permissions=[], expires_at=None)
    def cache_session(self, *a): self.writes.append(a)


def _manager_with(monkeypatch, env_value):
    cache = _CacheWithSession()
    monkeypatch.setattr(qa.cache_manager, "get_cache", lambda device_id: cache)
    if env_value is None: monkeypatch.delenv("ZOE_TOUCH_PANEL_OFFLINE_CACHE", raising=False)
    else: monkeypatch.setenv("ZOE_TOUCH_PANEL_OFFLINE_CACHE", env_value)
    async def _make():
        monkeypatch.setattr(qa.asyncio, "create_task", lambda coro: (coro.close(), None)[1])
        return qa.get_quick_auth_manager("unit-panel", "lab")
    return asyncio.run(_make()), cache


def test_revoked_session_is_not_resurrected_from_the_cache_when_offline_is_off(monkeypatch):
    mgr, cache = _manager_with(monkeypatch, None)
    async def _rejected(session_id): return qa.QuickAuthResult(success=False, error_message="revoked")
    monkeypatch.setattr(mgr, "_validate_session_with_server", _rejected)
    result = asyncio.run(mgr.validate_session("sess-1"))
    assert result.success is False


def test_cached_session_is_honoured_only_when_offline_is_on(monkeypatch):
    mgr, cache = _manager_with(monkeypatch, "1")
    async def _rejected(session_id): return qa.QuickAuthResult(success=False, error_message="server down")
    monkeypatch.setattr(mgr, "_validate_session_with_server", _rejected)
    result = asyncio.run(mgr.validate_session("sess-1"))
    assert result.success is True and result.offline_mode is True


def test_successful_login_is_not_cached_when_offline_is_off(monkeypatch):
    mgr, cache = _manager_with(monkeypatch, None)
    async def _ok(username, passcode, device_info):
        return qa.QuickAuthResult(success=True, user_id="asya", session_id="s-ok", permissions=[])
    monkeypatch.setattr(mgr, "_authenticate_with_server", _ok)
    result = asyncio.run(mgr.authenticate_passcode("asya", "1234"))
    assert result.success is True and cache.writes == []


def test_cached_users_listing_is_empty_when_offline_is_off(monkeypatch):
    mgr, cache = _manager_with(monkeypatch, None)
    assert asyncio.run(mgr.get_cached_users()) == []
