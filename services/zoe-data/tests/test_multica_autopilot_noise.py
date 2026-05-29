"""Tests for Multica autopilot board noise controls."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import multica_autopilot_sync as mas


def test_should_create_tracker_issue_default_off_for_mapped_task():
    fn = lambda: None  # noqa: E731
    with patch.object(mas, "_CREATE_ISSUES", False):
        assert mas._should_create_tracker_issue("Board Review", "create_issue", fn) is False


def test_should_create_tracker_issue_allowlist():
    fn = lambda: None  # noqa: E731
    with patch.object(mas, "_is_configured", lambda: True):
        with patch.object(mas, "_CREATE_ISSUES", False):
            with patch.object(mas, "_CREATE_ISSUES_FOR", {"platform health check"}):
                assert mas._should_create_tracker_issue("Platform Health Check", "create_issue", fn) is True


@pytest.mark.asyncio
async def test_fire_autopilot_job_skips_create_when_disabled(monkeypatch):
    posts: list = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "issue-1"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            posts.append(kwargs)
            return FakeResponse()

        async def put(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(mas, "_is_configured", lambda: True)
    monkeypatch.setattr(mas, "_CREATE_ISSUES", False)
    monkeypatch.setattr(mas, "_CREATE_ISSUES_FOR", set())
    monkeypatch.setattr(mas.httpx, "AsyncClient", lambda **kw: FakeClient())

    async def noop():
        return None

    monkeypatch.setattr(mas, "_zoe_task_for_autopilot", lambda _t: noop)

    await mas._fire_autopilot_job("ap-1", "Board Review", "create_issue", None)

    assert posts == []


@pytest.mark.asyncio
async def test_fire_autopilot_job_failure_uses_cancelled_not_todo(monkeypatch):
    statuses: list[str] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "issue-99"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

        async def put(self, url, json=None, **kwargs):
            if json:
                statuses.append(json.get("status", ""))
            return FakeResponse()

    monkeypatch.setattr(mas, "_is_configured", lambda: True)
    monkeypatch.setattr(mas, "_CREATE_ISSUES", True)
    monkeypatch.setattr(mas.httpx, "AsyncClient", lambda **kw: FakeClient())

    async def boom():
        raise RuntimeError("fail")

    monkeypatch.setattr(mas, "_zoe_task_for_autopilot", lambda _t: boom)

    await mas._fire_autopilot_job("ap-1", "Board Review", "create_issue", None)

    assert "cancelled" in statuses
    assert "todo" not in statuses


@pytest.mark.asyncio
async def test_close_stale_autopilot_wrappers_closes_old(monkeypatch):
    updates: list[str] = []

    class FakeClient:
        def is_configured(self):
            return True

        async def update_issue(self, issue_id, status=None, **kwargs):
            updates.append((issue_id, status))

    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = [
        {
            "id": "old-1",
            "title": "Autopilot: Board Review",
            "created_at": "2020-01-01T00:00:00Z",
        }
    ]
    list_resp.raise_for_status = MagicMock()

    class FakeHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return list_resp

    monkeypatch.setattr(mas, "_is_configured", lambda: True)
    monkeypatch.setattr(mas, "_STALE_AUTOPILOT_HOURS", 0.0)
    monkeypatch.setattr(mas.httpx, "AsyncClient", lambda **kw: FakeHttp())
    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )
    class _FakeDb:
        async def fetchrow(self, *args, **kwargs):
            return None

    class _FakeDbCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeDbCtx()),
    )

    n = await mas.close_stale_autopilot_wrappers(min_age_hours=0)

    assert n >= 1
    assert any(s == "done" for _, s in updates)
