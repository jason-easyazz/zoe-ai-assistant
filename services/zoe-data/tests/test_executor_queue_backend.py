"""Tests for the Phase-2 executor queue backend (no live DB — fake connection).

Covers the two load-bearing contracts of
docs/architecture/multica-executor-migration.md:
  1. the row/detail shapes the (untouched) kanban_adapter reads, and
  2. a reason on EVERY transition, written in the status change's transaction.
"""
import json

import pytest

import executors.executor_queue_backend as eb
import executors.kanban_adapter as ka

pytestmark = pytest.mark.ci_safe


class FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeConn:
    """Minimal asyncpg-shaped double recording every statement it is given."""

    def __init__(self, *, fetchval_results=None, fetch_results=None, fetchrow_result=None,
                 execute_result="UPDATE 1"):
        self.statements: list[tuple[str, tuple]] = []
        self._fetchval = list(fetchval_results or [])
        self._fetch = list(fetch_results or [])
        self._fetchrow = fetchrow_result
        self._execute_result = execute_result

    def transaction(self):
        return FakeTx()

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return self._execute_result

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args))
        return self._fetchval.pop(0) if self._fetchval else None

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        return self._fetch.pop(0) if self._fetch else []

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        return self._fetchrow

    def logged_actions(self) -> list[str]:
        out = []
        for sql, args in self.statements:
            if "INSERT INTO activity_log" in sql:
                out.append(args[2])
        return out

    def logged_reasons(self) -> list[str]:
        out = []
        for sql, args in self.statements:
            if "INSERT INTO activity_log" in sql:
                out.append(json.loads(args[3])["reason"])
        return out


IDENTITY = {
    "workspace_id": "00000000-0000-4000-8000-000000000001",
    "runtime_id": "00000000-0000-4000-8000-000000000002",
    "agent_id": "00000000-0000-4000-8000-000000000003",
}


# --- the seam itself -------------------------------------------------------

def test_default_backend_is_hermes_so_shipping_changes_nothing(monkeypatch):
    monkeypatch.delenv("ZOE_KANBAN_BACKEND", raising=False)
    assert ka._kanban_backend() == "hermes"


