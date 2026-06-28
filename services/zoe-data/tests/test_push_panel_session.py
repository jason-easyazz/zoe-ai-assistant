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


# SQL needles for the two resolver queries.
_OWN_ROW = "AND (chat_session_id = ? OR chat_session_id IS NULL)"  # step 1: own bound row
_SESSION_PANELS = "WHERE chat_session_id = ? AND user_id = ? LIMIT 2"  # step 2: session panels


@pytest.mark.asyncio
async def test_connecting_id_authoritative_when_bound(monkeypatch):
    """When the connecting id is itself a bound panel, subscribe to ITS channel."""
    db = _install_database(monkeypatch, _Db(routes={_OWN_ROW: cur(row=(1,))}))
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", "session-1")
        == "zoe-touch-pi"
    )
    assert db.calls[0][1] == ("zoe-touch-pi", "u1", "session-1")


@pytest.mark.asyncio
async def test_never_routes_panel_a_to_panel_b(monkeypatch):
    """Regression for 'Session Picks Wrong Panel': panel A reconnecting (its id IS
    bound) stays on A's channel even if B is a newer foreground row for the same
    session. Step 1 matches A's own row, so step 2 (which might pick B) never runs."""
    db = _install_database(
        monkeypatch,
        _Db(routes={
            _OWN_ROW: cur(row=(1,)),  # panel A has its own bound row
            _SESSION_PANELS: cur(rows=[{"panel_id": "panel-A"}, {"panel_id": "panel-B"}]),
        }),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("panel-A", "session-1") == "panel-A"
    # Only the step-1 query ran; the multi-panel session lookup was not reached.
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_unbound_alias_resolves_to_sole_session_panel(monkeypatch):
    """A generated alias with no row of its own resolves to the session's panel
    when the session is bound to exactly ONE panel."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN_ROW: cur(row=None),  # alias has no bound row
            _SESSION_PANELS: cur(rows=[{"panel_id": "zoe-touch-pi"}]),
        }),
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("panel_0e3ko5bl", "session-1")
        == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_unbound_alias_rejected_when_session_has_multiple_panels(monkeypatch):
    """Security/correctness: an unbound alias is NOT mapped when the session has
    more than one panel — we can't tell which, so we refuse rather than mis-route."""
    _install_database(
        monkeypatch,
        _Db(routes={
            _OWN_ROW: cur(row=None),
            _SESSION_PANELS: cur(rows=[{"panel_id": "panel-A"}, {"panel_id": "panel-B"}]),
        }),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("panel_new", "session-1") is None


@pytest.mark.asyncio
async def test_unbound_alias_rejected_when_session_has_no_panel(monkeypatch):
    """No own row and no session panel → reject."""
    _install_database(
        monkeypatch,
        _Db(routes={_OWN_ROW: cur(row=None), _SESSION_PANELS: cur(rows=[])}),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("panel_unrelated", "session-1") is None


@pytest.mark.asyncio
async def test_legacy_null_session_bind_authoritative(monkeypatch):
    """A NULL-session legacy/device bind owned by the user is honoured via step 1
    (the connecting id matches its own NULL-session row)."""
    _install_database(monkeypatch, _Db(routes={_OWN_ROW: cur(row=(1,))}))
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", None) == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_other_users_panel_rejected(monkeypatch):
    """A member can't reach another user's panel: queries filter on user_id, so no
    row matches in either step → None."""
    _install_database(
        monkeypatch,
        _Db(routes={_OWN_ROW: cur(row=None), _SESSION_PANELS: cur(rows=[])}),
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
