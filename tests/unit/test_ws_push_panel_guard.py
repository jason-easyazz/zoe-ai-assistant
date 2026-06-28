"""Unit tests for the /ws/push panel-subscription guard.

Covers `main._session_can_subscribe_panel`, the guard that decides whether a
browser push WebSocket may subscribe to a panel channel. A single physical touch
panel answers to more than one ``panel_id`` over its life (a generated
``panel_xxxx`` plus the registered id e.g. ``zoe-touch-pi``); the guard must
accept the panel's legitimate aliases — those bound under the SAME browser
``session_id`` — without authorizing a panel the session was never bound to.

Regression target: a push socket connecting under a generated ``panel_xxxx`` was
rejected (1008 / "403"); the fix authorizes panel ids bound to the connecting
session, while refusing arbitrary ids (the security tightening from PR review).

Run with:
    pytest tests/unit/test_ws_push_panel_guard.py -v
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest.mock as mock

import pytest


# Both services/zoe-data and services/zoe-auth expose a top-level `main` module
# (conftest.py adds both to sys.path), so a bare `import main` is ambiguous, and
# zoe-data must win over zoe-auth for sibling modules like `models`/`routers`.
# Load zoe-data's main.py explicitly by path, and RESTORE the global import state
# (sys.path + any modules we evict) in teardown so a mixed zoe-data/zoe-auth test
# run isn't corrupted by collection order.
_ZOE_DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "services", "zoe-data")
)
_SHADOWED = ("models", "routers", "database")

main = None  # populated by the autouse fixture below


@pytest.fixture(autouse=True)
def _load_zoe_data_main():
    """Import zoe-data's main under an isolated sys.path/module-table, then restore."""
    global main
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in _SHADOWED}

    sys.path.insert(0, _ZOE_DATA_DIR)
    for name in _SHADOWED:
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location(
            "zoe_data_main", os.path.join(_ZOE_DATA_DIR, "main.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["zoe_data_main"] = module
        spec.loader.exec_module(module)
        main = module
        yield
    finally:
        main = None
        sys.modules.pop("zoe_data_main", None)
        # Restore evicted modules (or remove if they weren't present before).
        for name, mod in saved_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        sys.path[:] = saved_path


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDB:
    """Async DB stub. `responses` maps a substring of the SQL to the row to return.

    The first matching needle (by insertion order) wins, so place the most
    specific needles first.
    """

    def __init__(self, responses):
        self._responses = responses

    async def execute(self, sql, params=()):
        for needle, row in self._responses.items():
            if needle in sql:
                return _FakeCursor(row)
        return _FakeCursor(None)


def _patch_db(monkeypatch, responses):
    async def _fake_get_db():
        yield _FakeDB(responses)

    # _session_can_subscribe_panel does `from database import get_db`.
    fake_database = mock.MagicMock()
    fake_database.get_db = _fake_get_db
    monkeypatch.setitem(sys.modules, "database", fake_database)


def _patch_session(monkeypatch, user):
    async def _fake_resolve(session_id):
        return user

    monkeypatch.setattr(main, "_resolve_ws_session", _fake_resolve)


# Needles matching the two guard queries. The session-bound lookup also contains
# "AND user_id = ?", so match on the distinguishing trailing clause.
_SESSION_BOUND = "chat_session_id = ?"
_LEGACY_NULL = "chat_session_id IS NULL"


@pytest.mark.asyncio
async def test_member_panel_bound_to_this_session_accepted(monkeypatch):
    """A member connecting to a panel bound under THIS session_id is accepted."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(monkeypatch, {_SESSION_BOUND: (1,), _LEGACY_NULL: None})
    assert await main._session_can_subscribe_panel("zoe-touch-pi", "sess-1") is True


@pytest.mark.asyncio
async def test_member_alias_bound_to_same_session_accepted(monkeypatch):
    """The bug fix: a generated alias id bound under the SAME session is accepted."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(monkeypatch, {_SESSION_BOUND: (1,), _LEGACY_NULL: None})
    assert await main._session_can_subscribe_panel("panel_0e3ko5bl", "sess-1") is True


@pytest.mark.asyncio
async def test_member_legacy_null_session_bind_accepted(monkeypatch):
    """A legacy/device bind (chat_session_id IS NULL) owned by the user is accepted."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(monkeypatch, {_SESSION_BOUND: None, _LEGACY_NULL: (1,)})
    assert await main._session_can_subscribe_panel("zoe-touch-pi", "sess-1") is True


@pytest.mark.asyncio
async def test_member_panel_not_bound_to_this_session_rejected(monkeypatch):
    """Security: a panel the connecting session was NEVER bound to is rejected,
    even though the user may own other panels."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    # No row for this panel_id under this session_id, and no NULL-session row.
    _patch_db(monkeypatch, {_SESSION_BOUND: None, _LEGACY_NULL: None})
    assert await main._session_can_subscribe_panel("panel_unrelated", "sess-1") is False


@pytest.mark.asyncio
async def test_member_other_users_panel_rejected(monkeypatch):
    """A member may not subscribe to a panel bound to a DIFFERENT user.

    The query filters on `user_id = ?`, so a row owned by u2 simply doesn't match
    for u1 — both lookups return no row.
    """
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(monkeypatch, {_SESSION_BOUND: None, _LEGACY_NULL: None})
    assert await main._session_can_subscribe_panel("zoe-touch-pi", "sess-1") is False


@pytest.mark.asyncio
async def test_admin_accepted_without_binding(monkeypatch):
    """Admin/agent roles bypass the per-panel binding check."""
    _patch_session(monkeypatch, {"user_id": "admin1", "role": "admin"})
    # No DB patch needed; admin short-circuits before the query.
    assert await main._session_can_subscribe_panel("anything", "sess-1") is True


@pytest.mark.asyncio
async def test_no_session_id_only_legacy_null_bind_accepted(monkeypatch):
    """Without a session_id the session-bound branch can't fire; only a legacy
    NULL-session bind owned by the user is accepted."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    # Even if a session-bound row existed it wouldn't apply (no session_id); a
    # NULL-session row is the only accept path.
    _patch_db(monkeypatch, {_SESSION_BOUND: (1,), _LEGACY_NULL: None})
    assert await main._session_can_subscribe_panel("panel_0e3ko5bl", None) is False
    _patch_db(monkeypatch, {_SESSION_BOUND: None, _LEGACY_NULL: (1,)})
    assert await main._session_can_subscribe_panel("zoe-touch-pi", None) is True


@pytest.mark.asyncio
async def test_empty_panel_id_rejected(monkeypatch):
    """An empty panel_id is rejected before any DB lookup."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    assert await main._session_can_subscribe_panel("", "sess-1") is False


@pytest.mark.asyncio
async def test_no_user_falls_back_to_guest_policy(monkeypatch):
    """An unresolved session defers to the registered-panel guest policy."""
    _patch_session(monkeypatch, None)

    async def _fake_guest(panel_id):
        return panel_id == "guest-panel"

    monkeypatch.setattr(main, "_panel_allows_guest_push", _fake_guest)
    assert await main._session_can_subscribe_panel("guest-panel", None) is True
    assert await main._session_can_subscribe_panel("panel_0e3ko5bl", None) is False
