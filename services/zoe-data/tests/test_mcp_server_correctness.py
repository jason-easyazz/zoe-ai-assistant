"""Regression tests for scoped correctness fixes in mcp_server._execute_tool.

Covers:
- #1 user-id resolution: an explicit ``user_id`` target must survive the
  framework-injected ``_user_id`` (the old eager-default pop discarded it, so
  proactive_schedule targeted the caller instead of the requested user).
- #3 journal_get_streak: operate on native asyncpg date objects (streak used to
  be stuck at 0 and raised TypeError once two distinct days existed).
- #2 journal_on_this_day: created_at is TEXT, so cast text->timestamp before
  Postgres ``to_char(created_at::timestamp, 'MM-DD')`` and
  ``created_at::timestamp::date < CURRENT_DATE`` (not SQLite
  ``strftime``/``date('now')`` and not an uncast ``to_char``/``::date``).
- #4 flag_needs_human_review: report the true outcome via ``push_status``
  (failed / suppressed_quiet_hours / submitted) with ``push_sent`` always False,
  since fire_notification returns None in every path (including quiet-hours
  suppression) and never confirms device delivery.
"""

import asyncio
import json
import os
import sys
import types
from contextlib import asynccontextmanager
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

    async def execute(self, sql, *params):
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = tuple(params[0])
        self.calls.append((sql, params))
        for needle, rows in self._routes.items():
            if needle in sql:
                return _Cursor(rows)
        return _Cursor([])


class _DashboardTxn:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        await self._db.lock.acquire()
        self._db.active_transactions += 1
        self._db.max_active_transactions = max(
            self._db.max_active_transactions,
            self._db.active_transactions,
        )
        return self

    async def __aexit__(self, *_args):
        self._db.active_transactions -= 1
        self._db.lock.release()


class _DashboardLayoutDb:
    def __init__(self, layout=None):
        self.layout = layout
        self.calls = []
        self.lock = asyncio.Lock()
        self.active_transactions = 0
        self.max_active_transactions = 0

    def transaction(self):
        return _DashboardTxn(self)

    async def fetchrow(self, sql, *params):
        self.calls.append((sql, params))
        if self.layout is None:
            return None
        return {"layout": json.dumps(self.layout)}

    async def execute(self, sql, *params):
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = tuple(params[0])
        self.calls.append((sql, params))
        if sql.startswith("INSERT INTO dashboard_layouts"):
            if self.layout is None:
                self.layout = json.loads(params[1])
            return "INSERT 0 1"
        if sql.startswith("UPDATE dashboard_layouts"):
            self.layout = json.loads(params[0])
            return "UPDATE 1"
        return _Cursor([])


class _SlowMcpDashboardSaveDb(_DashboardLayoutDb):
    def __init__(self, layout=None):
        super().__init__(layout=layout)
        self.save_update_started = asyncio.Event()
        self.release_save_update = asyncio.Event()
        self._held_save_once = False

    async def execute(self, sql, *params):
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = tuple(params[0])
        if (
            sql.startswith("UPDATE dashboard_layouts")
            and not self._held_save_once
            and params[0] == json.dumps([{"id": "tasks", "x": 0, "y": 0, "w": 2, "h": 3}])
        ):
            self._held_save_once = True
            self.save_update_started.set()
            await self.release_save_update.wait()
        return await super().execute(sql, *params)


# --------------------------------------------------------------------------
# MCP actor authorization — tools/call must trust transport/session context,
# not caller-supplied _user_id/user_id arguments.
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stdio_no_context_still_falls_back_to_family_admin_for_local_calls():
    db = _RoutingDb({"dashboard_layouts": []})

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={},
        actor_context={},
    )

    sql, params = db.calls[0]
    assert "FROM dashboard_layouts" in sql
    assert params == ("family-admin",)


@pytest.mark.asyncio
async def test_handle_tool_without_actor_context_preserves_internal_user_id(monkeypatch):
    db = _RoutingDb({"FROM notes": []})

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(mcp_server, "_pg_get_db", fake_db_ctx)

    result = json.loads(
        await mcp_server.handle_tool(
            "note_search",
            {"_user_id": "jason", "query": "birthday"},
        )
    )

    assert result == {"notes": []}
    sql, params = next(call for call in db.calls if "FROM notes" in call[0])
    assert params == ("%birthday%", "%birthday%", "jason")


