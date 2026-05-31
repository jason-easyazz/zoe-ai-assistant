"""Tests for the Hermes Kanban executor adapter (CLI mocked)."""
import json

import pytest

import executors.kanban_adapter as ka


class _FakeAdapter(ka.KanbanAdapter):
    """KanbanAdapter with the CLI replaced by a scripted recorder."""

    def __init__(self, list_rows=None, show_map=None):
        self.calls = []
        self._list_rows = list_rows or []
        self._show_map = show_map or {}
        self._create_seq = 0

    async def _run(self, args, *, expect_json=False):
        self.calls.append(args)
        verb = args[0]
        if verb == "create":
            self._create_seq += 1
            # idempotency-key arg lets us derive a stable id per phase
            key = args[args.index("--idempotency-key") + 1]
            return {"id": f"t_{key.split(':')[-1]}", "deduplicated": False}
        if verb == "list":
            return self._list_rows
        if verb == "show":
            return self._show_map.get(args[1], {"comments": [], "latest_summary": ""})
        return ""


@pytest.mark.asyncio
async def test_dispatch_creates_three_linked_phases():
    a = _FakeAdapter()
    issue = {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": "do it"}
    result = await a.dispatch(issue)
    assert result["ok"] is True
    assert result["external_ref"] == "multica:uuid-1"
    assert set(result["chain"]) == {"implement", "review", "closeout"}
    creates = [c for c in a.calls if c[0] == "create"]
    assert len(creates) == 3
    # review + closeout must be linked to a parent
    assert "--parent" in creates[1]
    assert "--parent" in creates[2]
    # idempotency keys are phase-scoped
    keys = [c[c.index("--idempotency-key") + 1] for c in creates]
    assert keys == ["multica:uuid-1:implement", "multica:uuid-1:review", "multica:uuid-1:closeout"]


@pytest.mark.asyncio
async def test_dispatch_pins_expected_skills():
    a = _FakeAdapter()
    await a.dispatch({"id": "u", "identifier": "ZOE-1", "title": "t"})
    creates = [c for c in a.calls if c[0] == "create"]
    impl_skills = [creates[0][i + 1] for i, v in enumerate(creates[0]) if v == "--skill"]
    closeout_skills = [creates[2][i + 1] for i, v in enumerate(creates[2]) if v == "--skill"]
    assert "zoe-graphify" in impl_skills and "code-structure-cleanup" in impl_skills
    assert "github-greptile-loop" in closeout_skills


@pytest.mark.asyncio
async def test_dispatch_requires_issue_id():
    a = _FakeAdapter()
    result = await a.dispatch({"identifier": "ZOE-2"})
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_poll_not_found():
    a = _FakeAdapter(list_rows=[{"id": "t_x", "idempotency_key": "other:1:implement"}])
    out = await a.poll("multica:uuid-1")
    assert out["found"] is False
    assert out["status"] == "not_found"


@pytest.mark.asyncio
async def test_poll_blocked_detected():
    rows = [
        {"id": "t_i", "idempotency_key": "multica:u:implement", "status": "blocked", "block_reason": "dirty tree"},
        {"id": "t_r", "idempotency_key": "multica:u:review", "status": "todo"},
        {"id": "t_c", "idempotency_key": "multica:u:closeout", "status": "todo"},
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:u")
    assert out["found"] is True
    assert out["status"] == "blocked"
    assert "dirty tree" in out["blocker"]


@pytest.mark.asyncio
async def test_poll_partial_chain_is_redispatchable():
    # closeout phase never got created (e.g. CLI error mid-chain): must report
    # "partial" (not "running") so the sync path re-dispatches instead of
    # skipping the wedged chain forever.
    rows = [
        {"id": "t_i", "idempotency_key": "multica:u:implement", "status": "done"},
        {"id": "t_r", "idempotency_key": "multica:u:review", "status": "running"},
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:u")
    assert out["found"] is True
    assert out["status"] == "partial"


@pytest.mark.asyncio
async def test_poll_done_and_pr_extracted():
    rows = [
        {"id": "t_i", "idempotency_key": "multica:u:implement", "status": "done"},
        {"id": "t_r", "idempotency_key": "multica:u:review", "status": "done"},
        {"id": "t_c", "idempotency_key": "multica:u:closeout", "status": "done"},
    ]
    show = {
        "t_c": {
            "latest_summary": "",
            "comments": [{"body": "Merged via https://github.com/o/r/pull/42 — done"}],
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:u")
    assert out["status"] == "done"
    assert out["pr_url"] == "https://github.com/o/r/pull/42"
