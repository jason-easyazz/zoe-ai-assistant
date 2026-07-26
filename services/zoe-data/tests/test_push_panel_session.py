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

import contextlib
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402

pytestmark = pytest.mark.ci_safe


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
    """Back the resolver's ``db_pool.get_db_ctx`` with a fake connection.

    Since #978 the push guards acquire their connection via
    ``async with db_pool.get_db_ctx()`` (the #953 pool-leak fix), not
    ``async for db in database.get_db()`` — so that is what must be faked.
    """
    module = types.ModuleType("db_pool")

    @contextlib.asynccontextmanager
    async def get_db_ctx():
        yield db

    module.get_db_ctx = get_db_ctx
    monkeypatch.setitem(sys.modules, "db_pool", module)
    return db


def _install_empty_database(monkeypatch: pytest.MonkeyPatch):
    """DB unavailable: acquiring a pooled connection fails, so the guard's
    except-path must reject (return None/False), never grant access."""
    module = types.ModuleType("db_pool")

    @contextlib.asynccontextmanager
    async def get_db_ctx():
        raise RuntimeError("db pool unavailable")
        yield None  # pragma: no cover - makes this a generator

    module.get_db_ctx = get_db_ctx
    monkeypatch.setitem(sys.modules, "db_pool", module)


def _member(monkeypatch, user_id="u1", role="member"):
    async def resolve(_session_id):
        return {"user_id": user_id, "role": role}

    monkeypatch.setattr(main, "_resolve_ws_session", resolve)


async def _install_real_db(monkeypatch: pytest.MonkeyPatch):
    """Back ``db_pool.get_db_ctx`` with a real in-memory aiosqlite connection so
    the resolver's actual SQL (incl. the ORDER BY tie-break) runs, not a canned row.

    Seeds two REGISTERED panels (A and B) on one session/user where B wins the
    is_foreground/last_seen_at ordering, to prove resolution follows the
    CONNECTING panel rather than the row that sorts first.
    """
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE panels (
            panel_id TEXT PRIMARY KEY, is_active INTEGER DEFAULT 1,
            allow_guest INTEGER DEFAULT 0
        );
        CREATE TABLE ui_panel_sessions (
            panel_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            chat_session_id TEXT, is_foreground INTEGER DEFAULT 1, last_seen_at TEXT
        );
        INSERT INTO panels(panel_id) VALUES ('panel_a'), ('panel_b');
        -- Same session + user, both registered. panel_b is foreground AND more
        -- recently seen, so the old is_foreground/last_seen_at order picked B.
        INSERT INTO ui_panel_sessions(panel_id, user_id, chat_session_id, is_foreground, last_seen_at)
        VALUES ('panel_a', 'u1', 'session-1', 0, '2026-06-29T10:00:00'),
               ('panel_b', 'u1', 'session-1', 1, '2026-06-29T11:00:00');
        """
    )
    await conn.commit()

    module = types.ModuleType("db_pool")

    @contextlib.asynccontextmanager
    async def get_db_ctx():
        yield conn

    module.get_db_ctx = get_db_ctx
    monkeypatch.setitem(sys.modules, "db_pool", module)
    return conn


@pytest.mark.asyncio
async def test_multi_registered_session_routes_to_connecting_panel(monkeypatch):
    """Regression for 'Registered Panel Misroutes': a session holds rows for TWO
    registered panels (A and B) where B wins is_foreground/last_seen_at. Each
    panel's socket must resolve to ITS OWN channel, not whichever row sorts first.
    """
    conn = await _install_real_db(monkeypatch)
    try:
        _member(monkeypatch)
        # panel A connects → must subscribe to panel_a even though B is foreground
        # and most-recently-seen (the row that sorted first before the tie-break).
        assert await main._resolve_subscribable_panel("panel_a", "session-1") == "panel_a"
        # Reverse: panel B connects → panel_b.
        assert await main._resolve_subscribable_panel("panel_b", "session-1") == "panel_b"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_generated_alias_still_resolves_to_registered_panel(monkeypatch):
    """A generated alias (no registered row of its own, not in `panels`) connecting
    on the same multi-panel session has no per-panel link, so it falls back to the
    foreground/most-recent registered panel — here panel_b. This preserves the
    alias → registered routing the canonical resolution exists for."""
    conn = await _install_real_db(monkeypatch)
    try:
        _member(monkeypatch)
        assert (
            await main._resolve_subscribable_panel("panel_generated_xyz", "session-1")
            == "panel_b"
        )
    finally:
        await conn.close()


# SQL needles for the two resolver queries.
_REGISTERED = "JOIN panels p ON p.panel_id = s.panel_id"  # step 1: session's registered panel
_OWN = "AND (chat_session_id = ? OR chat_session_id IS NULL)"  # step 2: own-row fallback


@pytest.mark.asyncio
async def test_resolves_to_session_registered_panel(monkeypatch):
    """The canonical target is the session's REGISTERED panel (a bound row whose id
    is in `panels`), short-circuiting the own-row fallback."""
    db = _install_database(
        monkeypatch, _Db(routes={_REGISTERED: cur(row={"panel_id": "zoe-touch-pi"})})
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", "session-1")
        == "zoe-touch-pi"
    )
    assert len(db.calls) == 1  # registered lookup answered; fallback not reached
    # params: chat_session_id, user_id, and the connecting id tie-break that ranks
    # the connecting panel's own registered row first.
    assert db.calls[0][1] == ("session-1", "u1", "zoe-touch-pi")


@pytest.mark.asyncio
async def test_alias_with_own_row_still_routes_to_registered_channel(monkeypatch):
    """Regression for 'Stale Alias Wins/Rebinds': the connecting id is a generated
    alias that ALREADY HAS its own ui_panel_sessions row, AND the same session also
    has the registered panel. Canonical resolution is unconditional, so the socket
    joins the REGISTERED channel (where pushes go), not panel_<alias>."""
    db = _install_database(
        monkeypatch,
        _Db(routes={
            _REGISTERED: cur(row={"panel_id": "zoe-touch-pi"}),  # registered panel on session
            _OWN: cur(row=(1,)),  # the alias DOES have its own row — must NOT win
        }),
    )
    _member(monkeypatch)
    resolved = await main._resolve_subscribable_panel("panel_0e3ko5bl", "session-1")
    assert resolved == "zoe-touch-pi"  # registered channel, NOT the alias's own
    # Registered lookup is consulted first and wins; the own-row fallback that would
    # return the alias is never reached.
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_fresh_alias_routes_to_registered_channel(monkeypatch):
    """A freshly generated alias with no row of its own also routes to the session's
    registered panel."""
    _install_database(
        monkeypatch, _Db(routes={_REGISTERED: cur(row={"panel_id": "zoe-touch-pi"})})
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("panel_new", "session-1")
        == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_alias_only_session_falls_back_to_own_bound_row(monkeypatch):
    """When the session has only generated aliases (no registered panel resolves),
    the connecting id is honoured when it is itself a bound row."""
    _install_database(
        monkeypatch,
        _Db(routes={_REGISTERED: cur(row=None), _OWN: cur(row=(1,))}),
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("panel_0e3ko5bl", "session-1")
        == "panel_0e3ko5bl"
    )


@pytest.mark.asyncio
async def test_null_session_device_bind_authoritative(monkeypatch):
    """A NULL-session legacy/device bind owned by the user is honoured via the
    own-row fallback (no registered-panel row resolves for a device bind)."""
    _install_database(
        monkeypatch,
        _Db(routes={_REGISTERED: cur(row=None), _OWN: cur(row=(1,))}),
    )
    _member(monkeypatch)
    assert (
        await main._resolve_subscribable_panel("zoe-touch-pi", None) == "zoe-touch-pi"
    )


@pytest.mark.asyncio
async def test_rejected_when_nothing_matches(monkeypatch):
    """No registered panel and the connecting id isn't a bound row → reject."""
    _install_database(
        monkeypatch,
        _Db(routes={_REGISTERED: cur(row=None), _OWN: cur(row=None)}),
    )
    _member(monkeypatch)
    assert await main._resolve_subscribable_panel("panel_unrelated", "session-1") is None