@pytest.mark.asyncio
async def test_legacy_explicit_user_uses_db_role_for_cross_user_target():
    db = _RoutingDb(
        {
            "SELECT role FROM users": [{"role": "member"}],
            "dashboard_layouts": [],
        }
    )

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={"_user_id": "jason", "user_id": "victim"},
    )

    sql, params = next(call for call in db.calls if "FROM dashboard_layouts" in call[0])
    assert params == ("jason",)


@pytest.mark.asyncio
async def test_non_admin_transport_actor_cannot_override_dashboard_target():
    db = _RoutingDb({"dashboard_layouts": []})

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={
            "_user_id": "victim-via-caller-arg",
            "user_id": "victim",
        },
        actor_context={"user_id": "jason", "role": "member", "source": "test-session"},
    )

    sql, params = next(call for call in db.calls if "FROM dashboard_layouts" in call[0])
    assert "FROM dashboard_layouts" in sql
    assert params == ("jason",)


@pytest.mark.asyncio
async def test_meta_actor_role_admin_is_ignored_when_db_role_is_member():
    db = _RoutingDb(
        {
            "SELECT role FROM users": [{"role": "member"}],
            "dashboard_layouts": [],
        }
    )
    actor_context = mcp_server._trusted_actor_context_from_message(
        {
            "params": {
                "_meta": {
                    "zoe": {
                        "actor_user_id": "jason",
                        "actor_role": "admin",
                    }
                }
            }
        }
    )

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={"user_id": "victim"},
        actor_context=actor_context,
    )

    assert actor_context == {
        "user_id": "jason",
        "role": None,
        "role_source": None,
        "source": "transport",
    }
    sql, params = next(call for call in db.calls if "FROM dashboard_layouts" in call[0])
    assert params == ("jason",)


@pytest.mark.asyncio
async def test_env_actor_role_is_ignored_when_actor_user_comes_from_meta(monkeypatch):
    monkeypatch.setenv("ZOE_MCP_ACTOR_USER_ID", "env-admin")
    monkeypatch.setenv("ZOE_MCP_ACTOR_ROLE", "admin")
    db = _RoutingDb(
        {
            "SELECT role FROM users": [{"role": "member"}],
            "dashboard_layouts": [],
        }
    )
    actor_context = mcp_server._trusted_actor_context_from_message(
        {"params": {"_meta": {"zoe": {"actor_user_id": "jason"}}}}
    )

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={"user_id": "victim"},
        actor_context=actor_context,
    )

    assert actor_context == {
        "user_id": "jason",
        "role": None,
        "role_source": None,
        "source": "transport",
    }
    sql, params = next(call for call in db.calls if "FROM dashboard_layouts" in call[0])
    assert params == ("jason",)


@pytest.mark.asyncio
async def test_env_actor_role_is_trusted_when_actor_user_also_comes_from_env(monkeypatch):
    monkeypatch.setenv("ZOE_MCP_ACTOR_USER_ID", "ops-admin")
    monkeypatch.setenv("ZOE_MCP_ACTOR_ROLE", "admin")
    db = _RoutingDb({"dashboard_layouts": []})
    actor_context = mcp_server._trusted_actor_context_from_message({"params": {}})

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={"user_id": "target"},
        actor_context=actor_context,
    )

    assert actor_context == {
        "user_id": "ops-admin",
        "role": "admin",
        "role_source": "env",
        "source": "transport",
    }
    sql, params = next(call for call in db.calls if "FROM dashboard_layouts" in call[0])
    assert params == ("target",)


def test_dashboard_add_widget_schema_allows_admin_user_target():
    tool = next(t for t in mcp_server.TOOLS if t["name"] == "dashboard_add_widget")

    assert "user_id" in tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["properties"]["user_id"]["type"] == "string"