def test_backend_flag_is_read_per_call_so_revert_needs_no_code_change(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_BACKEND", "executor")
    assert ka._kanban_backend() == "executor"
    monkeypatch.setenv("ZOE_KANBAN_BACKEND", "hermes")
    assert ka._kanban_backend() == "hermes"


@pytest.mark.asyncio
async def test_run_routes_to_executor_backend_when_flagged(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_BACKEND", "executor")
    seen = {}

    async def fake_run(args, *, expect_json=False):
        seen["args"] = args
        seen["expect_json"] = expect_json
        return [{"id": "t1"}]

    monkeypatch.setattr(eb, "run_kanban_command", fake_run)

    def _boom(*a, **k):  # the CLI must NOT be shelled under the executor backend
        raise AssertionError("hermes CLI was invoked while the executor backend was selected")

    monkeypatch.setattr(ka, "_spawn_cli", _boom)
    out = await ka.KanbanAdapter()._run(["list", "--json"], expect_json=True)
    assert out == [{"id": "t1"}]
    assert seen["args"] == ["list", "--json"] and seen["expect_json"] is True


@pytest.mark.asyncio
async def test_backend_errors_surface_as_KanbanCLIError(monkeypatch):
    """Existing recovery paths key off KanbanCLIError — the backend must not
    leak a different exception type through the seam."""
    monkeypatch.setenv("ZOE_KANBAN_BACKEND", "executor")

    async def boom(args, *, expect_json=False):
        raise eb.ExecutorBackendError("queue unreachable")

    monkeypatch.setattr(eb, "run_kanban_command", boom)
    with pytest.raises(ka.KanbanCLIError) as err:
        await ka.KanbanAdapter()._run(["list", "--json"], expect_json=True)
    assert "queue unreachable" in str(err.value)


# --- shape contract --------------------------------------------------------

def test_row_shape_matches_every_field_the_adapter_reads():
    row = {
        "id": "abc",
        "status": "failed",
        "failure_reason": "worker died: exit 1",
        "result": json.dumps({"summary": "did the thing"}),
        "context": json.dumps({
            "title": "implement ZOE-1",
            "body": "zoe-ref: multica:ZOE-1:implement",
            "idempotency_key": "multica:ZOE-1:implement",
            "workspace_path": "/wt/x",
        }),
        "work_dir": "/wt/x",
    }
    mapped = eb._row_to_hermes(row)
    for field in ("id", "title", "body", "status", "block_reason", "result", "workspace_path"):
        assert field in mapped, f"adapter reads {field} from list rows"
    # failed -> blocked so the adapter's ACTIVE/recovery paths still apply,
    # and the reason is finally durable (Hermes logged 0/128).
    assert mapped["status"] == "blocked"
    assert mapped["block_reason"] == "worker died: exit 1"
    # _row_ref_key prefers idempotency_key over parsing the body marker.
    assert mapped["idempotency_key"] == "multica:ZOE-1:implement"
    assert ka._row_ref_key(mapped) == "multica:ZOE-1:implement"


def test_completed_row_has_no_block_reason():
    row = {
        "id": "abc", "status": "completed", "failure_reason": None,
        "result": json.dumps({"summary": "ok"}), "context": json.dumps({}), "work_dir": "",
    }
    mapped = eb._row_to_hermes(row)
    assert mapped["status"] == "done" and mapped["block_reason"] is None


def test_status_vocabulary_covers_every_multica_state():
    # The CHECK constraint on agent_task_queue allows exactly these.
    assert set(eb._STATUS_TO_HERMES) == {
        "queued", "dispatched", "running", "completed", "failed", "cancelled"
    }


# --- the reason non-negotiable --------------------------------------------

@pytest.mark.asyncio
async def test_activity_log_refuses_an_empty_reason():
    conn = FakeConn()
    with pytest.raises(eb.ExecutorBackendError) as err:
        await eb._log_activity(conn, IDENTITY, "t1", "task_blocked", "   ")
    assert "without a reason" in str(err.value)
    assert conn.logged_actions() == []


@pytest.mark.asyncio
async def test_block_records_the_reason_with_the_status_change():
    conn = FakeConn(execute_result="UPDATE 1")
    await eb._cmd_block(conn, IDENTITY, ["task-1", "BLOCKER=verify failed on tests"])
    assert conn.logged_actions() == ["task_blocked"]
    assert conn.logged_reasons() == ["BLOCKER=verify failed on tests"]
    sqls = " ".join(s for s, _ in conn.statements)
    assert "UPDATE agent_task_queue" in sqls and "INSERT INTO activity_log" in sqls


@pytest.mark.asyncio
async def test_block_does_not_overwrite_an_already_completed_task():
    conn = FakeConn(execute_result="UPDATE 0")
    await eb._cmd_block(conn, IDENTITY, ["task-1", "late blocker"])
    assert conn.logged_actions() == [], "a terminal row must not be re-blocked"


@pytest.mark.asyncio
async def test_complete_and_archive_both_record_reasons():
    conn = FakeConn()
    await eb._cmd_complete(
        conn, IDENTITY,
        ["--result", "PR_URL=https://x/pull/1", "--summary", "shipped",
         "--metadata", json.dumps({"pr_url": "https://x/pull/1"}), "task-2"],
    )
    assert conn.logged_actions() == ["task_completed"]
    assert conn.logged_reasons() == ["shipped"]

    conn2 = FakeConn()
    await eb._cmd_archive(conn2, IDENTITY, ["task-3"])
    assert conn2.logged_actions() == ["task_archived"]
    assert conn2.logged_reasons()[0].strip() != ""


# --- create / idempotency --------------------------------------------------

@pytest.mark.asyncio
async def test_create_dedupes_on_idempotency_key_without_inserting():
    conn = FakeConn(fetchval_results=["existing-id"])
    out = await eb._cmd_create(
        conn, IDENTITY,
        ["implement ZOE-9", "--assignee", "zoe-coder", "--workspace", "worktree",
         "--idempotency-key", "multica:ZOE-9:implement", "--body", "b", "--json"],
    )
    assert out == {"id": "existing-id", "deduplicated": True}
    assert all("INSERT INTO agent_task_queue" not in s for s, _ in conn.statements)


@pytest.mark.asyncio
async def test_create_takes_an_advisory_lock_before_the_dedupe_check():
    """Without the lock two concurrent creates both miss and both insert."""
    conn = FakeConn(fetchval_results=[None, "new-id"])
    await eb._cmd_create(
        conn, IDENTITY,
        ["implement ZOE-9", "--idempotency-key", "multica:ZOE-9:implement", "--json"],
    )
    sqls = [s for s, _ in conn.statements]
    lock_idx = next(i for i, s in enumerate(sqls) if "pg_advisory_xact_lock" in s)
    check_idx = next(i for i, s in enumerate(sqls) if "context->>'idempotency_key'" in s)
    assert lock_idx < check_idx


@pytest.mark.asyncio
async def test_create_routes_implement_to_the_heavy_lane_and_logs_it():
    conn = FakeConn(fetchval_results=[None, "new-id"])
    out = await eb._cmd_create(
        conn, IDENTITY,
        ["implement ZOE-9", "--idempotency-key", "multica:ZOE-9:implement", "--json"],
    )
    assert out["deduplicated"] is False and out["id"] == "new-id"
    insert = next(a for s, a in conn.statements if "INSERT INTO agent_task_queue" in s)
    context = json.loads(insert[3])
    assert context["lane"] == "heavy" and context["phase"] == "implement"
    assert conn.logged_actions() == ["task_created"]


@pytest.mark.asyncio
async def test_create_routes_non_implement_phases_to_the_light_lane():
    conn = FakeConn(fetchval_results=[None, "new-id"])
    await eb._cmd_create(
        conn, IDENTITY, ["review ZOE-9", "--idempotency-key", "multica:ZOE-9:review", "--json"],
    )
    context = json.loads(next(a for s, a in conn.statements if "INSERT INTO agent_task_queue" in s)[3])
    assert context["lane"] == "light" and context["phase"] == "review"


@pytest.mark.asyncio
async def test_create_without_an_idempotency_key_is_refused():
    conn = FakeConn()
    with pytest.raises(eb.ExecutorBackendError):
        await eb._cmd_create(conn, IDENTITY, ["some title", "--json"])


# --- argv parsing ----------------------------------------------------------

def test_parse_flags_collects_repeated_skills_and_positionals():
    flags, positional = eb._parse_flags(
        ["title here", "--skill", "a", "--skill", "b", "--json", "--parent", "p1"],
        eb._CREATE_VALUE_FLAGS,
    )
    assert positional == ["title here"]
    assert flags["skill"] == ["a", "b"]
    assert flags["parent"] == ["p1"]
    assert "json" in flags


def test_multica_dsn_swaps_only_the_database_name(monkeypatch):
    monkeypatch.delenv("MULTICA_DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/zoe")
    assert eb.multica_dsn() == "postgresql://u:p@localhost:5432/multica"


def test_multica_dsn_prefers_an_explicit_override(monkeypatch):
    monkeypatch.setenv("MULTICA_DATABASE_URL", "postgresql://u:p@h:5432/other")
    assert eb.multica_dsn() == "postgresql://u:p@h:5432/other"


@pytest.mark.asyncio
async def test_unsupported_verb_is_refused_not_silently_ignored(monkeypatch):
    async def fake_pool():
        class P:
            def acquire(self):
                class C:
                    async def __aenter__(self_inner):
                        return FakeConn()

                    async def __aexit__(self_inner, *a):
                        return False

                return C()

        return P()

    monkeypatch.setattr(eb, "get_pool", fake_pool)
    monkeypatch.setattr(eb, "ensure_executor_identity", lambda conn: _ident())
    with pytest.raises(eb.ExecutorBackendError):
        await eb.run_kanban_command(["frobnicate", "x"])


async def _ident():
    return IDENTITY