@pytest.mark.asyncio
async def test_other_users_panel_rejected(monkeypatch):
    """A member can't reach another user's panel: queries filter on user_id, so no
    row matches in either step → None."""
    _install_database(
        monkeypatch,
        _Db(routes={_REGISTERED: cur(row=None), _OWN: cur(row=None)}),
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


# ---------------------------------------------------------------------------
# websocket_push channel-cleanup wiring: the socket subscribes under the
# RESOLVED panel id, so disconnect cleanup must target that same channel — not
# the connecting alias. Drives the endpoint with a fake WebSocket + broadcaster.
# ---------------------------------------------------------------------------
class _FakeWS:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}

    async def accept(self):
        pass

    async def send_json(self, _data):
        pass

    async def receive_text(self):
        # Mimic the client closing the socket immediately so the relay loop exits.
        raise main.WebSocketDisconnect()

    async def close(self, *_a, **_k):
        pass


class _RecordingBroadcaster:
    def __init__(self):
        self.connect_panel_calls: list = []
        self.connect_calls: list = []
        self.disconnect_calls: list = []

    async def connect_panel(self, _ws, panel_id):
        self.connect_panel_calls.append(panel_id)

    async def connect(self, _ws, channel, user_id=None):
        self.connect_calls.append((channel, user_id))

    def disconnect(self, _ws, channel="all"):
        self.disconnect_calls.append(channel)

    async def catchup(self, _ws, _seq):
        pass


@pytest.mark.asyncio
async def test_websocket_push_disconnects_under_resolved_panel_channel(monkeypatch):
    """A browser connecting as panel_<alias> that resolves to panel_<registered>
    subscribes to panel_<registered>, so cleanup must disconnect from that same
    channel. Regression for cleanup keyed on the connecting alias instead of the
    resolved id."""
    bc = _RecordingBroadcaster()
    monkeypatch.setattr(main, "broadcaster", bc)

    async def _resolve(_panel_id, _session_id):
        return "zoe-touch-pi"  # resolved id differs from the connecting alias

    monkeypatch.setattr(main, "_resolve_subscribable_panel", _resolve)

    ws = _FakeWS(query={"session_id": "session-1"})
    await main.websocket_push(ws, channel="all", panel_id="panel_alias_xyz")

    assert bc.connect_panel_calls == ["zoe-touch-pi"]
    # Must be the resolved channel, NOT "panel_panel_alias_xyz".
    assert bc.disconnect_calls == ["panel_zoe-touch-pi"]


@pytest.mark.asyncio
async def test_websocket_push_non_panel_disconnects_under_channel(monkeypatch):
    """Non-panel connections still clean up under their plain channel (guards the
    branch-local disconnect_channel assignment)."""
    bc = _RecordingBroadcaster()
    monkeypatch.setattr(main, "broadcaster", bc)

    async def _resolve_session(_sid):
        return {"user_id": "u1", "role": "member"}

    monkeypatch.setattr(main, "_resolve_ws_session", _resolve_session)

    ws = _FakeWS(query={"session_id": "session-1"})
    await main.websocket_push(ws, channel="all", panel_id="")

    assert bc.connect_calls == [("all", "u1")]
    assert bc.disconnect_calls == ["all"]
