"""The optional ``source`` filter on GET /api/ui/actions/pending.

Without it, a caller that only wants one card class (the contact-offer poller)
filters client-side AFTER the LIMIT, so its action can be starved behind >=limit
older queued actions. When ``source`` is passed, the filter is applied in SQL
(before the LIMIT), guaranteeing the action is returned.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.ci_safe

import routers.ui_actions as ui_actions  # noqa: E402
from routers.ui_actions import get_pending_ui_actions  # noqa: E402


class _Cursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _PendingDb:
    """Fakes the pending-endpoint DB call sequence and captures the final SELECT."""

    def __init__(self):
        self.final_select = None

    async def execute(self, sql, params=()):
        if "FROM ui_panel_sessions" in sql:
            return _Cursor(row={"user_id": "panel-owner"})
        if sql.strip().startswith("UPDATE"):
            return _Cursor()
        if "SELECT id, panel_id, chat_session_id" in sql:
            self.final_select = (sql, params)
            return _Cursor(rows=[])
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        pass


async def _call(monkeypatch, **kw):
    db = _PendingDb()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ui_actions, "require_feature_access", _noop)
    monkeypatch.setattr(ui_actions, "_authorize_panel", _noop)
    await get_pending_ui_actions(
        panel_id="zoe-touch-pi",
        user={"user_id": "browser-user"},
        db=db,
        **kw,
    )
    return db


@pytest.mark.asyncio
async def test_source_filter_added_to_sql(monkeypatch):
    db = await _call(monkeypatch, source="contact_offer", limit=10)
    sql, params = db.final_select
    assert "payload::jsonb->>'source' = ?" in sql
    # source param sits between panel_id and the trailing limit
    assert params == ("panel-owner", "zoe-touch-pi", "contact_offer", 10)


@pytest.mark.asyncio
async def test_no_source_leaves_query_unfiltered(monkeypatch):
    db = await _call(monkeypatch, source=None, limit=20)
    sql, params = db.final_select
    assert "payload::jsonb->>'source' = ?" not in sql
    assert params == ("panel-owner", "zoe-touch-pi", 20)
