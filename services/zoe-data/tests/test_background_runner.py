"""Tests for background_runner enqueue, task lifecycle, and Hermes routing."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import background_runner
import background_runner as br
from repo_paths import zoe_repo_root

pytestmark = pytest.mark.ci_safe


# ---------------------------------------------------------------------------
# Hermes worker-profile routing (main)
# ---------------------------------------------------------------------------


def test_background_profile_defaults_to_zoe_coder(monkeypatch):
    monkeypatch.delenv("HERMES_BACKGROUND_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_BACKGROUND_MODEL", raising=False)
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    assert br._background_profile() == "zoe-coder"


def test_background_profile_honours_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_BACKGROUND_PROFILE", "zoe-planner")
    assert br._background_profile() == "zoe-planner"


def test_zoe_repo_root_is_portable(monkeypatch):
    monkeypatch.delenv("ZOE_REPO_ROOT", raising=False)
    root = zoe_repo_root()
    assert (Path(root) / "services" / "zoe-data").is_dir()


@pytest.mark.asyncio
async def test_run_hermes_background_task_uses_worker_cli(monkeypatch):
    captured = {}

    async def fake_run_to_completion(cmd, **kwargs):
        captured["cmd"] = tuple(cmd)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(list(cmd), 0, stdout=b"done", stderr=b"")

    # The spawn goes through async_subprocess.run_to_completion (off-loop fork
    # rule), imported into background_runner's namespace.
    monkeypatch.setattr(br, "run_to_completion", fake_run_to_completion)
    monkeypatch.setenv("HERMES_BACKGROUND_PROFILE", "zoe-coder")

    result = await br._run_hermes_background_task("audit validators only", user_id="u1", task_id=99)

    assert result == "done"
    cmd = captured["cmd"]
    assert "-p" in cmd and "zoe-coder" in cmd
    assert "--accept-hooks" in cmd and "-z" in cmd
    assert "audit validators only" in cmd[-1]
    assert captured["kwargs"]["cwd"] == zoe_repo_root()


# ---------------------------------------------------------------------------
# enqueue / depth-guard / _run_task lifecycle
# ---------------------------------------------------------------------------


class _FakeDB:
    """Minimal async DB context that records calls."""

    def __init__(self):
        self._next_id = 42
        self.executions: list[tuple] = []

    async def fetchrow(self, sql, *args):
        self.executions.append(("fetchrow", sql, args))
        return {"id": self._next_id}

    async def execute(self, sql, *args):
        self.executions.append(("execute", sql, args))

    async def fetch(self, sql, *args):
        self.executions.append(("fetch", sql, args))
        return []


class _FakeCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_):
        return None


@pytest.fixture(autouse=True)
def _isolate_running_dict(monkeypatch):
    monkeypatch.setattr(background_runner, "_running", {})


@pytest.mark.asyncio
async def test_enqueue_rejects_excessive_depth():
    """Requests beyond _MAX_REQUEST_DEPTH must raise ValueError immediately."""
    with pytest.raises(ValueError, match="depth"):
        await background_runner.enqueue_background_task(
            task="do something",
            user_id="u1",
            request_depth=background_runner._MAX_REQUEST_DEPTH + 1,
        )


@pytest.mark.asyncio
async def test_enqueue_accepts_max_depth(monkeypatch):
    """Requests at exactly _MAX_REQUEST_DEPTH must be accepted."""
    db = _FakeDB()
    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db)),
    )

    async def _noop(*_a, **_kw):
        pass

    monkeypatch.setattr(background_runner, "_run_task", _noop)

    cancelled = []

    def fake_ensure_future(coro):
        fut = asyncio.get_event_loop().create_future()
        fut.cancel()
        cancelled.append(coro)
        return fut

    monkeypatch.setattr(asyncio, "ensure_future", fake_ensure_future)

    task_id = await background_runner.enqueue_background_task(
        task="borderline depth task",
        user_id="u1",
        request_depth=background_runner._MAX_REQUEST_DEPTH,
    )
    assert task_id == 42
    for c in cancelled:
        if hasattr(c, "close"):
            c.close()


@pytest.mark.asyncio
async def test_enqueue_inserts_row_and_returns_id(monkeypatch):
    """enqueue_background_task should INSERT a row and return the new task id."""
    db = _FakeDB()
    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db)),
    )

    async def _noop(*_a, **_kw):
        pass

    monkeypatch.setattr(background_runner, "_run_task", _noop)

    coros = []

    def fake_ensure_future(coro):
        fut = asyncio.get_event_loop().create_future()
        fut.cancel()
        coros.append(coro)
        return fut

    monkeypatch.setattr(asyncio, "ensure_future", fake_ensure_future)

    task_id = await background_runner.enqueue_background_task(
        task="find cheap flights",
        user_id="user-abc",
        session_id="sess-1",
    )

    assert task_id == 42
    assert any(
        "INSERT" in str(call) and "background_tasks" in str(call)
        for call in db.executions
    ), f"Expected INSERT into background_tasks in {db.executions}"

    for c in coros:
        if hasattr(c, "close"):
            c.close()


@pytest.mark.asyncio
async def test_run_task_marks_done_on_success(monkeypatch):
    """_run_task should set status='done' and store result when Hermes succeeds."""
    db = _FakeDB()
    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db)),
    )
    monkeypatch.setitem(
        sys.modules,
        "push",
        types.SimpleNamespace(broadcaster=types.SimpleNamespace(broadcast=_noop_async)),
    )
    monkeypatch.setitem(
        sys.modules,
        "engineering_workflow",
        types.SimpleNamespace(reconcile_background_task=_noop_async),
    )

    async def fake_hermes(task, *, user_id, task_id):
        return "Hotels found: Marriott $99"

    monkeypatch.setattr(background_runner, "_run_hermes_background_task", fake_hermes)

    await background_runner._run_task(99, "find hotels", "user-x", "sess-2")

    done_calls = [c for c in db.executions if "done" in str(c)]
    assert done_calls, f"Expected status=done in DB calls, got: {db.executions}"


@pytest.mark.asyncio
async def test_run_task_marks_error_on_failure(monkeypatch):
    """_run_task should set status='error' when Hermes raises an exception."""
    db = _FakeDB()
    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db)),
    )
    monkeypatch.setitem(
        sys.modules,
        "push",
        types.SimpleNamespace(broadcaster=types.SimpleNamespace(broadcast=_noop_async)),
    )
    monkeypatch.setitem(
        sys.modules,
        "engineering_workflow",
        types.SimpleNamespace(reconcile_background_task=_noop_async),
    )

    async def fail_hermes(task, *, user_id, task_id):
        raise RuntimeError("Hermes timeout")

    monkeypatch.setattr(background_runner, "_run_hermes_background_task", fail_hermes)

    await background_runner._run_task(100, "find flights", "user-y", None)

    error_calls = [c for c in db.executions if "error" in str(c)]
    assert error_calls, f"Expected status=error in DB calls, got: {db.executions}"


async def _noop_async(*_a, **_kw):
    pass


# ── queue saturation must be reported honestly ─────────────────────────────

@pytest.mark.asyncio
async def test_queue_saturation_is_not_reported_as_a_child_timeout(monkeypatch):
    """"Never started" and "ran too long" must not read the same in the log.

    run_to_completion raises TimeoutExpired for BOTH giving up on the queue and
    the child overrunning. Reporting the child's 900s budget when we actually
    abandoned the queue after `queue_wait_s` sends whoever reads this hunting a
    slow hermes that never ran.
    """
    import background_runner as br

    queue_wait = float(os.environ.get("HERMES_BACKGROUND_QUEUE_WAIT_S", "600"))

    async def _queue_gave_up(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["queue_timeout"])

    monkeypatch.setattr(br, "run_to_completion", _queue_gave_up)
    with pytest.raises(TimeoutError) as ei:
        await br._run_hermes_background_task("t", user_id="jason", task_id=1)
    assert "never started" in str(ei.value)
    assert f"{queue_wait:.0f}s" in str(ei.value)


@pytest.mark.asyncio
async def test_child_overrun_still_reports_the_child_budget(monkeypatch):
    """The other branch must keep reporting the runtime budget."""
    import background_runner as br

    async def _child_overran(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(br, "run_to_completion", _child_overran)
    with pytest.raises(TimeoutError) as ei:
        await br._run_hermes_background_task("t", user_id="jason", task_id=1)
    assert "never started" not in str(ei.value)
    assert "timed out after" in str(ei.value)


@pytest.mark.asyncio
async def test_background_lane_waits_rather_than_failing_fast(monkeypatch):
    """Background work has no latency budget — it must not take the 30s default.

    The pre-helper create_subprocess_exec path simply started; adopting the
    helper's interactive default would abort background tasks under contention.
    """
    import async_subprocess
    import background_runner as br

    seen = {}

    async def _capture(cmd, **kw):
        seen.update(kw)

        class _P:
            returncode = 0
            stdout = b"ok"
            stderr = b""
        return _P()

    monkeypatch.setattr(br, "run_to_completion", _capture)
    await br._run_hermes_background_task("t", user_id="jason", task_id=1)
    assert seen["queue_timeout"] > async_subprocess._QUEUE_WAIT_S
