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
