"""Tests for browser panel WebSocket session authorization + channel routing.

Covers ``main._resolve_subscribable_panel``, which returns the CANONICAL panel_id
a browser push socket should subscribe to (the id the panel is bound under, which
is the id ``ui_actions`` are pushed to), or ``None`` to reject. The connecting id
is intentionally not trusted: the resolver maps the socket to the panel bound
under its session, so delivery works regardless of which alias the client used.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Db:
    """Routes execute() to a row by matching a substring of the SQL.

    ``routes`` maps an SQL substring -> row (or None). The first matching needle
    (insertion order) wins; unmatched queries return None. A bare ``row`` arg sets
    a single default row for every query (back-compat with the simpler cases).
    """

    def __init__(self, row=None, routes: dict | None = None):
        self._default = row
        self._routes = routes or {}
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple):
        self.calls.append((sql, params))
        for needle, r in self._routes.items():
            if needle in sql:
                return _Cursor(r)
        return _Cursor(self._default)


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


_SESSION_BOUND = "WHERE chat_session_id = ?"
_LEGACY_NULL = "chat_session_id IS NULL"


@pytest.mark.asyncio
async def test_resolves_canonical_id_for_bound_session(monkeypatch):
    db = _install_database(
        monkeypatch, _Db(routes={_SESSION_BOUND: {"panel_id": "zoe-touch-pi"}})
    )

    async def resolve(_session_id):
        return {"user_id": "guest", "role": "guest"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "session-1") == "zoe-touch-pi"
    assert db.calls[0][1] == ("session-1", "guest")


@pytest.mark.asyncio
async def test_alias_connect_routes_to_canonical_channel(monkeypatch):
    """Connecting under a generated alias still subscribes to the bound canonical
    channel — this is the core fix that makes delivery work despite the alias."""
    _install_database(
        monkeypatch, _Db(routes={_SESSION_BOUND: {"panel_id": "zoe-touch-pi"}})
    )

    async def resolve(_session_id):
        return {"user_id": "guest", "role": "guest"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    resolved = await main._resolve_subscribable_panel("panel_0e3ko5bl", "session-1")
    assert resolved == "zoe-touch-pi"  # NOT the connecting alias id


@pytest.mark.asyncio
async def test_rejects_unbound_session(monkeypatch):
    _install_database(monkeypatch, _Db(routes={_SESSION_BOUND: None, _LEGACY_NULL: None}))

    async def resolve(_session_id):
        return {"user_id": "guest", "role": "guest"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "session-1") is None


@pytest.mark.asyncio
async def test_legacy_null_session_bind_resolves_to_connecting_id(monkeypatch):
    _install_database(monkeypatch, _Db(routes={_SESSION_BOUND: None, _LEGACY_NULL: (1,)}))

    async def resolve(_session_id):
        return {"user_id": "guest", "role": "guest"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "session-1") == "zoe-touch-pi"


@pytest.mark.asyncio
async def test_admin_subscribes_to_explicit_panel_without_binding(monkeypatch):
    db = _install_database(monkeypatch, _Db(None))

    async def resolve(_session_id):
        return {"user_id": "admin-user", "role": "admin"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "session-1") == "zoe-touch-pi"
    assert db.calls == []


@pytest.mark.asyncio
async def test_rejects_empty_user_id(monkeypatch):
    db = _install_database(monkeypatch, _Db({"user_id": "guest"}))

    async def resolve(_session_id):
        return {"user_id": "", "role": "member"}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "session-1") is None
    assert db.calls == []


@pytest.mark.asyncio
async def test_rejects_invalid_session(monkeypatch):
    db = _install_database(monkeypatch, _Db(None))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", None) is None
    assert "FROM panels WHERE panel_id = ?" in db.calls[0][0]


@pytest.mark.asyncio
async def test_allows_registered_guest_panel_without_valid_session(monkeypatch):
    db = _install_database(monkeypatch, _Db({"allow_guest": 1, "is_active": 1}))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", "stale-guest-session")
        == "zoe-touch-pi"
    )
    assert "FROM panels WHERE panel_id = ?" in db.calls[0][0]
    assert db.calls[0][1] == ("zoe-touch-pi",)


@pytest.mark.asyncio
async def test_rejects_guest_panel_when_guest_disabled(monkeypatch):
    _install_database(monkeypatch, _Db({"allow_guest": 0, "is_active": 1}))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "stale-guest-session") is None


@pytest.mark.asyncio
async def test_rejects_inactive_guest_panel(monkeypatch):
    _install_database(monkeypatch, _Db({"allow_guest": 1, "is_active": 0}))

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "stale-guest-session") is None


@pytest.mark.asyncio
async def test_rejects_when_guest_panel_db_yields_nothing(monkeypatch):
    _install_empty_database(monkeypatch)

    async def resolve(_session_id):
        return None

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)

    assert await main._resolve_subscribable_panel("zoe-touch-pi", "stale-guest-session") is None
