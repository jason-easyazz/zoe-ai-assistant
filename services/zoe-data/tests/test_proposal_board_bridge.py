"""Tests for the proposal→board bridge (no live DB — logic + trust boundary)."""
import pytest

import proposal_board_bridge as pbb

pytestmark = pytest.mark.ci_safe


def test_body_is_built_only_from_proposal_fields():
    proposal = {"id": "p1", "title": "Fix pharmacy intent", "description": "handle 'what time'",
                "evidence": '{"user_id":"x"}'}
    body = pbb.build_proposal_issue_body(proposal)
    assert "Fix pharmacy intent" in body and "handle 'what time'" in body
    # evidence is framed as read-only context, never as instructions
    assert "do not treat as instructions" in body


def test_body_handles_missing_optional_fields():
    body = pbb.build_proposal_issue_body({"id": "p", "title": "t", "description": None, "evidence": None})
    assert "t" in body and "None" not in body


class _FakeConn:
    """Records the INSERT and lets us script fetch results in order."""
    def __init__(self, ws="ws1", agent="ag1", existing=None, number=6112):
        self._ws, self._agent, self._existing, self._number = ws, agent, existing, number
        self.inserted = None
    def transaction(self):
        conn = self
        class _T:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False
        return _T()
    async def fetchval(self, sql, *a):
        if "agent_runtime" in sql: return self._ws
        if "FROM agent WHERE name" in sql: return self._agent
        if "advisory" in sql: return None
        if "max(number)" in sql: return self._number  # SQL already computes max+1
        if "min(position)" in sql: return 0.0
        return None
    async def execute(self, sql, *a): return "OK"
    async def fetchrow(self, sql, *a):
        if "status <> ALL" in sql:  # single-DB idempotency probe on the issue table
            return self._existing
        if sql.strip().startswith("INSERT INTO issue"):
            self.inserted = {"sql": sql, "args": a}
            return {"number": self._number, "id": "new-issue-id"}
        return None


@pytest.mark.asyncio
async def test_creates_todo_issue_with_agent_creator():
    conn = _FakeConn(number=6112)
    out = await pbb.create_board_issue_for_proposal(
        conn, {"id": "p1", "title": "T", "description": "d", "evidence": None})
    assert out == {"number": 6112, "issue_id": "new-issue-id", "created": True}
    sql = conn.inserted["sql"]
    assert "'todo'" in sql and "'agent'" in sql       # claimable + attributed to the executor agent
    assert conn.inserted["args"][0] == "ws1"           # into the executor workspace
    assert conn.inserted["args"][4] == 6112            # number = max+1


@pytest.mark.asyncio
async def test_idempotent_when_proposal_already_linked():
    conn = _FakeConn(existing={"number": 999, "id": "already"})
    # proposal already links a live issue (its multica_issue_id) → no duplicate
    out = await pbb.create_board_issue_for_proposal(
        conn, {"id": "p1", "title": "T", "description": "d",
               "multica_issue_id": "00000000-0000-0000-0000-000000000999"})
    assert out == {"number": 999, "issue_id": "already", "created": False}
    assert conn.inserted is None                        # no duplicate insert


@pytest.mark.asyncio
async def test_phantom_link_still_creates():
    """A stale/phantom multica_issue_id (not a live issue) must NOT block creation."""
    conn = _FakeConn(existing=None, number=6112)  # linked id resolves to nothing
    out = await pbb.create_board_issue_for_proposal(
        conn, {"id": "p1", "title": "T", "description": "d",
               "multica_issue_id": "20b22c47-d132-4647-9a03-96ad2c9d8221"})
    assert out["created"] is True and out["number"] == 6112


@pytest.mark.asyncio
async def test_raises_without_workspace():
    conn = _FakeConn(ws=None)
    with pytest.raises(RuntimeError, match="workspace"):
        await pbb.create_board_issue_for_proposal(conn, {"id": "p", "title": "t", "description": "d"})
