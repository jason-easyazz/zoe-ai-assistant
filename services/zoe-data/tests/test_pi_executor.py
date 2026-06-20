"""Focused unit tests for the S1 Pi executor (no OpenRouter spend, no real git/gh).

Mocks the pi subprocess + the three gate functions so behaviour is pinned without
spawning pi or touching GitHub.
"""
import asyncio
import types

import pytest

import pi_executor as pe


# ── helpers ──────────────────────────────────────────────────────────────────


def _ft(ran=True, passed=True, summary="ok"):
    return types.SimpleNamespace(ran=ran, passed=passed, summary=summary,
                                 content_hash="h", test_paths=())


def _rr(ready=True, reason="green"):
    return types.SimpleNamespace(ready=ready, reason=reason)


def _co(merged=True, sha="abc123", reason="merged"):
    return types.SimpleNamespace(merged=merged, merge_sha=sha, reason=reason)


def _wire(monkeypatch, calls, *, pi_out="PR_URL=https://github.com/o/r/pull/9",
          ft=None, rr=None, co=None, has_commits=False):
    """Wire execute_issue's collaborators to in-memory stand-ins; record call order."""
    monkeypatch.setattr(pe, "_fetch_issue",
                        lambda n: {"number": n, "title": "T", "body": "B", "url": "u"})
    monkeypatch.setattr(pe, "worktree_branch", lambda tid: f"wt/{tid}")
    monkeypatch.setattr(pe, "ensure_worktree", lambda tid, **k: pe.Path("/tmp/wt-" + tid))

    async def fake_pi(prompt, wt):
        calls.append("pi")
        return 0, pi_out
    monkeypatch.setattr(pe, "run_pi_implement", fake_pi)
    monkeypatch.setattr(pe, "_worktree_has_commits", lambda b, w: has_commits)
    monkeypatch.setattr(pe, "_executor_open_pr",
                        lambda b, w: "https://github.com/o/r/pull/77")

    def f_tests(url, **k):
        calls.append("tests"); return ft if ft is not None else _ft()
    def f_review(url, **k):
        calls.append("review"); return rr if rr is not None else _rr()
    def f_merge(url, **k):
        calls.append("merge"); return co if co is not None else _co()
    monkeypatch.setattr(pe, "run_focused_pr_tests", f_tests)
    monkeypatch.setattr(pe, "assess_pr_review_ready", f_review)
    monkeypatch.setattr(pe, "run_closeout_merge", f_merge)
    monkeypatch.setattr(pe, "remove_task_worktree", lambda tid, **k: True)


def _on(monkeypatch):
    monkeypatch.setenv("ZOE_USE_PI_EXECUTOR", "true")


# ── flag ─────────────────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_USE_PI_EXECUTOR", raising=False)
    calls = []
    _wire(monkeypatch, calls)
    # also spy that pi/gates are never reached
    res = asyncio.run(pe.execute_issue(123))
    assert res.stage == "disabled" and res.ok is False
    assert calls == []  # nothing ran


def test_enabled_flag_parsing(monkeypatch):
    for val in ("true", "1", "yes", "TRUE"):
        monkeypatch.setenv("ZOE_USE_PI_EXECUTOR", val)
        assert pe.pi_executor_enabled() is True
    for val in ("false", "0", "no", ""):
        monkeypatch.setenv("ZOE_USE_PI_EXECUTOR", val)
        assert pe.pi_executor_enabled() is False


# ── prompt ───────────────────────────────────────────────────────────────────


def test_prompt_contains_contract():
    issue = {"number": 42, "title": "Add a docstring", "body": "to helper foo"}
    p = pe._build_implement_prompt(issue, "wt/pi-gh-42", pe.Path("/tmp/wt-pi-gh-42"))
    assert "Add a docstring" in p and "to helper foo" in p
    assert "/tmp/wt-pi-gh-42" in p and "wt/pi-gh-42" in p
    assert "gh pr create" in p and "PR_URL=" in p
    assert "Do NOT merge" in p and "--force" in p


