"""background_runner after the executor was deleted (deep dive S0-7).

It used to launch ``hermes --accept-hooks`` with auto-approve as the zoe user for any text a chat / voice / A2A
escalation enqueued. That path is gone; what remains is the read side (``get_pending_tasks``) and the watchdog.
The no-exec guarantee itself is pinned repo-wide by ``test_no_hermes_exec.py``.
"""
from __future__ import annotations

import sys
import types

import pytest

import background_runner as br

pytestmark = pytest.mark.ci_safe


def test_the_executor_is_gone_and_the_live_surface_remains():
    for gone in ("enqueue_background_task", "_run_task", "_run_hermes_background_task", "_background_profile",
                 "_background_runtime_s", "_background_queue_wait_s", "run_to_completion", "hermes_bin"):
        assert not hasattr(br, gone), f"background_runner.{gone} is back"
    assert callable(br.get_pending_tasks) and callable(br._watchdog_loop)


# ── the watchdog must not crash or be disabled by a bad env value ───────────

def test_watchdog_window_defaults_to_one_hour(monkeypatch):
    monkeypatch.delenv("ZOE_TASK_TIMEOUT_S", raising=False)
    assert br._watchdog_timeout_s() == 3600.0


def test_watchdog_window_honours_a_good_setting(monkeypatch):
    monkeypatch.setenv("ZOE_TASK_TIMEOUT_S", "900")
    assert br._watchdog_timeout_s() == 900.0


@pytest.mark.parametrize("bad", ["not-a-number", "nan", "inf", "10 minutes"])
def test_watchdog_window_falls_back_rather_than_crashing_or_disabling(monkeypatch, bad):
    """NaN defeats the cutoff comparison and inf disables the reaper; garbage must raise nothing."""
    monkeypatch.setenv("ZOE_TASK_TIMEOUT_S", bad)
    assert br._watchdog_timeout_s() == 3600.0


# ── get_pending_tasks: the genuinely live read path ─────────────────────────

class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    async def fetch(self, sql, *args):
        self.fetched = (sql, args)
        return self.rows

    async def execute(self, sql, *args):
        self.executions.append((sql, args))


class _FakeCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_get_pending_tasks_returns_done_unseen_rows_and_marks_them_seen(monkeypatch):
    db = _FakeDB([{"id": 7, "task": "t", "result": "r", "created_at": "c", "completed_at": "d"}])
    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db), get_db=None))

    out = await br.get_pending_tasks("user-x")

    assert out == [{"id": 7, "task": "t", "result": "r", "created_at": "c", "completed_at": "d"}]
    assert "status='done'" in db.fetched[0] and db.fetched[1] == ("user-x",)
    assert db.executions and "seen=1" in db.executions[0][0] and db.executions[0][1] == ([7],)


@pytest.mark.asyncio
async def test_get_pending_tasks_with_nothing_pending_writes_nothing(monkeypatch):
    db = _FakeDB([])
    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db), get_db=None))
    assert await br.get_pending_tasks("user-x") == []
    assert db.executions == []
