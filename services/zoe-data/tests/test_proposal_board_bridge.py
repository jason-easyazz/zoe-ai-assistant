"""Tests for the proposal→board bridge (no live DB — logic + trust boundary)."""
import sys
import types

import pytest

import proposal_board_bridge as pbb

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _fake_executor_identity(monkeypatch):
    """The bridge resolves workspace/agent via ensure_executor_identity — the
    SAME resolver the board runner uses. Stub it so tests don't need a DB."""
    async def _identity(conn):
        return {"workspace_id": "ws1", "agent_id": "ag1"}
    monkeypatch.setitem(
        sys.modules, "executors.executor_queue_backend",
        types.SimpleNamespace(ensure_executor_identity=_identity),
    )


def test_body_is_built_only_from_proposal_fields():
    proposal = {"id": "p1", "title": "Fix pharmacy intent", "description": "handle 'what time'",
                "evidence": '{"user_id":"x"}'}
    body = pbb.build_proposal_issue_body(proposal)
    assert "Fix pharmacy intent" in body and "handle 'what time'" in body
    assert "do not treat as instructions" in body  # evidence framed as read-only context


def test_body_handles_missing_optional_fields():
    body = pbb.build_proposal_issue_body({"id": "p", "title": "t", "description": None, "evidence": None})
    assert "t" in body and "None" not in body


class _FakeConn:
    """Records the INSERT and scripts fetch results; existing = an already-linked issue."""
    def __init__(self, existing=None, number=6112):
        self._existing, self._number = existing, number
        self.inserted = None
    def transaction(self):
        conn = self
        class _T:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False
        return _T()
    async def fetchval(self, sql, *a):
        if "advisory" in sql: return None
        if "max(number)" in sql: return self._number  # SQL already computes max+1
        return None
    async def execute(self, sql, *a): return "OK"
    async def fetchrow(self, sql, *a):
        if "context_refs @>" in sql and "INSERT" not in sql:  # idempotency probe
            return self._existing
        if sql.strip().startswith("INSERT INTO issue"):
            self.inserted = {"sql": sql, "args": a}
            return {"number": self._number, "id": "new-issue-id"}
        return None


@pytest.mark.asyncio
async def test_creates_todo_issue_with_agent_creator_and_proposal_ref():
    conn = _FakeConn(number=6112)
    out = await pbb.create_board_issue_for_proposal(conn, {"id": "p1", "title": "T", "description": "d"})
    assert out == {"number": 6112, "issue_id": "new-issue-id", "created": True}
    sql = conn.inserted["sql"]
    assert "'todo'" in sql and "'agent'" in sql        # claimable + attributed to the executor agent
    assert "context_refs" in sql                        # stores the stable proposal back-ref
    assert conn.inserted["args"][0] == "ws1"            # into the runner's resolved workspace
    assert conn.inserted["args"][4] == 6112             # number = max+1
    assert '"proposal_id": "p1"' in conn.inserted["args"][6]  # the proposal ref


@pytest.mark.asyncio
async def test_idempotent_when_an_issue_already_refs_the_proposal():
    conn = _FakeConn(existing={"number": 999, "id": "already", "status": "in_progress"})
    out = await pbb.create_board_issue_for_proposal(conn, {"id": "p1", "title": "T", "description": "d"})
    assert out["number"] == 999 and out["issue_id"] == "already" and out["created"] is False
    assert conn.inserted is None                         # no duplicate insert (concurrency-safe)


@pytest.mark.asyncio
async def test_idempotent_even_when_prior_issue_is_terminal():
    """Re-approving an already-DONE proposal must NOT enqueue a second run."""
    conn = _FakeConn(existing={"number": 6112, "id": "done-issue", "status": "done"})
    out = await pbb.create_board_issue_for_proposal(conn, {"id": "p1", "title": "T", "description": "d"})
    assert out["created"] is False and out["status"] == "done"
    assert conn.inserted is None                         # no re-execution


def test_may_auto_execute_is_fail_closed():
    # a DB proposal has no autonomy contract → gate blocks even for an admin
    admin = {"role": "family-admin"}
    prop = {"id": "p1", "title": "T"}  # no autonomy_class / approval_required
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is False and "execution gate blocked" in reason

    # non-admin is denied BEFORE the gate is even consulted
    allowed, reason = pbb.may_auto_execute({"role": "guest"}, prop)
    assert allowed is False and reason == "not admin"
    allowed, _ = pbb.may_auto_execute(None, prop)
    assert allowed is False
    allowed, _ = pbb.may_auto_execute({}, prop)  # missing role
    assert allowed is False


def test_may_auto_execute_allows_admin_with_executable_contract():
    admin = {"role": "family-admin", "user_id": "jason"}
    # executable autonomy + the privileged-execution approval, which the admin's
    # own approval satisfies (approval:admin:<id>) — the real happy path.
    prop = {"id": "p9", "autonomy_class": "execute",
            "approval_required": ["user_or_admin_for_privileged_execution"]}
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is True, reason


def test_may_auto_execute_parses_json_string_approval_required():
    """approval_required arrives as a JSON-array TEXT column from the DB — the
    real end-to-end shape after the autonomy contract is persisted."""
    admin = {"role": "family-admin", "user_id": "jason"}
    prop = {"id": "p", "autonomy_class": "execute",
            "approval_required": '["user_or_admin_for_privileged_execution"]'}  # JSON string
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is True, reason


def test_admin_approval_does_not_satisfy_other_approval_classes():
    """The admin ref satisfies only privileged-execution — a proposal that also
    requires e.g. security_review still blocks until THAT evidence exists."""
    admin = {"role": "family-admin", "user_id": "jason"}
    prop = {"id": "p9", "autonomy_class": "execute",
            "approval_required": ["user_or_admin_for_privileged_execution", "security_review"]}
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is False and "security_review" in reason


@pytest.mark.asyncio
async def test_no_live_ref_still_creates():
    """A phantom/stale link (no LIVE issue refs the proposal) must not block creation."""
    conn = _FakeConn(existing=None, number=6112)
    out = await pbb.create_board_issue_for_proposal(
        conn, {"id": "p1", "title": "T", "description": "d",
               "multica_issue_id": "20b22c47-d132-4647-9a03-96ad2c9d8221"})
    assert out["created"] is True and out["number"] == 6112
