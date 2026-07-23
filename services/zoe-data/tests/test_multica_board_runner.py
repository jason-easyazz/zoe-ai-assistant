"""Tests for the Multica board runner (no DB — logic only)."""
import asyncio
import pytest
import multica_board_runner as r

pytestmark = pytest.mark.ci_safe


def test_build_issue_body_includes_description_and_acceptance():
    row = {"description": "Do the thing.", "acceptance_criteria": ["a passes", "b green"]}
    body = r.build_issue_body(row)
    assert "Do the thing." in body
    assert "Acceptance criteria:" in body and "- a passes" in body and "- b green" in body


def test_build_issue_body_handles_empty_acceptance_and_json_string():
    assert r.build_issue_body({"description": "x", "acceptance_criteria": []}) == "x"
    assert "y" in r.build_issue_body({"description": "y", "acceptance_criteria": "[]"})


def test_paused_when_kill_switch_present(monkeypatch):
    monkeypatch.setattr(r, "kill_switch_present", lambda: True)
    out = asyncio.run(r.run_one())
    assert out["status"] == "paused"


def test_disabled_when_executor_flag_off(monkeypatch):
    monkeypatch.setattr(r, "kill_switch_present", lambda: False)
    monkeypatch.setattr(r, "omnigent_executor_enabled", lambda: False)
    out = asyncio.run(r.run_one())
    assert out["status"] == "disabled"


class _Rec(dict):
    pass


def test_report_result_maps_merged_to_done(monkeypatch):
    logged = {}
    calls = []
    class FakeConn:
        def transaction(self):
            class T:
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            return T()
        async def execute(self, sql, *a):
            calls.append((sql, a))
    from omnigent_issue_executor import OmnigentResult
    ident = {"workspace_id": "w", "agent_id": "a"}
    issue = _Rec(id="i1", number=5, title="t")
    res = OmnigentResult(True, "done", "merged", pr_url="u", merged=True, merge_sha="sha")
    async def fake_log(conn, identity, issue_id, action, reason, extra=None):
        logged["action"] = action; logged["status_ok"] = True
    monkeypatch.setattr(r, "_log_issue_activity", fake_log)
    asyncio.run(r.report_result(FakeConn(), ident, issue, res))
    assert logged["action"] == "issue_completed"
    assert any("status=$2" in s and a[1] == "done" for s, a in calls)


def test_report_result_maps_block_to_blocked(monkeypatch):
    calls = []
    class FakeConn:
        def transaction(self):
            class T:
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            return T()
        async def execute(self, sql, *a): calls.append((sql, a))
    from omnigent_issue_executor import OmnigentResult
    monkeypatch.setattr(r, "_log_issue_activity", lambda *a, **k: _noop())
    res = OmnigentResult(False, "review", "review not ready: CI not green", pr_url="u")
    asyncio.run(r.report_result(FakeConn(), {"workspace_id":"w","agent_id":"a"}, _Rec(id="i1", number=5), res))
    assert any("status=$2" in s and a[1] == "blocked" for s, a in calls)


async def _noop():
    return None


def test_ensure_postgres_url_strips_surrounding_quotes(monkeypatch, tmp_path):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("MULTICA_DATABASE_URL", raising=False)
    envf = tmp_path / ".env"
    envf.write_text('FOO=bar\nPOSTGRES_URL="postgresql://u:p@h:5432/zoe"\n')
    monkeypatch.setenv("ZOE_ENV_FILE", str(envf))
    r._ensure_postgres_url()
    assert r.os.environ["POSTGRES_URL"] == "postgresql://u:p@h:5432/zoe"


def test_run_one_turns_execute_exception_into_blocked(monkeypatch):
    monkeypatch.setattr(r, "kill_switch_present", lambda: False)
    monkeypatch.setattr(r, "omnigent_executor_enabled", lambda: True)

    issue = _Rec(id="i1", number=7, title="t", description="d", acceptance_criteria=[])

    class FakeConn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class FakePool:
        def acquire(self): return FakeConn()

    async def fake_pool(): return FakePool()
    monkeypatch.setattr(r, "get_pool", fake_pool)
    async def fake_ident(conn): return {"workspace_id": "w", "agent_id": "a"}
    monkeypatch.setattr(r, "ensure_executor_identity", fake_ident)
    async def fake_claim(conn, identity, *, issue_number=None): return issue
    monkeypatch.setattr(r, "claim_next_issue", fake_claim)

    def boom(_d): raise RuntimeError("gate blew up")
    monkeypatch.setattr(r, "execute_issue_dict", boom)

    reported = {}
    async def fake_report(conn, identity, iss, result):
        reported["status"] = "done" if result.merged else ("in_review" if result.ok else "blocked")
        reported["stage"] = result.stage
    monkeypatch.setattr(r, "report_result", fake_report)

    import asyncio
    out = asyncio.run(r.run_one())
    # the issue must end blocked, never stranded in_progress
    assert reported["status"] == "blocked" and reported["stage"] == "error"
    assert out["status"] == "blocked"
