"""Unit tests for the /ws/push panel-subscription guard.

Covers `main._session_can_subscribe_panel`, the guard that decides whether a
browser push WebSocket may subscribe to a panel channel. A single physical touch
panel answers to more than one ``panel_id`` over its life (a generated
``panel_xxxx`` plus the registered id e.g. ``zoe-touch-pi``); the guard must
accept the panel's legitimate aliases without blanket-accepting arbitrary ids.

Regression target: a push socket connecting under a generated ``panel_xxxx`` was
rejected (1008 / "403") because the bound ``ui_panel_sessions`` row lived under
the registered alias. See PR for the alias-set fix mirroring #817.

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
# (conftest.py adds both to sys.path), so a bare `import main` is ambiguous.
# Load the zoe-data main.py explicitly by path.
_ZOE_DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "services", "zoe-data")
)
# zoe-data must win over zoe-auth for sibling modules like `models`/`routers`.
sys.path.insert(0, _ZOE_DATA_DIR)
for _mod in ("models", "routers"):
    sys.modules.pop(_mod, None)
_spec = importlib.util.spec_from_file_location(
    "zoe_data_main", os.path.join(_ZOE_DATA_DIR, "main.py")
)
main = importlib.util.module_from_spec(_spec)
sys.modules["zoe_data_main"] = main
_spec.loader.exec_module(main)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDB:
    """Async DB stub. `responses` maps a substring of the SQL to the row to return."""

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


@pytest.mark.asyncio
async def test_member_exact_panel_id_accepted(monkeypatch):
    """A member bound to the exact requested panel_id is accepted."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(monkeypatch, {"WHERE panel_id = ?": {"user_id": "u1"}})
    assert await main._session_can_subscribe_panel("zoe-touch-pi", "sess-1") is True


@pytest.mark.asyncio
async def test_member_alias_panel_id_accepted(monkeypatch):
    """The bug fix: a generated alias id (no exact row) is accepted when the
    SAME browser session owns a bound panel session under another id."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(
        monkeypatch,
        {
            # No row for the exact generated id...
            "WHERE panel_id = ?": None,
            # ...but the same session_id has a bound row for this user.
            "WHERE chat_session_id = ?": (1,),
        },
    )
    assert await main._session_can_subscribe_panel("panel_0e3ko5bl", "sess-1") is True


@pytest.mark.asyncio
async def test_member_unknown_panel_and_no_session_binding_rejected(monkeypatch):
    """Security: an unknown panel_id with no session-owned binding is rejected."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(
        monkeypatch,
        {
            "WHERE panel_id = ?": None,
            "WHERE chat_session_id = ?": None,
        },
    )
    assert await main._session_can_subscribe_panel("panel_attacker", "sess-1") is False


@pytest.mark.asyncio
async def test_member_other_users_panel_rejected(monkeypatch):
    """A member may not subscribe to a panel bound to a DIFFERENT user."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(
        monkeypatch,
        {
            "WHERE panel_id = ?": {"user_id": "u2"},  # owned by someone else
            "WHERE chat_session_id = ?": None,
        },
    )
    assert await main._session_can_subscribe_panel("zoe-touch-pi", "sess-1") is False


@pytest.mark.asyncio
async def test_admin_accepted_without_binding(monkeypatch):
    """Admin/agent roles bypass the per-panel binding check."""
    _patch_session(monkeypatch, {"user_id": "admin1", "role": "admin"})
    # No DB patch needed; admin short-circuits before the query.
    assert await main._session_can_subscribe_panel("anything", "sess-1") is True


@pytest.mark.asyncio
async def test_alias_requires_session_id(monkeypatch):
    """Without a session_id the alias branch cannot fire — unknown id rejected."""
    _patch_session(monkeypatch, {"user_id": "u1", "role": "member"})
    _patch_db(
        monkeypatch,
        {
            "WHERE panel_id = ?": None,
            "WHERE chat_session_id = ?": (1,),
        },
    )
    assert await main._session_can_subscribe_panel("panel_0e3ko5bl", None) is False


@pytest.mark.asyncio
async def test_no_user_falls_back_to_guest_policy(monkeypatch):
    """An unresolved session defers to the registered-panel guest policy."""
    _patch_session(monkeypatch, None)

    async def _fake_guest(panel_id):
        return panel_id == "guest-panel"

    monkeypatch.setattr(main, "_panel_allows_guest_push", _fake_guest)
    assert await main._session_can_subscribe_panel("guest-panel", None) is True
    assert await main._session_can_subscribe_panel("panel_0e3ko5bl", None) is False