@pytest.mark.asyncio
async def test_admin_transport_actor_can_target_another_dashboard_user():
    db = _RoutingDb({"dashboard_layouts": []})

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={"user_id": "target"},
        actor_context={"user_id": "family-admin", "role": "admin", "source": "test-session"},
    )

    sql, params = db.calls[0]
    assert "FROM dashboard_layouts" in sql
    assert params == ("target",)


@pytest.mark.asyncio
async def test_non_admin_transport_actor_cannot_override_portrait_target(monkeypatch):
    captured = {}

    async def fake_load_portrait(user_id):
        captured["user_id"] = user_id
        return {"summary": "actor only"}

    monkeypatch.setitem(
        sys.modules,
        "user_portrait",
        types.SimpleNamespace(load_portrait=fake_load_portrait),
    )

    result = await mcp_server._execute_tool(
        db=None,
        name="user_portrait_get",
        args={"_user_id": "victim-via-caller-arg", "user_id": "victim"},
        actor_context={"user_id": "jason", "role": "member", "source": "test-session"},
    )

    assert captured["user_id"] == "jason"
    assert result["user_id"] == "jason"
    assert result["has_portrait"] is True


# --------------------------------------------------------------------------
# #1 — explicit user_id target survives the injected _user_id (proactive_schedule)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_proactive_schedule_admin_can_target_explicit_user(monkeypatch):
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
            "_user_id": "family-admin",
            "user_id": "target",
            "message": "drink water",
            "send_at": send_at,
        },
    )

    assert result["status"] == "scheduled"
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


@pytest.mark.asyncio
async def test_dashboard_get_layout_targets_explicit_user_not_caller():
    # No stored layout -> "No layout saved yet"; we only need the bound query param.
    db = _RoutingDb({"dashboard_layouts": []})

    await mcp_server._execute_tool(
        db=db,
        name="dashboard_get_layout",
        args={"_user_id": "family-admin", "user_id": "target"},
    )

    sql, params = db.calls[0]
    assert "FROM dashboard_layouts" in sql
    assert params == ("target",)


@pytest.mark.asyncio
async def test_dashboard_save_layout_targets_explicit_user_not_caller():
    db = _DashboardLayoutDb()

    result = await mcp_server._execute_tool(
        db=db,
        name="dashboard_save_layout",
        args={"_user_id": "family-admin", "user_id": "target", "layout": [{"w": "tasks"}]},
    )

    assert result["status"] == "ok"
    sql, params = db.calls[0]
    assert "INSERT INTO dashboard_layouts" in sql
    # uid is the first bound param of the ensure-row insert.
    assert params[0] == "target"
    assert any("FOR UPDATE" in sql for sql, _params in db.calls)


