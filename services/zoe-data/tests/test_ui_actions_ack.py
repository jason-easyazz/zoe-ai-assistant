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
            assert "panel_id" in sql
            assert params == ("action-1", "action-1", "browser-user", "zoe-touch-pi", "zoe-touch-pi")
            return _Cursor({
                "id": "action-1",
                "user_id": "panel-owner",
                "panel_id": "zoe-touch-pi",
                "status": "queued",
            })
        if "UPDATE ui_actions" in sql:
            self.updated.append((sql, params))
            assert params[-2:] == ("action-1", "panel-owner")
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
        {"status": "success", "panel_id": "zoe-touch-pi", "ui_context": {"page": "/touch/skybridge.html"}},
        user={"user_id": "browser-user", "role": "guest"},
        db=db,
    )

    assert result == {"status": "ok", "action_id": "action-1", "state": "success"}
    assert len(db.updated) == 1
    assert len(db.ledger) == 1
    assert db.commits == 1
