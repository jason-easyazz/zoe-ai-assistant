import sys
import types

import pytest

import engineering_workflow


def test_build_hermes_prompt_requires_pr_contract():
    prompt = engineering_workflow.build_hermes_prompt(
        "Fix the thing",
        workflow_id="wf-1",
        max_rounds=3,
        target_confidence=5,
    )

    assert "zoe-engineering" in prompt
    assert "github-greptile-loop" in prompt
    assert "PR_URL=" in prompt
    assert "BLOCKER=" in prompt
    assert "do not push to main" in prompt


def test_parse_pr_url_extracts_github_pull_request():
    pr_url, pr_number = engineering_workflow._parse_pr_url(
        "Done\nPR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/123\n"
    )

    assert pr_url == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/123"
    assert pr_number == 123


@pytest.mark.asyncio
async def test_reconcile_background_task_records_pr(monkeypatch):
    workflow = {
        "id": "wf-1",
        "user_id": "family-admin",
        "title": "Test workflow",
        "task": "Do work",
        "phase": "hermes_running",
        "status": "active",
        "background_task_id": 42,
        "multica_issue_id": None,
    }
    background = {
        "id": 42,
        "status": "done",
        "result": "SUMMARY=done\nPR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/77\n",
    }

    class FakeDB:
        async def fetchrow(self, sql, *args):
            if "WHERE background_task_id=$1" in sql:
                return workflow
            if "FROM background_tasks" in sql:
                return background
            if "SET phase='pr_open'" in sql:
                workflow.update(
                    {
                        "phase": "pr_open",
                        "pr_url": args[0],
                        "pr_number": args[1],
                        "updated_at": args[2],
                    }
                )
                return workflow
            raise AssertionError(f"unexpected query: {sql}")

    class FakeCtx:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, *_):
            return None

    fake_db_pool = types.SimpleNamespace(get_db_ctx=lambda: FakeCtx())
    monkeypatch.setitem(sys.modules, "db_pool", fake_db_pool)

    async def fake_sync(*_, **__):
        return None

    monkeypatch.setattr(engineering_workflow, "sync_multica_issue", fake_sync)

    updated = await engineering_workflow.reconcile_background_task(42)

    assert updated["phase"] == "pr_open"
    assert updated["pr_number"] == 77
    assert updated["pr_url"].endswith("/pull/77")


@pytest.mark.asyncio
async def test_start_engineering_task_returns_existing_when_already_claimed(monkeypatch):
    existing = {
        "id": "wf-claimed",
        "phase": "hermes_running",
        "background_task_id": 99,
    }

    class FakeDB:
        async def fetchrow(self, sql, *args):
            if "UPDATE engineering_tasks" in sql:
                return None
            if "SELECT * FROM engineering_tasks WHERE id=$1" in sql:
                return existing
            raise AssertionError(f"unexpected query: {sql}")

    class FakeCtx:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, *_):
            return None

    async def fail_enqueue(*_, **__):
        raise AssertionError("enqueue should not be called for an already claimed workflow")

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: FakeCtx()))
    monkeypatch.setitem(
        sys.modules,
        "background_runner",
        types.SimpleNamespace(enqueue_background_task=fail_enqueue),
    )

    task = await engineering_workflow.start_engineering_task("wf-claimed")

    assert task == existing


@pytest.mark.asyncio
async def test_reconcile_does_not_overwrite_cancelled_workflow(monkeypatch):
    workflow = {
        "id": "wf-cancelled",
        "phase": "cancelled",
        "status": "cancelled",
        "background_task_id": 42,
    }

    class FakeDB:
        async def fetchrow(self, sql, *args):
            if "WHERE background_task_id=$1" in sql:
                return workflow
            raise AssertionError(f"unexpected query after terminal workflow: {sql}")

    class FakeCtx:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, *_):
            return None

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: FakeCtx()))

    async def fake_sync(*_, **__):
        raise AssertionError("terminal workflow should not sync new state")

    monkeypatch.setattr(engineering_workflow, "sync_multica_issue", fake_sync)

    updated = await engineering_workflow.reconcile_background_task(42)

    assert updated == workflow


@pytest.mark.asyncio
async def test_update_greptile_state_does_not_overwrite_terminal_workflow(monkeypatch):
    workflow = {
        "id": "wf-done",
        "phase": "done",
        "status": "done",
        "target_confidence": 5,
    }

    class FakeDB:
        async def fetchrow(self, sql, *args):
            if "UPDATE engineering_tasks" in sql:
                assert "phase NOT IN ('cancelled', 'done')" in sql
                return None
            if "SELECT * FROM engineering_tasks WHERE id=$1" in sql:
                return workflow
            raise AssertionError(f"unexpected query: {sql}")

    class FakeCtx:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, *_):
            return None

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: FakeCtx()))

    async def fake_sync(*_, **__):
        raise AssertionError("terminal workflow should not sync Greptile state")

    monkeypatch.setattr(engineering_workflow, "sync_multica_issue", fake_sync)

    updated = await engineering_workflow.update_greptile_state(
        "wf-done",
        greptile_status="clean",
        confidence=5,
        unaddressed_count=0,
    )

    assert updated == workflow
