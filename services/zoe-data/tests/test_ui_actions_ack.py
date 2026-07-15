"""Tests for touch UI action acknowledgement."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.ui_actions as ui_actions  # noqa: E402
from routers.ui_actions import ack_ui_action  # noqa: E402


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _AckDb:
    def __init__(self):
        self.updated = []
        self.ledger = []
        self.commits = 0

    async def execute(self, sql, params=()):
        if "FROM ui_actions" in sql:
            assert "EXISTS" in sql
            assert "FROM ui_panel_sessions" in sql
            assert params == (
                "action-1",
                "action-1",
                "browser-user",
                "zoe-touch-pi",
                "zoe-touch-pi",
                "zoe-touch-pi",
                "browser-user",
            )
            return _Cursor({
                "id": "action-1",
                "user_id": "panel-owner",
                "panel_id": "zoe-touch-pi",
                "status": "queued",
            })
        if "UPDATE ui_actions" in sql:
            self.updated.append((sql, params))
            assert "CAST(? AS boolean)" in sql
            assert params == ("success", None, None, None, True, "action-1", "panel-owner")
            return _Cursor()
        if "INSERT INTO ui_action_ledger" in sql:
            self.ledger.append((sql, params))
            assert params[2] == "panel-owner"
            assert params[3] == "zoe-touch-pi"
            assert params[4] == "ack:success"
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_ack_ui_action_accepts_matching_panel_when_user_differs(monkeypatch):
    async def allow(*_args, **_kwargs):
        return None

    class Broadcaster:
        async def broadcast(self, *_args, **_kwargs):
            return 1

    monkeypatch.setattr(ui_actions, "require_feature_access", allow)
    monkeypatch.setattr(ui_actions, "broadcaster", Broadcaster())

    db = _AckDb()
    result = await ack_ui_action(
        "action-1",
        {"status": "success", "panel_id": "zoe-touch-pi", "ui_context": {"page": "/touch/home.html"}},
        user={"user_id": "browser-user", "role": "guest"},
        db=db,
    )

    assert result == {"status": "ok", "action_id": "action-1", "state": "success"}
    assert len(db.updated) == 1
    assert len(db.ledger) == 1
    assert db.commits == 1


class _UnauthorizedAckDb:
    def __init__(self):
        self.updated = []
        self.commits = 0

    async def execute(self, sql, params=()):
        if "FROM ui_actions" in sql:
            assert "FROM ui_panel_sessions" in sql
            return _Cursor(None)
        if "UPDATE ui_actions" in sql:
            self.updated.append((sql, params))
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_ack_ui_action_rejects_panel_ack_when_user_not_registered(monkeypatch):
    async def allow(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ui_actions, "require_feature_access", allow)

    db = _UnauthorizedAckDb()
    result = await ack_ui_action(
        "action-1",
        {"status": "failed", "panel_id": "zoe-touch-pi"},
        user={"user_id": "other-guest", "role": "guest"},
        db=db,
    )

    assert result == {"status": "already_acked", "action_id": "action-1"}
    assert db.updated == []
    assert db.commits == 0


class _RowsCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _PendingDb:
    def __init__(self):
        self.executes = []
        self.commits = 0

    async def execute(self, sql, params=()):
        self.executes.append((sql, params))
        if "FROM ui_panel_sessions" in sql:
            return _RowsCursor(row={"user_id": "guest"})
        if "UPDATE ui_actions" in sql:
            return _RowsCursor()
        if "FROM ui_actions" in sql:
            return _RowsCursor(rows=[{
                "id": "new-card",
                "panel_id": "zoe-touch-pi",
                "chat_session_id": None,
                "action_type": "show_card",
                "payload": '{"source":"voice:skybridge","cards":[]}',
                "status": "queued",
                "requires_confirmation": 0,
                "confirmation_token": None,
                "retry_count": 0,
                "max_retries": 3,
                "created_at": "2026-06-14T00:00:00Z",
                "updated_at": "2026-06-14T00:00:00Z",
            }])
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_pending_actions_skips_superseded_skybridge_voice_cards(monkeypatch):
    async def allow(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ui_actions, "require_feature_access", allow)

    db = _PendingDb()
    # Since the panel-hijack fix (#921) /actions/pending is gated by
    # _authorize_panel: the poller must be the panel's own device-token
    # daemon (user dict carries panel_id) or a bound human session.
    result = await ui_actions.get_pending_ui_actions(
        panel_id="zoe-touch-pi",
        limit=10,
        user={"user_id": "guest", "role": "guest", "panel_id": "zoe-touch-pi"},
        db=db,
    )

    cleanup_sql, cleanup_params = next(
        (sql, params)
        for sql, params in db.executes
        if "Superseded by newer Skybridge voice card" in sql
    )
    assert "payload::jsonb->>'source' = 'voice:skybridge'" in cleanup_sql
    assert cleanup_params == ("guest", "zoe-touch-pi", "guest", "zoe-touch-pi")
    assert result["count"] == 1
    assert result["actions"][0]["id"] == "new-card"
    assert db.commits == 2
