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
