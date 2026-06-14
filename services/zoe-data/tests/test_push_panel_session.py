"""Tests for browser panel WebSocket session authorization."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402


class _Cursor:
    def __init__(self, row: dict | None):
        self._row = row

    async def fetchone(self):
        return self._row


class _Db:
    def __init__(self, row: dict | None):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple):
        self.calls.append((sql, params))
        return _Cursor(self.row)


def _install_database(monkeypatch: pytest.MonkeyPatch, db: _Db):
    module = types.ModuleType("database")

    async def get_db():
        yield db

    module.get_db = get_db
    monkeypatch.setitem(sys.modules, "database", module)
    return db


def _install_empty_database(monkeypatch: pytest.MonkeyPatch):
    module = types.ModuleType("database")

    async def get_db():
        if False:
            yield None

    module.get_db = get_db
    monkeypatch.setitem(sys.modules, "database", module)


@pytest.mark.asyncio
async def test_panel_push_session_allows_bound_panel_user(monkeypatch):
    db = _install_database(monkeypatch, _Db({"user_id": "guest"}))

    async def resolve(_session_id):
        return {"user_id": "guest", "role": "guest"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "session-1") is True
    assert db.calls[0][1] == ("zoe-touch-pi",)


@pytest.mark.asyncio
async def test_panel_push_session_rejects_unbound_panel_user(monkeypatch):
    _install_database(monkeypatch, _Db({"user_id": "other-user"}))

    async def resolve(_session_id):
        return {"user_id": "guest", "role": "guest"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "session-1") is False


@pytest.mark.asyncio
async def test_panel_push_session_allows_admin_without_panel_binding(monkeypatch):
    db = _install_database(monkeypatch, _Db(None))

    async def resolve(_session_id):
        return {"user_id": "admin-user", "role": "admin"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "session-1") is True
    assert db.calls == []


@pytest.mark.asyncio
async def test_panel_push_session_rejects_empty_user_id(monkeypatch):
    db = _install_database(monkeypatch, _Db({"user_id": "guest"}))

    async def resolve(_session_id):
        return {"user_id": "", "role": "member"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "session-1") is False
    assert db.calls == []


@pytest.mark.asyncio
async def test_panel_push_session_rejects_invalid_session(monkeypatch):
    db = _install_database(monkeypatch, _Db(None))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", None) is False
    assert "FROM panels WHERE panel_id = ?" in db.calls[0][0]


@pytest.mark.asyncio
async def test_panel_push_session_allows_registered_guest_panel_without_valid_session(monkeypatch):
    db = _install_database(monkeypatch, _Db({"allow_guest": 1, "is_active": 1}))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "stale-guest-session") is True
    assert "FROM panels WHERE panel_id = ?" in db.calls[0][0]
    assert db.calls[0][1] == ("zoe-touch-pi",)


@pytest.mark.asyncio
async def test_panel_push_session_rejects_guest_panel_when_guest_disabled(monkeypatch):
    _install_database(monkeypatch, _Db({"allow_guest": 0, "is_active": 1}))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "stale-guest-session") is False


@pytest.mark.asyncio
async def test_panel_push_session_rejects_inactive_guest_panel(monkeypatch):
    _install_database(monkeypatch, _Db({"allow_guest": 1, "is_active": 0}))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "stale-guest-session") is False


@pytest.mark.asyncio
async def test_panel_push_session_rejects_when_guest_panel_db_yields_nothing(monkeypatch):
    _install_empty_database(monkeypatch)

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._session_can_subscribe_panel("zoe-touch-pi", "stale-guest-session") is False
