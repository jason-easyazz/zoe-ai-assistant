"""Tests for browser panel WebSocket session authorization + channel routing.

Covers ``main._resolve_subscribable_panel``, which returns the panel_id a browser
push socket should subscribe to (the channel ``ui_actions`` are pushed to), or
``None`` to reject. Resolution is anchored on the binding:

  * the connecting id is authoritative when it is itself a bound panel (under this
    session, or a NULL-session legacy bind) — each of a session's panels stays on
    its own channel, so panel A is never routed to panel B's channel;
  * a freshly generated alias with no bound row resolves to the session's panel
    ONLY when the session is bound to exactly one panel.
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
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return list(self._rows)


class _Db:
    """Routes execute() to a cursor by matching a substring of the SQL.

    ``routes`` maps an SQL substring -> a _Cursor (use ``cur(...)`` helper). The
    first matching needle (insertion order) wins; unmatched queries return an
    empty cursor. A bare ``row`` arg sets a single default row for every query.
    """

    def __init__(self, row=None, routes: dict | None = None):
        self._default = _Cursor(row)
        self._routes = routes or {}
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple):
        self.calls.append((sql, params))
        for needle, c in self._routes.items():
            if needle in sql:
                return c
        return self._default


def cur(row=None, rows=None):
    return _Cursor(row=row, rows=rows)


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


def _member(monkeypatch, user_id="u1", role="member"):
    async def resolve(_session_id):
        return {"user_id": user_id, "role": role}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)


# SQL needles for the three resolver queries.
_OWN = "SELECT is_foreground FROM"          # step 1: connecting id's own row (this session)
_NULL_SESSION = "chat_session_id IS NULL"   # step 2: NULL-session legacy/device bind
_FOREGROUND = "AND is_foreground = 1"        # step 3: session's foreground panel


@pytest.mark.asyncio
async def test_connecting_id_wins_when_it_is_foreground(monkeypatch):
    """A live panel reconnecting (its own row is foreground) subscribes to its own
    channel — and the later queries are not reached."""
    db = _install_database(
        monkeypatch, _Db(routes={_OWN: cur(row={"is_foreground": 1})})
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", "session-1")
        == "zoe-touch-pi"
    )
    assert len(db.calls) == 1  # short-circuited at step 1
    assert db.calls[0][1] == ("zoe-touch-pi", "u1", "session-1")


@pytest.mark.asyncio
async def test_never_routes_panel_a_to_panel_b(monkeypatch):
    """Regression for 'Foreground Panel Misroutes': panel A reconnecting is
    foreground under its own id, so it stays on panel_A even though the session
    also has a (background) row and a different foreground lookup exists."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN: cur(row={"is_foreground": 1}),  # A is foreground under its own id
            _FOREGROUND: cur(row={"panel_id": "panel-B"}),  # would mis-route if reached
        }),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("panel-A", "session-1") == "panel-A"


@pytest.mark.asyncio
async def test_stale_background_alias_yields_to_foreground_panel(monkeypatch):
    """Regression for 'Stale Alias Still Wins': a stale alias whose own row is
    BACKGROUND (is_foreground=0) does not win — it follows the session's foreground
    panel, the channel pushes target."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN: cur(row={"is_foreground": 0}),  # stale alias, background
            _NULL_SESSION: cur(row=None),
            _FOREGROUND: cur(row={"panel_id": "zoe-touch-pi"}),
        }),
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("panel_0e3ko5bl", "session-1")
        == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_fresh_alias_no_row_routes_to_foreground(monkeypatch):
    """A freshly generated alias with no row of its own routes to the session's
    foreground panel, so delivery works regardless of the connecting id."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN: cur(row=None),
            _NULL_SESSION: cur(row=None),
            _FOREGROUND: cur(row={"panel_id": "zoe-touch-pi"}),
        }),
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("panel_new", "session-1")
        == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_null_session_device_bind_authoritative(monkeypatch):
    """A NULL-session legacy/device bind owned by the user is honoured via step 2."""
    _install_database(
        monkeypatch,
        _Db(routes={_OWN: cur(row=None), _NULL_SESSION: cur(row=(1,))}),
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", None) == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_rejected_when_nothing_matches(monkeypatch):
    """No own foreground row, no NULL-session bind, no session foreground → reject."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN: cur(row=None),
            _NULL_SESSION: cur(row=None),
            _FOREGROUND: cur(row=None),
        }),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("panel_unrelated", "session-1") is None


@pytest.mark.asyncio
async def test_other_users_panel_rejected(monkeypatch):
    """A member can't reach another user's panel: queries filter on user_id, so no
    row matches in any step → None."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN: cur(row=None),
            _NULL_SESSION: cur(row=None),
            _FOREGROUND: cur(row=None),
        }),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("zoe-touch-pi", "session-1") is None


@pytest.mark.asyncio
async def test_admin_subscribes_to_explicit_panel_without_binding(monkeypatch):
    db = _install_database(monkeypatch, _Db())
    _member(monkeypatch, user_id="admin-user", role="admin")
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", "session-1")
        == "zoe-touch-pi"
    )
    assert db.calls == []


@pytest.mark.asyncio
async def test_empty_panel_id_rejected(monkeypatch):
    db = _install_database(monkeypatch, _Db())
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("", "session-1") is None
    assert db.calls == []


@pytest.mark.asyncio
async def test_rejects_empty_user_id(monkeypatch):
    db = _install_database(monkeypatch, _Db())

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
