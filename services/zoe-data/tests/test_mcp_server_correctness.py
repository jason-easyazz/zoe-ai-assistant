"""Regression tests for scoped correctness fixes in mcp_server._execute_tool.

Covers:
- #1 user-id resolution: an explicit ``user_id`` target must survive the
  framework-injected ``_user_id`` (the old eager-default pop discarded it, so
  proactive_schedule targeted the caller instead of the requested user).
- #3 journal_get_streak: operate on native asyncpg date objects (streak used to
  be stuck at 0 and raised TypeError once two distinct days existed).
- #2 journal_on_this_day: Postgres ``to_char`` instead of SQLite ``strftime``.
- #4 flag_needs_human_review: report the real push-sent flag, not a hardcoded
  success when the notification fire failed.
"""

import os
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mcp_server


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _RoutingDb:
    """Fake db that returns rows based on a substring match of the SQL."""

    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for needle, rows in self._routes.items():
            if needle in sql:
                return _Cursor(rows)
        return _Cursor([])


# --------------------------------------------------------------------------
# #1 — explicit user_id target survives the injected _user_id (proactive_schedule)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_proactive_schedule_targets_explicit_user_not_caller(monkeypatch):
    captured = {}

    async def fake_schedule_reminder(*, user_id, message, send_at):
        captured["user_id"] = user_id
        captured["message"] = message
        return "reminder-123"

    monkeypatch.setitem(
        sys.modules,
        "proactive.triggers.reminders",
        types.SimpleNamespace(schedule_reminder=fake_schedule_reminder),
    )

    send_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    result = await mcp_server._execute_tool(
        db=None,
        name="proactive_schedule",
        args={
            "_user_id": "caller",          # framework-injected caller identity
            "user_id": "target",           # explicit target the tool asked for
            "message": "drink water",
            "send_at": send_at,
        },
    )

    assert result["status"] == "scheduled"
    # The explicit target must win — not the caller that was injected by the framework.
    assert captured["user_id"] == "target"


@pytest.mark.asyncio
async def test_proactive_schedule_falls_back_to_caller_when_no_explicit_target(monkeypatch):
    captured = {}

    async def fake_schedule_reminder(*, user_id, message, send_at):
        captured["user_id"] = user_id
        return "reminder-456"

    monkeypatch.setitem(
        sys.modules,
        "proactive.triggers.reminders",
        types.SimpleNamespace(schedule_reminder=fake_schedule_reminder),
    )

    send_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    await mcp_server._execute_tool(
        db=None,
        name="proactive_schedule",
        args={"_user_id": "caller", "message": "stretch", "send_at": send_at},
    )

    assert captured["user_id"] == "caller"


@pytest.mark.asyncio
async def test_web_search_uses_resolved_caller_identity(monkeypatch):
    captured = {}

    async def fake_web_search_ddg(query, user_id=""):
        captured["user_id"] = user_id
        captured["query"] = query
        return "results"

    monkeypatch.setitem(
        sys.modules,
        "zoe_agent",
        types.SimpleNamespace(_web_search_ddg=fake_web_search_ddg),
    )

    result = await mcp_server._execute_tool(
        db=None,
        name="web_search",
        args={"_user_id": "caller", "query": "weather"},
    )

    assert result["query"] == "weather"
    # _user_id was popped for identity resolution; the caller must still reach the search.
    assert captured["user_id"] == "caller"


# --------------------------------------------------------------------------
# #3 — journal_get_streak operates on date objects
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_journal_get_streak_counts_consecutive_date_objects():
    today = date.today()
    # Distinct days (asyncpg-style date objects), newest first not required:
    # today, today-1  (current streak = 2)
    # then a gap, then today-3, today-4, today-5 (longest run = 3)
    day_rows = [
        (today,),
        (today - timedelta(days=1),),
        (today - timedelta(days=3),),
        (today - timedelta(days=4),),
        (today - timedelta(days=5),),
    ]
    db = _RoutingDb(
        {
            "COUNT(*)": [(len(day_rows),)],
            "DISTINCT date(created_at)": day_rows,
        }
    )

    result = await mcp_server._execute_tool(
        db=db,
        name="journal_get_streak",
        args={"_user_id": "jason"},
    )

    assert result["total_entries"] == 5
    assert result["current_streak"] == 2
    assert result["longest_streak"] == 3


@pytest.mark.asyncio
async def test_journal_get_streak_handles_iso_string_rows():
    """Legacy/SQLite rows may hand back ISO strings — must not raise, must count."""
    today = date.today()
    day_rows = [
        (today.isoformat(),),
        ((today - timedelta(days=1)).isoformat(),),
    ]
    db = _RoutingDb(
        {
            "COUNT(*)": [(2,)],
            "DISTINCT date(created_at)": day_rows,
        }
    )

    result = await mcp_server._execute_tool(
        db=db,
        name="journal_get_streak",
        args={"_user_id": "jason"},
    )

    assert result["current_streak"] == 2
    assert result["longest_streak"] == 2


# --------------------------------------------------------------------------
# #2 — journal_on_this_day uses Postgres to_char, not SQLite strftime
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_journal_on_this_day_uses_postgres_to_char():
    db = _RoutingDb({"journal_entries": []})

    await mcp_server._execute_tool(
        db=db,
        name="journal_on_this_day",
        args={"_user_id": "jason"},
    )

    sql, params = db.calls[0]
    assert "to_char(created_at, 'MM-DD')" in sql
    assert "strftime" not in sql
    # The bound parameter must be the MM-DD string matching to_char's format.
    assert params[1] == date.today().strftime("%m-%d")


# --------------------------------------------------------------------------
# #4 — flag_needs_human_review reports the real push-sent flag
# --------------------------------------------------------------------------
class _UnconfiguredMulticaClient:
    def is_configured(self):
        return False


@pytest.mark.asyncio
async def test_flag_needs_human_review_reports_push_failure(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: _UnconfiguredMulticaClient()),
    )

    async def failing_fire_notification(**_kwargs):
        raise RuntimeError("push backend down")

    monkeypatch.setitem(
        sys.modules,
        "proactive.engine",
        types.SimpleNamespace(fire_notification=failing_fire_notification),
    )

    result = await mcp_server._execute_tool(
        db=None,
        name="flag_needs_human_review",
        args={"_user_id": "jason", "reason": "needs a look", "urgency": "normal"},
    )

    assert result["ok"] is True
    # The push genuinely failed — must not claim success.
    assert result["push_sent"] is False


@pytest.mark.asyncio
async def test_flag_needs_human_review_reports_push_success(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: _UnconfiguredMulticaClient()),
    )

    sent = {}

    async def ok_fire_notification(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setitem(
        sys.modules,
        "proactive.engine",
        types.SimpleNamespace(fire_notification=ok_fire_notification),
    )

    result = await mcp_server._execute_tool(
        db=None,
        name="flag_needs_human_review",
        args={"_user_id": "jason", "reason": "needs a look", "urgency": "high"},
    )

    assert result["ok"] is True
    assert result["push_sent"] is True
    assert sent["user_id"] == "jason"
