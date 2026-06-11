import json

import pytest

import greploop_guard


def _packet(**overrides):
    data = {
        "task_type": "FIX_GREPTILE_FINDING",
        "pr": 66,
        "head_sha": "abc123",
        "base_branch": "main",
        "allowed_files": ["services/zoe-data/example.py"],
        "max_files": 1,
        "max_changed_lines": 50,
        "issue_text": "Fix the narrow bug",
        "commands_to_run": ["git diff --check"],
        "success_condition": "focused fix",
        "stop_condition": "stop on ambiguity",
        "forbidden_actions": greploop_guard.FORBIDDEN_ACTIONS,
    }
    data.update(overrides)
    return greploop_guard.GuardPacket(**data)


def test_validate_packet_rejects_broad_missing_context():
    packet = _packet(issue_text="")

    with pytest.raises(greploop_guard.GuardError, match="BLOCKED_MISSING_CONTEXT"):
        greploop_guard.validate_packet(packet)


def test_redact_removes_secret_like_values():
    payload = {"message": "Authorization: Bearer abc123", "nested": ["api_key=secret-value"]}

    redacted = greploop_guard.redact(payload)

    assert "abc123" not in json.dumps(redacted)
    assert "secret-value" not in json.dumps(redacted)


def test_analyze_result_rejects_files_outside_allowlist(monkeypatch):
    monkeypatch.setattr(greploop_guard, "_diff_files", lambda base_sha=None: ["services/zoe-data/other.py"])
    monkeypatch.setattr(greploop_guard, "_diff_changed_lines", lambda base_sha=None: 5)

    result = greploop_guard.analyze_result(_packet(), "done")

    assert result["classification"] == "REJECTED"
    assert result["outside_allowlist"] == ["services/zoe-data/other.py"]


def test_analyze_result_accepts_focused_diff(monkeypatch):
    monkeypatch.setattr(greploop_guard, "_diff_files", lambda base_sha=None: ["services/zoe-data/example.py"])
    monkeypatch.setattr(greploop_guard, "_diff_changed_lines", lambda base_sha=None: 5)

    result = greploop_guard.analyze_result(_packet(), "TESTS=git diff --check")

    assert result["classification"] == "APPLIED"


def test_analyze_result_checks_committed_diff_from_pre_run_sha(monkeypatch):
    seen = {}

    def fake_diff_files(base_sha=None):
        seen["files_base_sha"] = base_sha
        return ["services/zoe-data/other.py"]

    def fake_diff_changed_lines(base_sha=None):
        seen["lines_base_sha"] = base_sha
        return 5

    monkeypatch.setattr(greploop_guard, "_diff_files", fake_diff_files)
    monkeypatch.setattr(greploop_guard, "_diff_changed_lines", fake_diff_changed_lines)

    result = greploop_guard.analyze_result(_packet(), "done", pre_run_sha="before-sha")

    assert result["classification"] == "REJECTED"
    assert seen == {"files_base_sha": "before-sha", "lines_base_sha": "before-sha"}


def test_lock_prevents_duplicate_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)

    with greploop_guard.acquire_lock(66):
        with pytest.raises(greploop_guard.GuardError, match="already running"):
            with greploop_guard.acquire_lock(66):
                pass


def test_write_json_redacts_state(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)

    greploop_guard._write_json(66, "status.json", {"token": "token=abc123"})

    assert "abc123" not in (tmp_path / "pr-66" / "status.json").read_text()


@pytest.mark.asyncio
async def test_cheap_runner_blocks_before_budget_exceeded(monkeypatch):
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_CMD", "false")
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD", "99")
    monkeypatch.setattr(greploop_guard, "MAX_COST_USD", 1.0)

    status, output = await greploop_guard._run_cheap_agent(_packet())

    assert status == "BLOCKED_BUDGET_EXCEEDED"
    assert "max_cost_usd=1.0" in output


def test_ci_status_from_rollup_flags_pending_and_failed():
    rollup = [
        {"name": "validate", "status": "IN_PROGRESS", "conclusion": ""},
        {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "FAILURE"},
    ]

    out = greploop_guard._ci_status_from_rollup(rollup)

    assert out["ok"] is False
    assert out["reason"] == "CI_PENDING"
    assert "validate" in out["pending"]


