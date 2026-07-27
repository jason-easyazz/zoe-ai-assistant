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
            self.probe_sql = sql
            # Honour the probe's own status filter, so tests exercise the REAL
            # SQL semantics instead of a stub that ignores allow_retry.
            if (self._existing and "<> ALL(ARRAY['blocked','cancelled'])" in sql
                    and self._existing.get("status") in ("blocked", "cancelled")):
                return None
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
    # (identified admin: the user_id fail-closed check has its own test)
    admin = {"role": "family-admin", "user_id": "jason"}
    prop = {"id": "p1", "title": "T"}  # no type / autonomy_class / approval_required
    allowed, reason = pbb.may_auto_execute(admin, prop)
    # denied at the earliest applicable check (now the type-policy cross-check;
    # specific paths each have their own test) — the property is: DENIED, with a reason
    assert allowed is False and reason

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
    prop = {"id": "p9", "type": "intent_pattern", "autonomy_class": "execute",
            "approval_required": ["user_or_admin_for_privileged_execution"]}
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is True, reason


def test_may_auto_execute_parses_json_string_approval_required():
    """approval_required arrives as a JSON-array TEXT column from the DB — the
    real end-to-end shape after the autonomy contract is persisted."""
    admin = {"role": "family-admin", "user_id": "jason"}
    prop = {"id": "p", "type": "intent_pattern", "autonomy_class": "execute",
            "approval_required": '["user_or_admin_for_privileged_execution"]'}  # JSON string
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is True, reason


def test_admin_approval_does_not_satisfy_other_approval_classes():
    """The admin ref satisfies only privileged-execution — a proposal that also
    requires e.g. security_review still blocks until THAT evidence exists."""
    admin = {"role": "family-admin", "user_id": "jason"}
    prop = {"id": "p9", "type": "user_frustration", "autonomy_class": "execute",
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


def test_admin_without_user_id_fails_closed():
    """An admin-shaped dict with no user_id must NOT mint approval:admin:unknown —
    an approval ref has to name an identifiable principal."""
    prop = {"id": "p9", "type": "intent_pattern", "autonomy_class": "execute",
            "approval_required": ["user_or_admin_for_privileged_execution"]}
    for user in ({"role": "family-admin"}, {"role": "admin", "user_id": ""},
                 {"role": "admin", "user_id": "   "}):
        allowed, reason = pbb.may_auto_execute(user, prop)
        assert allowed is False and "user_id" in reason, (user, reason)


def test_evolution_proposal_action_requires_admin():
    """The approve/reject/defer endpoint is security-critical (it sanctions
    self-modification and, for executable proposals, triggers autonomous
    implementation). It must carry require_admin — not get_current_user, which
    resolves unauthenticated callers to a guest identity instead of rejecting."""
    from routers import system as system_router
    from auth import require_admin

    route = next(
        r for r in system_router._agent_card_router.routes
        if getattr(r, "path", "").endswith("/evolution/proposals/{proposal_id}/action")
    )
    dep_calls = [d.call for d in route.dependant.dependencies]
    assert require_admin in dep_calls, (
        "evolution_proposal_action must Depends(require_admin); "
        f"got dependencies: {[getattr(c, '__name__', c) for c in dep_calls]}"
    )


def test_stored_execute_on_wrong_type_is_refused():
    """Defense in depth vs the UPDATE-bypass: a row whose stored autonomy_class
    says 'execute' while its TYPE's policy is review-only must be refused —
    only the type grants executability (mirrors the DB trigger, and covers
    stores where the trigger doesn't exist)."""
    admin = {"role": "family-admin", "user_id": "jason"}
    prop = {"id": "p9", "type": "code_improvement", "autonomy_class": "execute",
            "approval_required": ["user_or_admin_for_privileged_execution"]}
    allowed, reason = pbb.may_auto_execute(admin, prop)
    assert allowed is False and "does not match the policy" in reason


@pytest.mark.asyncio
async def test_reapproval_after_cancelled_does_not_reenqueue():
    """DEFAULT is fail-closed: a cancelled prior run still blocks a new enqueue.

    Re-approval alone must NEVER re-run autonomous implementation — the dead
    issue is returned with its status so the caller can surface it. Retrying is
    a separate, explicit admin decision (allow_retry)."""
    conn = _FakeConn(existing={"number": 42, "id": "dead", "status": "cancelled"}, number=7001)
    out = await pbb.create_board_issue_for_proposal(conn, {"id": "p1", "title": "T", "description": "d"})
    assert out["created"] is False and out["status"] == "cancelled"
    assert conn.inserted is None


@pytest.mark.asyncio
async def test_blocked_prior_issue_allows_an_explicit_retry():
    """With allow_retry=True (the admin explicitly asked), a prior FAILED run
    (blocked/cancelled) is ignored and a fresh issue is enqueued."""
    conn = _FakeConn(existing={"number": 42, "id": "dead", "status": "blocked"}, number=7001)
    out = await pbb.create_board_issue_for_proposal(
        conn, {"id": "p1", "title": "T", "description": "d"}, allow_retry=True)
    assert out["created"] is True and out["number"] == 7001


@pytest.mark.asyncio
async def test_explicit_retry_still_never_redoes_live_or_done_work():
    """allow_retry only forgives FAILURES: a done (or live) issue still blocks —
    shipped work is never redone, a running lane is never forked."""
    conn = _FakeConn(existing={"number": 42, "id": "shipped", "status": "done"}, number=7001)
    out = await pbb.create_board_issue_for_proposal(
        conn, {"id": "p1", "title": "T", "description": "d"}, allow_retry=True)
    assert out["created"] is False and out["status"] == "done"
    assert conn.inserted is None


@pytest.mark.asyncio
async def test_the_probe_sql_is_unfiltered_by_default_and_excludes_only_failures_on_retry():
    """Pin the invariant in the SQL itself: no status filter by default (ANY
    prior issue blocks); with allow_retry, ONLY blocked/cancelled are excluded."""
    conn = _FakeConn(number=1)
    await pbb.create_board_issue_for_proposal(conn, {"id": "p", "title": "t", "description": "d"})
    assert "<> ALL" not in conn.probe_sql, "default probe must match ANY prior issue"
    await pbb.create_board_issue_for_proposal(
        conn, {"id": "p", "title": "t", "description": "d"}, allow_retry=True)
    sql = conn.probe_sql
    assert "'blocked'" in sql and "'cancelled'" in sql and "<> ALL" in sql
    for live in ("todo", "in_progress", "in_review", "done"):
        assert f"'{live}'" not in sql, f"{live} must BLOCK a duplicate, not be excluded"