@pytest.mark.asyncio
async def test_dashboard_save_layout_serializes_with_add_widget_on_layout_lock():
    db = _SlowMcpDashboardSaveDb(
        layout=[{"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2}]
    )

    save_task = asyncio.create_task(
        mcp_server._execute_tool(
            db=db,
            name="dashboard_save_layout",
            args={
                "_user_id": "u1",
                "layout": [{"id": "tasks", "x": 0, "y": 0, "w": 2, "h": 3}],
            },
        )
    )
    await db.save_update_started.wait()

    add_task = asyncio.create_task(
        mcp_server._execute_tool(
            db=db,
            name="dashboard_add_widget",
            args={"_user_id": "u1", "widgets": ["events"]},
        )
    )

    await asyncio.sleep(0)
    assert not add_task.done()

    db.release_save_update.set()
    save_result, add_result = await asyncio.gather(save_task, add_task)

    assert save_result == {"status": "ok"}
    assert add_result == {"status": "ok", "added": ["events"]}
    assert {widget["id"] for widget in db.layout} == {"tasks", "events"}
    assert db.max_active_transactions == 1
    assert sum("FOR UPDATE" in sql for sql, _params in db.calls) >= 2


@pytest.mark.asyncio
async def test_user_portrait_get_targets_explicit_user_not_caller(monkeypatch):
    captured = {}

    async def fake_load_portrait(user_id):
        captured["user_id"] = user_id
        return {"summary": "knows target"}

    monkeypatch.setitem(
        sys.modules,
        "user_portrait",
        types.SimpleNamespace(load_portrait=fake_load_portrait),
    )

    result = await mcp_server._execute_tool(
        db=None,
        name="user_portrait_get",
        args={"_user_id": "family-admin", "user_id": "target"},
    )

    assert captured["user_id"] == "target"
    assert result["user_id"] == "target"
    assert result["has_portrait"] is True


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
            "DISTINCT created_at::timestamp::date": day_rows,
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
            "DISTINCT created_at::timestamp::date": day_rows,
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

    sql, params = next(call for call in db.calls if "journal_entries" in call[0])
    assert "strftime" not in sql
    assert "date('now')" not in sql
    # created_at is TEXT in the live schema, so to_char and the date comparison
    # MUST cast text->timestamp first — an uncast to_char(created_at, ...) /
    # created_at::date errors on Postgres.
    assert "to_char(created_at::timestamp, 'MM-DD')" in sql
    assert "created_at::timestamp::date < CURRENT_DATE" in sql
    assert "to_char(created_at," not in sql
    assert "to_char(created_at)" not in sql
    # The bound parameter must be the MM-DD string matching to_char's format.
    assert params[1] == date.today().strftime("%m-%d")


# --------------------------------------------------------------------------
# #4 — flag_needs_human_review reports the TRUE outcome. fire_notification
#      returns None in EVERY path (sent, deferred, or suppressed-by-quiet-hours)
#      and raises only on error — it never confirms device delivery. The tool
#      must therefore distinguish failed / suppressed_quiet_hours / submitted,
#      never claim a delivery, and keep push_sent False (unconfirmable).
# --------------------------------------------------------------------------
class _UnconfiguredMulticaClient:
    def is_configured(self):
        return False


def _fake_engine(monkeypatch, *, fire, quiet_hours, sink=None):
    """Install a fake proactive.engine exposing fire_notification + _is_in_quiet_hours."""
    async def fire_notification(**kwargs):
        if sink is not None:
            sink.update(kwargs)
        return await fire(**kwargs)

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: _UnconfiguredMulticaClient()),
    )
    monkeypatch.setitem(
        sys.modules,
        "proactive.engine",
        types.SimpleNamespace(
            fire_notification=fire_notification,
            _is_in_quiet_hours=lambda: quiet_hours,
        ),
    )


@pytest.mark.asyncio
async def test_flag_needs_human_review_reports_failed_on_exception(monkeypatch):
    async def boom(**_kwargs):
        raise RuntimeError("push backend down")

    _fake_engine(monkeypatch, fire=boom, quiet_hours=False)

    result = await mcp_server._execute_tool(
        db=None,
        name="flag_needs_human_review",
        args={"_user_id": "jason", "reason": "needs a look", "urgency": "normal"},
    )

    assert result["ok"] is True
    assert result["push_status"] == "failed"
    assert result["push_sent"] is False


@pytest.mark.asyncio
async def test_flag_needs_human_review_reports_submitted_not_delivered(monkeypatch):
    sent = {}

    async def ok(**_kwargs):
        # Real contract: returns None for an attempted dispatch (no delivery info).
        return None

    # Not in quiet hours -> the engine attempts delivery, but we can't confirm it.
    _fake_engine(monkeypatch, fire=ok, quiet_hours=False, sink=sent)

    result = await mcp_server._execute_tool(
        db=None,
        name="flag_needs_human_review",
        args={"_user_id": "jason", "reason": "needs a look", "urgency": "normal"},
    )

    assert result["ok"] is True
    # Attempted but unconfirmable — never claim sent/delivered.
    assert result["push_status"] == "submitted"
    assert result["push_sent"] is False
    assert sent["user_id"] == "jason"
    assert sent["context"]["force_send"] is False


@pytest.mark.asyncio
async def test_flag_needs_human_review_reports_suppressed_during_quiet_hours(monkeypatch):
    """Quiet hours + normal urgency: fire_notification still returns None but
    suppresses the alert. The tool must NOT call that submitted/queued."""
    async def ok(**_kwargs):
        return None

    _fake_engine(monkeypatch, fire=ok, quiet_hours=True)

    result = await mcp_server._execute_tool(
        db=None,
        name="flag_needs_human_review",
        args={"_user_id": "jason", "reason": "needs a look", "urgency": "normal"},
    )

    assert result["ok"] is True
    assert result["push_status"] == "suppressed_quiet_hours"
    assert result["push_sent"] is False