def test_ci_status_from_rollup_accepts_success():
    rollup = [
        {"name": "validate", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]

    assert greploop_guard._ci_status_from_rollup(rollup)["ok"] is True


def test_ci_status_from_rollup_rejects_empty_rollup():
    out = greploop_guard._ci_status_from_rollup([])

    assert out["ok"] is False
    assert out["reason"] == "CI_NO_CHECKS"


def test_gh_mergeable_state_blocks_non_mergeable_and_unknown_state(monkeypatch):
    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        return type(
            "P",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "state": "OPEN",
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "UNKNOWN",
                        "statusCheckRollup": [
                            {"name": "validate", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        ],
                    }
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    out = greploop_guard._gh_mergeable_state(66)

    assert out["ok"] is False
    assert out["reason"] == "GH_NOT_MERGEABLE"


def test_gh_mergeable_state_blocks_conflicting_mergeable(monkeypatch):
    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        return type(
            "P",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "state": "OPEN",
                        "mergeable": "CONFLICTING",
                        "mergeStateStatus": "DIRTY",
                        "statusCheckRollup": [],
                    }
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    out = greploop_guard._gh_mergeable_state(66)

    assert out["ok"] is False
    assert out["reason"] == "GH_NOT_MERGEABLE"


@pytest.mark.asyncio
async def test_assess_merge_readiness_blocks_low_confidence(monkeypatch):
    async def fake_status(**_kwargs):
        return {"confidenceScore": 3, "reviewIsRunning": False, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66, target_confidence=5)

    assert out["ready"] is False
    assert any("GREPTILE_CONFIDENCE" in b for b in out["blockers"])


@pytest.mark.asyncio
async def test_run_guard_once_does_not_retrigger_active_reviewing_files(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": True,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
            "codeReviews": [{"status": "REVIEWING_FILES"}],
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    async def fail_trigger(**_kwargs):
        raise AssertionError("active Greptile reviews must not be retriggered")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "WAITING_GREPTILE"
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "WAITING_GREPTILE"
    assert state["iteration"] == 0


@pytest.mark.asyncio
async def test_run_guard_once_active_review_does_not_trip_no_progress(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": True,
            "headSha": None,
            "reviewCompleteness": "No Greptile review comments",
            "codeReviews": [{"status": "REVIEWING_FILES"}],
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)

    for _ in range(greploop_guard.NO_PROGRESS_LIMIT + 1):
        out = await greploop_guard.run_guard_once(66)
        assert out["ok"] is True
        assert out["state"] == "WAITING_GREPTILE"

    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "WAITING_GREPTILE"
    assert state["iteration"] == 0
    assert state["no_progress_count"] == 0


@pytest.mark.asyncio
async def test_run_guard_once_ignores_pathless_greptile_summary_when_confident(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 5,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "summary-1",
                    "file_path": "",
                    "body": "Greptile summary with confidence 5/5 and no inline findings.",
                    "addressed": False,
                }
            ]
        }

    async def fail_trigger(**_kwargs):
        raise AssertionError("confident summary-only review must not retrigger Greptile")

    async def fail_runner(*_args, **_kwargs):
        raise AssertionError("pathless summary comments must not become repair packets")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(greploop_guard, "_run_cheap_agent", fail_runner)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "READY_TO_MERGE"
    assert state["greptile"]["unaddressed_count"] == 0
    assert state["greptile"]["summary_count"] == 1


@pytest.mark.asyncio
async def test_run_guard_once_blocks_permanently_active_review(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": True,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
            "codeReviews": [{"status": "REVIEWING_FILES"}],
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)

    out = {}
    for _ in range(greploop_guard.MAX_ITERATIONS + 1):
        out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is False
    assert out["state"] == "BLOCKED_GREPTILE_STUCK"
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "BLOCKED_GREPTILE_STUCK"
    assert state["no_progress_count"] == 0


@pytest.mark.asyncio
async def test_merge_pr_when_ready_merges_when_assessment_passes(tmp_path, monkeypatch):
    async def fake_assess(_pr_number, **_kwargs):
        return {"ready": True, "blockers": [], "greptile": {}, "gh": {"ok": True}}

    calls: list[list[str]] = []

    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        calls.append(list(args))
        if args[:2] == ["pr", "merge"]:
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return type(
            "P",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "mergeCommit": {"oid": "deadbeef"},
                        "url": "https://github.com/o/r/pull/66",
                        "state": "MERGED",
                    }
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    out = await greploop_guard.merge_pr_when_ready(66)

    assert out["ok"] is True
    assert out["state"] == "MERGED"
    assert out["merge_commit"] == "deadbeef"
    assert ["pr", "merge", "66", "--squash"] in calls


@pytest.mark.asyncio
async def test_cheap_runner_command_does_not_expand_shell_metacharacters(monkeypatch):
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_CMD", "python3 -c 'import sys; sys.stdin.read(); print(\"$HOME\")'")
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD", "0")

    status, output = await greploop_guard._run_cheap_agent(_packet())

    assert status == "OK"
    assert "$HOME" in output