# ── PR url extraction ────────────────────────────────────────────────────────


def test_extract_pr_url_from_output(monkeypatch):
    out = "blah\nPR_URL=https://github.com/o/r/pull/42\ndone"
    assert pe._extract_pr_url(out, "wt/x", pe.Path("/tmp")) == "https://github.com/o/r/pull/42"


def test_extract_pr_url_ignores_bare_url_uses_gh(monkeypatch):
    # A bare URL in pi's log (could be an unrelated PR) is NOT trusted; the
    # branch's own PR via gh is authoritative.
    monkeypatch.setattr(pe, "_gh_pr_for_branch", lambda b, w: None)
    out = "opened https://github.com/o/r/pull/13 ok"  # no PR_URL= contract line
    assert pe._extract_pr_url(out, "wt/x", pe.Path("/tmp")) is None


def test_extract_pr_url_fallback_to_gh_list(monkeypatch):
    monkeypatch.setattr(pe, "_gh_pr_for_branch",
                        lambda b, w: "https://github.com/o/r/pull/55")
    assert pe._extract_pr_url("no url here", "wt/x", pe.Path("/tmp")) == \
        "https://github.com/o/r/pull/55"


# ── orchestration ────────────────────────────────────────────────────────────


def test_no_pr_short_circuits(monkeypatch):
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls, pi_out="(no url and no commits)", has_commits=False)
    res = asyncio.run(pe.execute_issue(1))
    assert res.stage == "no_pr" and res.ok is False
    assert "tests" not in calls and "review" not in calls and "merge" not in calls


def test_happy_path_runs_gates_in_order(monkeypatch):
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls)
    res = asyncio.run(pe.execute_issue(1))
    assert res.ok is True and res.stage == "done" and res.merged is True
    assert calls == ["pi", "tests", "review", "merge"]


def test_tests_gate_failure_blocks_merge(monkeypatch):
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls, ft=_ft(ran=True, passed=False, summary="boom"))
    res = asyncio.run(pe.execute_issue(1))
    assert res.stage == "tests" and res.ok is False
    assert "review" not in calls and "merge" not in calls


def test_tests_not_run_is_not_a_failure(monkeypatch):
    # ran=False (no changed test files) must NOT block; flow continues.
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls, ft=_ft(ran=False, passed=False, summary="no tests"))
    res = asyncio.run(pe.execute_issue(1))
    assert res.ok is True and res.stage == "done"
    assert calls == ["pi", "tests", "review", "merge"]


def test_review_not_ready_blocks_merge(monkeypatch):
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls, rr=_rr(ready=False, reason="ci pending"))
    res = asyncio.run(pe.execute_issue(1))
    assert res.stage == "review" and res.ok is False
    assert "merge" not in calls


def test_no_merge_flag_stops_before_closeout(monkeypatch):
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls)
    res = asyncio.run(pe.execute_issue(1, no_merge=True))
    assert res.ok is True and res.stage == "review" and res.merged is False
    assert "merge" not in calls


def test_executor_fallback_opens_pr_when_pi_didnt(monkeypatch):
    _on(monkeypatch)
    calls = []
    _wire(monkeypatch, calls, pi_out="(committed but no PR)", has_commits=True)
    res = asyncio.run(pe.execute_issue(1))
    assert res.ok is True and res.pr_url == "https://github.com/o/r/pull/77"
    assert calls == ["pi", "tests", "review", "merge"]


def test_run_pi_implement_handles_missing_binary(monkeypatch):
    # If pi can't be spawned (e.g. binary missing), run_pi_implement must return
    # a clean failure, not let the exception escape into execute_issue.
    async def boom(*a, **k):
        raise OSError("pi: command not found")
    monkeypatch.setattr(pe.asyncio, "create_subprocess_exec", boom)
    monkeypatch.setattr(pe, "_pi_env", lambda: {})
    rc, out = asyncio.run(pe.run_pi_implement("prompt", pe.Path("/tmp")))
    assert rc == -1 and "could not run" in out
