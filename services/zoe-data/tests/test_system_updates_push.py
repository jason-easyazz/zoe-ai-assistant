"""Tests for Zoe update notification push behavior."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import system_updates  # noqa: E402

pytestmark = pytest.mark.ci_safe


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _Db:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []
        self.commits = 0

    async def execute(self, sql: str, params: tuple | None = None):
        self.calls.append((sql, params))
        return _Cursor(None)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_zoe_release_notification_awaits_push(monkeypatch):
    db = _Db()
    broadcast = AsyncMock(return_value=1)
    push_module = types.SimpleNamespace(
        broadcaster=types.SimpleNamespace(broadcast=broadcast)
    )
    monkeypatch.setitem(sys.modules, "push", push_module)
    monkeypatch.setattr(system_updates, "_is_dev_mode", lambda: False)
    monkeypatch.setattr(
        system_updates,
        "_fetch_github_latest_release",
        AsyncMock(return_value="v2.0.0"),
    )
    monkeypatch.setattr(system_updates, "_read_local_version", lambda: "v1.0.0")
    monkeypatch.setattr(system_updates, "_version_newer", lambda latest, current: True)

    await system_updates._check_and_notify_zoe_release(db)

    assert db.commits == 1
    broadcast.assert_awaited_once_with(
        "all",
        "notification_created",
        {
            "type": "zoe_update",
            "title": "Zoe v2.0.0 available",
            "message": "A new version of Zoe is ready to install.",
        },
    )
