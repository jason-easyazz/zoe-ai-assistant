"""Tests for Multica autopilot board noise controls."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import multica_autopilot_sync as mas

pytestmark = pytest.mark.ci_safe


@pytest.mark.asyncio
async def test_sync_autopilots_prefers_execution_mode(monkeypatch):
    captured_jobs: list[dict] = []

    class FakeCronTrigger:
        @classmethod
        def from_crontab(cls, expr, timezone=None):
            return {"expr": expr, "timezone": timezone}

    class FakeScheduler:
        def remove_job(self, job_id):
            raise LookupError(job_id)

        def add_job(self, func, *, trigger, id, replace_existing, kwargs):
            captured_jobs.append(
                {
                    "func": func,
                    "trigger": trigger,
                    "id": id,
                    "replace_existing": replace_existing,
                    "kwargs": kwargs,
                }
            )

    monkeypatch.setattr(mas, "_is_configured", lambda: True)
    monkeypatch.setattr(
        mas,
        "get_multica_autopilots",
        AsyncMock(
            return_value=[
                {
                    "id": "ap-run-only",
                    "title": "Morning Checkin",
                    "execution_mode": "run_only",
                    "mode": "create_issue",
                    "assignee_id": "agent-1",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        mas,
        "get_autopilot_triggers",
        AsyncMock(return_value=[{"cron_expression": "30 7 * * *"}]),
    )
    monkeypatch.setitem(
        sys.modules,
        "apscheduler.triggers.cron",
        types.SimpleNamespace(CronTrigger=FakeCronTrigger),
    )

    registered = await mas.sync_autopilots_from_multica(FakeScheduler())

    assert registered == 1
    assert captured_jobs == [
        {
            "func": mas._fire_autopilot_job,
            "trigger": {"expr": "30 7 * * *", "timezone": mas._TZ},
            "id": "multica_autopilot_ap-run-only",
            "replace_existing": True,
            "kwargs": {
                "autopilot_id": "ap-run-only",
                "autopilot_title": "Morning Checkin",
                "mode": "run_only",
                "assignee_agent_id": "agent-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_sync_autopilots_logs_missing_mode_default(monkeypatch, caplog):
    captured_jobs: list[dict] = []

    class FakeCronTrigger:
        @classmethod
        def from_crontab(cls, expr, timezone=None):
            return {"expr": expr, "timezone": timezone}

    class FakeScheduler:
        def remove_job(self, job_id):
            raise LookupError(job_id)

        def add_job(self, func, *, trigger, id, replace_existing, kwargs):
            captured_jobs.append(kwargs)

    monkeypatch.setattr(mas, "_is_configured", lambda: True)
    monkeypatch.setattr(
        mas,
        "get_multica_autopilots",
        AsyncMock(return_value=[{"id": "ap-missing-mode", "title": "Legacy"}]),
    )
    monkeypatch.setattr(
        mas,
        "get_autopilot_triggers",
        AsyncMock(return_value=[{"cron_expression": "0 8 * * *"}]),
    )
    monkeypatch.setitem(
        sys.modules,
        "apscheduler.triggers.cron",
        types.SimpleNamespace(CronTrigger=FakeCronTrigger),
    )

    with caplog.at_level("DEBUG", logger=mas.logger.name):
        registered = await mas.sync_autopilots_from_multica(FakeScheduler())

    assert registered == 1
    assert captured_jobs[0]["mode"] == "create_issue"
    assert "has no execution_mode/mode" in caplog.text


def test_should_create_tracker_issue_default_off_for_mapped_task():
    fn = lambda: None  # noqa: E731
    with patch.object(mas, "_CREATE_ISSUES", False):
        assert mas._should_create_tracker_issue("Board Review", "create_issue", fn) is False


@pytest.mark.asyncio
async def test_board_review_autopilot_guard_off_returns_early(monkeypatch):
    """When _BOARD_REVIEW_AUTOPILOT_ENABLED is False (default), _run_board_review logs and
    returns without touching multica_client or engineering_workflow."""
    monkeypatch.setattr(mas, "_BOARD_REVIEW_AUTOPILOT_ENABLED", False)
    result = await mas._run_board_review()
    assert result is None


@pytest.mark.asyncio
async def test_board_review_autopilot_guard_on_reaches_client(monkeypatch):
    """When _BOARD_REVIEW_AUTOPILOT_ENABLED is True, _run_board_review proceeds past the
    guard and calls get_multica_client()."""
    import sys
    import types

    monkeypatch.setattr(mas, "_BOARD_REVIEW_AUTOPILOT_ENABLED", True)

    # Stub multica_client so we don't need a real DB connection
    fake_client = types.SimpleNamespace(is_configured=lambda: False)
    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: fake_client),
    )
    monkeypatch.setitem(
        sys.modules,
        "engineering_workflow",
        types.SimpleNamespace(create_and_start_engineering_task=AsyncMock()),
    )
    # is_configured() returns False → function exits cleanly after the guard
    result = await mas._run_board_review()
    assert result is None


def test_should_create_tracker_issue_allowlist():
    fn = lambda: None  # noqa: E731
    with patch.object(mas, "_is_configured", lambda: True):
        with patch.object(mas, "_CREATE_ISSUES", False):
            with patch.object(mas, "_CREATE_ISSUES_FOR", {"platform health check"}):
                assert mas._should_create_tracker_issue("Platform Health Check", "create_issue", fn) is True


def test_platform_health_never_creates_wrapper_issue():
    with patch.object(mas, "_is_configured", lambda: True):
        with patch.object(mas, "_CREATE_ISSUES", True):
            with patch.object(mas, "_CREATE_ISSUES_FOR", {"platform health check"}):
                assert mas._should_create_tracker_issue(
                    "Platform Health Check",
                    "create_issue",
                    mas._run_platform_health_check,
                ) is False


@pytest.mark.asyncio
async def test_platform_health_failure_reuses_open_issue(monkeypatch, tmp_path):
    script = tmp_path / "health.sh"
    script.write_text("#!/bin/sh\necho broken\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    updates = []
    creates = []
    list_calls = []

    class FakeClient:
        async def list_issues(self, status=None, *, limit=None):
            assert limit == 1000
            list_calls.append(status)
            return [
                {
                    "id": "health-1",
                    "title": "Platform health failures detected",
                    "status": "blocked",
                }
            ] if status == "blocked" else []

        async def update_issue(self, issue_id, status=None, **kwargs):
            updates.append((issue_id, status, kwargs))
            return {"id": issue_id}

        async def create_issue(self, **kwargs):
            creates.append(kwargs)
            return {"id": "new"}

    monkeypatch.setenv("ZOE_HEALTH_CHECK_SCRIPT", str(script))
    monkeypatch.setattr(mas, "_is_configured", lambda: True)
    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    with pytest.raises(RuntimeError, match="Platform health check failed"):
        await mas._run_platform_health_check()

    assert creates == []
    assert list_calls == ["backlog", "todo", "in_progress", "blocked", "in_review"]
    assert updates == [
        (
            "health-1",
            None,
            {
                "description": (
                    "The scheduled Platform Health Check found failing services.\n\n"
                    "```\nbroken\n```"
                ),
            },
        )
    ]


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
