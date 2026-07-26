import types

import pytest

from routers import system

pytestmark = pytest.mark.ci_safe


@pytest.mark.asyncio
async def test_agent_board_keeps_active_and_chain_enrichment_bounded(monkeypatch):
    issues_by_status = {
        "backlog": [{"id": "backlog-1", "assignee_id": "hermes"}],
        "todo": [{"id": "todo-1", "assignee_id": "hermes"}],
        "in_progress": [{"id": "progress-1", "assignee_id": "hermes"}],
        "blocked": [{"id": "blocked-1", "assignee_id": "hermes"}],
        "in_review": [{"id": "review-1", "assignee_id": "hermes"}],
    }
    polled_refs: list[str] = []

    class FakeMULClient:
        def is_configured(self):
            return True

        async def list_issues(self, *, status):
            return issues_by_status[status]

    async def fake_poll_ref(ref, **_kwargs):
        polled_refs.append(ref)
        return {
            "status": "running",
            "pipeline": {"phase": "implement", "status": "running"},
            "blocker": None,
            "pr_url": "https://github.example/pr/1",
        }

    monkeypatch.setitem(
        __import__("sys").modules,
        "multica_client",
        types.SimpleNamespace(MULClient=FakeMULClient, get_engineering_multica_agent_id=lambda: "hermes"),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "executor_registry",
        types.SimpleNamespace(poll_ref=fake_poll_ref),
    )

    board = await system.get_agent_board(user={"sub": "test"})

    assert [issue["id"] for issue in board["active"]] == ["progress-1", "review-1"]
    assert sorted(board["groups"]) == ["backlog", "blocked", "in_progress", "in_review", "todo"]
    assert polled_refs == ["multica:progress-1", "multica:blocked-1", "multica:review-1"]
    assert board["groups"]["backlog"][0].get("chain") is None
    assert board["groups"]["in_progress"][0]["phase"] == "implement"
    assert board["groups"]["blocked"][0]["phase"] == "implement"