@pytest.mark.asyncio
async def test_flag_needs_human_review_high_urgency_bypasses_quiet_hours(monkeypatch):
    """urgency=high forces force_send=True, so quiet hours do NOT suppress — the
    alert is submitted (still delivery-unconfirmed)."""
    sent = {}

    async def ok(**_kwargs):
        return None

    _fake_engine(monkeypatch, fire=ok, quiet_hours=True, sink=sent)

    result = await mcp_server._execute_tool(
        db=None,
        name="flag_needs_human_review",
        args={"_user_id": "jason", "reason": "urgent", "urgency": "high"},
    )

    assert result["ok"] is True
    assert result["push_status"] == "submitted"
    assert result["push_sent"] is False
    assert sent["context"]["force_send"] is True


# --------------------------------------------------------------------------
# #2/#3 (live Postgres) — the journal queries must run against a TEXT created_at
# column. to_char()/::date on raw text errors on Postgres; this exercises the
# real SQL end-to-end so the missing cast would be caught. Skips when no DB.
# --------------------------------------------------------------------------
def _anniversary(today):
    """today's month/day in a prior year (handles Feb 29)."""
    y = today.year - 1
    while True:
        try:
            return today.replace(year=y)
        except ValueError:
            y -= 1


@pytest.mark.asyncio
async def test_journal_queries_run_against_text_created_at_on_postgres():
    postgres_url = os.environ.get("POSTGRES_URL")
    if not postgres_url:
        pytest.skip("POSTGRES_URL not set — no live Postgres to validate TEXT casts")
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        pytest.skip("asyncpg not installed")

    import db_pool

    conn = await asyncpg.connect(postgres_url)
    try:
        # TEMP TABLE shadows the real journal_entries for this connection only
        # (created_at TEXT mirrors the live schema), so no production rows change.
        await conn.execute(
            """CREATE TEMP TABLE journal_entries (
                   id text, user_id text, title text, mood text,
                   created_at text, deleted int
               )"""
        )
        today = date.today()
        anniv = _anniversary(today)
        ts = lambda d: f"{d.isoformat()} 09:00:00+00"  # noqa: E731 — matches NOW()::text shape

        # on_this_day fixtures for user U1: a past-year anniversary (expected) and
        # a same-MM-DD entry dated today (must be excluded by < CURRENT_DATE).
        await conn.execute(
            "INSERT INTO journal_entries VALUES ($1,$2,$3,$4,$5,$6)",
            "anniv", "U1", "one year ago", "happy", ts(anniv), 0,
        )
        await conn.execute(
            "INSERT INTO journal_entries VALUES ($1,$2,$3,$4,$5,$6)",
            "today", "U1", "today", "ok", ts(today), 0,
        )
        # streak fixtures for user U2: two consecutive days.
        await conn.execute(
            "INSERT INTO journal_entries VALUES ($1,$2,$3,$4,$5,$6)",
            "s0", "U2", "d0", "ok", ts(today), 0,
        )
        await conn.execute(
            "INSERT INTO journal_entries VALUES ($1,$2,$3,$4,$5,$6)",
            "s1", "U2", "d1", "ok", ts(today - timedelta(days=1)), 0,
        )

        db = db_pool.AsyncpgCompat(conn)

        on_this_day = await mcp_server._execute_tool(
            db=db, name="journal_on_this_day", args={"_user_id": "U1"}
        )
        ids = {e["id"] for e in on_this_day["entries"]}
        assert "anniv" in ids          # past anniversary surfaced
        assert "today" not in ids      # today excluded by created_at::timestamp::date < CURRENT_DATE

        streak = await mcp_server._execute_tool(
            db=db, name="journal_get_streak", args={"_user_id": "U2"}
        )
        assert streak["total_entries"] == 2
        assert streak["current_streak"] == 2
    finally:
        await conn.close()
