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


# ---- plain-English completion summaries ----

class _Proc:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _merged(pr="https://github.com/o/r/pull/1"):
    from omnigent_issue_executor import OmnigentResult
    return OmnigentResult(True, "done", "merged", pr_url=pr, merged=True, merge_sha="sha")


def test_first_paragraph_skips_headings_and_comments():
    body = "<!-- template -->\n\n# Title\n\nGuards the null case so it no longer crashes.\n\ntrailing"
    assert r._first_paragraph(body) == "Guards the null case so it no longer crashes."
    assert r._first_paragraph("") == "" and r._first_paragraph(None) == ""


def test_first_paragraph_caps_length():
    assert len(r._first_paragraph("x " * 500, cap=100)) <= 100


def test_build_completion_summary_none_when_not_merged():
    from omnigent_issue_executor import OmnigentResult
    res = OmnigentResult(True, "review", "open", pr_url="https://github.com/o/r/pull/1", merged=False)
    assert r.build_completion_summary(res) == (None, None)


def test_build_completion_summary_reads_pr_title_and_body(monkeypatch):
    payload = '{"title": "fix(x): guard null", "body": "# H\\n\\nGuards the null case so it no longer crashes."}'
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: _Proc(0, payload))
    title, detail = r.build_completion_summary(_merged())
    assert title == "fix(x): guard null"
    assert detail == "Guards the null case so it no longer crashes."


def test_build_completion_summary_none_on_gh_nonzero(monkeypatch):
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: _Proc(1, ""))
    assert r.build_completion_summary(_merged()) == (None, None)


def test_build_completion_summary_swallows_subprocess_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("gh missing")
    monkeypatch.setattr(r.subprocess, "run", boom)
    assert r.build_completion_summary(_merged()) == (None, None)


def _capture_report(monkeypatch):
    captured = {}
    class FakeConn:
        def transaction(self):
            class T:
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            return T()
        async def execute(self, sql, *a):
            pass
    async def fake_log(conn, identity, issue_id, action, reason, extra=None):
        captured["extra"] = extra or {}
    monkeypatch.setattr(r, "_log_issue_activity", fake_log)
    return FakeConn(), captured


def test_report_result_records_summary_when_present(monkeypatch):
    conn, cap = _capture_report(monkeypatch)
    asyncio.run(r.report_result(
        conn, {"workspace_id": "w", "agent_id": "a"}, _Rec(id="i1", number=5), _merged(),
        summary="fix(x): guard null", summary_detail="Guards the null case."))
    assert cap["extra"]["summary"] == "fix(x): guard null"
    assert cap["extra"]["summary_detail"] == "Guards the null case."


def test_report_result_omits_summary_when_absent(monkeypatch):
    from omnigent_issue_executor import OmnigentResult
    conn, cap = _capture_report(monkeypatch)
    res = OmnigentResult(False, "review", "not ready", pr_url="u")
    asyncio.run(r.report_result(conn, {"workspace_id": "w", "agent_id": "a"}, _Rec(id="i1", number=5), res))
    assert "summary" not in cap["extra"] and "summary_detail" not in cap["extra"]


def test_ensure_postgres_url_strips_surrounding_quotes(monkeypatch, tmp_path):
    # _ensure_postgres_url writes os.environ["POSTGRES_URL"] directly, which
    # monkeypatch cannot undo. Point the module at a throwaway env dict (sans the
    # two DSN keys) so the synthetic value can't leak into later tests.
    env = {k: v for k, v in r.os.environ.items() if k not in ("POSTGRES_URL", "MULTICA_DATABASE_URL")}
    envf = tmp_path / ".env"
    envf.write_text('FOO=bar\nPOSTGRES_URL="postgresql://u:p@h:5432/zoe"\n')
    env["ZOE_ENV_FILE"] = str(envf)  # the module reads env via r.os.environ, so set it here
    monkeypatch.setattr(r.os, "environ", env)
    r._ensure_postgres_url()
    assert env["POSTGRES_URL"] == "postgresql://u:p@h:5432/zoe"


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
    async def fake_report(conn, identity, iss, result, **kw):
        reported["status"] = "done" if result.merged else ("in_review" if result.ok else "blocked")
        reported["stage"] = result.stage
    monkeypatch.setattr(r, "report_result", fake_report)

    import asyncio
    out = asyncio.run(r.run_one())
    # the issue must end blocked, never stranded in_progress
    assert reported["status"] == "blocked" and reported["stage"] == "error"
    assert out["status"] == "blocked"
