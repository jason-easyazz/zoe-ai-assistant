import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

import greploop_guard

_ORIGINAL_GH_THREAD_COUNTS = greploop_guard._gh_thread_counts
_ORIGINAL_GH_OPEN_PRS_RUNNING = greploop_guard._gh_open_prs_with_running_greptile


@pytest.fixture(autouse=True)
def _default_github_helpers(monkeypatch):
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: None,
    )
    # P2a: the repo-wide capacity check now consults GitHub for in-flight reviews.
    # Default it to "no other running reviews" so tests never hit the network; tests
    # that exercise capacity override this explicitly.
    monkeypatch.setattr(
        greploop_guard,
        "_gh_open_prs_with_running_greptile",
        lambda repo=greploop_guard.DEFAULT_REPO: set(),
    )


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


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def test_run_greploop_wrapper_switches_to_matching_pr_worktree(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_worktree = tmp_path / "pr-worktree"
    fake_worktree.mkdir()
    target_script = fake_worktree / "scripts" / "maintenance" / "run_greploop_guard.sh"
    target_script.parent.mkdir(parents=True)
    _write_executable(target_script, "#!/usr/bin/env bash\necho switched-to-pr-worktree \"$@\"\nexit 43\n")
    _write_executable(
        bin_dir / "gh",
        "#!/usr/bin/env bash\nprintf 'codex/pr-branch\n'\n",
    )
    _write_executable(
        bin_dir / "git",
        f"#!/usr/bin/env bash\n"
        f"set -euo pipefail\n"
        f"if [[ \"$*\" == *'branch --show-current'* ]]; then printf 'main\\n'; exit 0; fi\n"
        f"if [[ \"$*\" == *'worktree list --porcelain'* ]]; then\n"
        f"  printf 'worktree {fake_worktree}\\nbranch refs/heads/codex/pr-branch\\n'\n"
        f"  for i in $(seq 1 500); do printf 'worktree /tmp/other-%s\\nbranch refs/heads/other-%s\\n' \"$i\" \"$i\"; done\n"
        f"  exit 0\n"
        f"fi\n"
        f"exit 1\n",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH", None)

    proc = subprocess.run(
        [str(greploop_guard.REPO_ROOT / "scripts/maintenance/run_greploop_guard.sh"), "--pr", "66", "--once"],
        cwd=greploop_guard.REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 43
    assert "switched-to-pr-worktree --pr 66 --once" in proc.stdout


def test_run_greploop_wrapper_loads_env_before_pr_head_lookup(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_worktree = tmp_path / "pr-worktree"
    fake_worktree.mkdir()
    target_script = fake_worktree / "scripts" / "maintenance" / "run_greploop_guard.sh"
    target_script.parent.mkdir(parents=True)
    _write_executable(target_script, "#!/usr/bin/env bash\necho env-loaded\nexit 0\n")
    hermes_env = fake_home / ".hermes" / ".env"
    hermes_env.parent.mkdir()
    hermes_env.write_text("ZOE_GITHUB_REPO=example/env-repo\n")
    _write_executable(
        bin_dir / "gh",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" != *'--repo example/env-repo'* ]]; then echo \"$*\" >&2; exit 9; fi\n"
        "printf 'main\\n'\n",
    )
    _write_executable(
        bin_dir / "git",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'branch --show-current'* ]]; then printf 'other\\n'; exit 0; fi\n"
        f"if [[ \"$*\" == *'worktree list --porcelain'* ]]; then printf 'worktree {fake_worktree}\\nbranch refs/heads/main\\n'; exit 0; fi\n"
        "exit 1\n",
    )
    env = {**os.environ, "HOME": str(fake_home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH", None)
    env.pop("ZOE_GITHUB_REPO", None)

    proc = subprocess.run(
        [str(greploop_guard.REPO_ROOT / "scripts/maintenance/run_greploop_guard.sh"), "--pr", "66", "--packet-only"],
        cwd=greploop_guard.REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "env-loaded" in proc.stdout


def test_run_greploop_wrapper_preserves_caller_repo_over_env_file(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_worktree = tmp_path / "pr-worktree"
    fake_worktree.mkdir()
    target_script = fake_worktree / "scripts" / "maintenance" / "run_greploop_guard.sh"
    target_script.parent.mkdir(parents=True)
    _write_executable(target_script, "#!/usr/bin/env bash\necho caller-repo-kept\nexit 0\n")
    hermes_env = fake_home / ".hermes" / ".env"
    hermes_env.parent.mkdir()
    hermes_env.write_text("ZOE_GITHUB_REPO=example/env-repo\n")
    _write_executable(
        bin_dir / "gh",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" != *'--repo example/caller-repo'* ]]; then echo \"$*\" >&2; exit 9; fi\n"
        "printf 'main\\n'\n",
    )
    _write_executable(
        bin_dir / "git",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'branch --show-current'* ]]; then printf 'other\\n'; exit 0; fi\n"
        f"if [[ \"$*\" == *'worktree list --porcelain'* ]]; then printf 'worktree {fake_worktree}\\nbranch refs/heads/main\\n'; exit 0; fi\n"
        "exit 1\n",
    )
    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ZOE_GITHUB_REPO": "example/caller-repo",
    }
    env.pop("ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH", None)

    proc = subprocess.run(
        [str(greploop_guard.REPO_ROOT / "scripts/maintenance/run_greploop_guard.sh"), "--pr", "66", "--packet-only"],
        cwd=greploop_guard.REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "caller-repo-kept" in proc.stdout


def test_run_greploop_wrapper_blocks_repair_without_pr_worktree(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "gh",
        "#!/usr/bin/env bash\nprintf 'codex/pr-branch\n'\n",
    )
    _write_executable(
        bin_dir / "git",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'branch --show-current'* ]]; then printf 'main\\n'; exit 0; fi\n"
        "if [[ \"$*\" == *'worktree list --porcelain'* ]]; then printf 'worktree /tmp/other\\nbranch refs/heads/other\\n'; exit 0; fi\n"
        "exit 1\n",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH", None)

    proc = subprocess.run(
        [str(greploop_guard.REPO_ROOT / "scripts/maintenance/run_greploop_guard.sh"), "--pr=66", "--packet-only"],
        cwd=greploop_guard.REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "Greploop repair mode must run from the PR branch worktree." in proc.stderr
    assert "PR #66 head branch: codex/pr-branch" in proc.stderr


def test_run_greploop_wrapper_blocks_repair_when_pr_head_unknown(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "gh",
        "#!/usr/bin/env bash\nexit 1\n",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH", None)

    proc = subprocess.run(
        [str(greploop_guard.REPO_ROOT / "scripts/maintenance/run_greploop_guard.sh"), "--pr", "66", "--once"],
        cwd=greploop_guard.REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "could not determine the PR head branch" in proc.stderr


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


def test_write_json_uses_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    real_replace = greploop_guard.os.replace
    calls = []

    def tracked_replace(src, dst):
        calls.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(greploop_guard.os, "replace", tracked_replace)

    greploop_guard._write_json(66, "status.json", {"terminal_state": "READY_TO_MERGE"})

    status_path = tmp_path / "pr-66" / "status.json"
    assert calls
    tmp_path_used, final_path = calls[0]
    assert tmp_path_used.parent == status_path.parent
    assert tmp_path_used.name.startswith(".status.json.")
    assert final_path == status_path
    assert not tmp_path_used.exists()
    assert json.loads(status_path.read_text())["terminal_state"] == "READY_TO_MERGE"


def test_fsync_dir_uses_directory_open_flag(tmp_path, monkeypatch):
    calls = []

    def fake_open(path, flags):
        calls.append((Path(path), flags))
        return 123

    monkeypatch.setattr(greploop_guard.os, "open", fake_open)
    monkeypatch.setattr(greploop_guard.os, "fsync", lambda fd: None)
    monkeypatch.setattr(greploop_guard.os, "close", lambda fd: None)

    greploop_guard._fsync_dir(tmp_path)

    assert calls == [(tmp_path, greploop_guard.os.O_RDONLY | getattr(greploop_guard.os, "O_DIRECTORY", 0))]


def test_read_guard_state_returns_retry_for_partial_json(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    state_dir = tmp_path / "pr-66"
    state_dir.mkdir()
    (state_dir / "status.json").write_text('{"terminal_state": "WAITING_GREPTILE"')

    state = greploop_guard.read_guard_state(66)

    assert state["pr"] == 66
    assert state["state"] == "STALE_READ_RETRY"
    assert state["terminal_state"] == "STALE_READ_RETRY"
    assert state["error"] == "invalid_json_state"


def test_load_status_resets_stale_retry_state(monkeypatch):
    monkeypatch.setattr(
        greploop_guard,
        "read_guard_state",
        lambda pr: {
            "pr": int(pr),
            "state": "STALE_READ_RETRY",
            "terminal_state": "STALE_READ_RETRY",
            "error": "invalid_json_state",
        },
    )

    state = greploop_guard._load_status(66)

    assert state == {
        "pr": 66,
        "iteration": 0,
        "no_progress_count": 0,
        "same_error_count": 0,
        "last_progress_key": "",
        "last_error_hash": "",
    }


@pytest.mark.asyncio
async def test_cheap_runner_blocks_before_budget_exceeded(monkeypatch):
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_CMD", "false")
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD", "99")
    monkeypatch.setattr(greploop_guard, "MAX_COST_USD", 1.0)
    monkeypatch.setattr(greploop_guard, "verify_pr_checkout_for_repair", lambda packet: {"ok": True})

    status, output = await greploop_guard._run_cheap_agent(_packet())

    assert status == "BLOCKED_BUDGET_EXCEEDED"
    assert "max_cost_usd=1.0" in output


def test_effective_pr_head_sha_returns_none_when_all_sources_missing(monkeypatch):
    monkeypatch.setattr(greploop_guard, "_local_head_sha", lambda: None)

    assert greploop_guard._effective_pr_head_sha({"headSha": None}, {"ok": False}) is None


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


def test_gh_thread_counts_tracks_unresolved_greptile_threads_separately(monkeypatch):
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_review_threads",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: [
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {"author": {"login": "human-reviewer"}, "path": "a.py", "body": "human thread"}
                    ]
                },
            },
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {"author": {"login": "greptile-apps"}, "path": "b.py", "body": "greptile thread"}
                    ]
                },
            },
            {
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "id": "PRRC_resolved",
                            "author": {"login": "greptile-apps"},
                            "path": "c.py",
                            "line": 12,
                            "body": "resolved greptile thread",
                            "url": "https://github.example/comment/3",
                        }
                    ]
                },
            },
        ],
    )

    out = _ORIGINAL_GH_THREAD_COUNTS(66)

    assert out["unresolved"] == 2
    assert out["unresolved_greptile_threads"] == 1
    assert out["greptile_thread_count"] == 2
    assert ("id", "PRRC_resolved") in out["resolved_greptile_keys"]
    assert ("url", "https://github.example/comment/3") in out["resolved_greptile_keys"]


@pytest.mark.asyncio
async def test_assess_merge_readiness_blocks_low_confidence(monkeypatch):
    async def fake_status(**_kwargs):
        return {"confidenceScore": 3, "reviewIsRunning": False, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", lambda *args, **kwargs: {"ok": False})
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda *args, **kwargs: {"ok": False, "unresolved": -1, "resolved_greptile_keys": []},
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66, target_confidence=5)

    assert out["ready"] is False
    assert any("GREPTILE_CONFIDENCE" in b for b in out["blockers"])


@pytest.mark.asyncio
async def test_assess_merge_readiness_fetches_greptile_status_and_comments_concurrently(monkeypatch):
    comments_started = asyncio.Event()

    async def fake_status(**_kwargs):
        await asyncio.wait_for(comments_started.wait(), timeout=0.5)
        return {
            "confidenceScore": 5,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        comments_started.set()
        await asyncio.sleep(0)
        return {"findings": []}

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda *args, **kwargs: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {
            "ok": True,
            "mergeStateStatus": "CLEAN",
            "ci": {"ok": True, "pending": [], "failures": []},
        },
    )

    out = await greploop_guard.assess_merge_readiness(66, target_confidence=5)

    assert out["ready"] is True
    assert out["blockers"] == []


def test_clear_greptile_wait_state_removes_wait_diagnostics():
    state = {
        "waiting_greptile_count": 3,
        "greptile_wait_started_at": 1_000.0,
        "greptile_wait_last_seen_at": 1_050.0,
        "greptile_wait_elapsed_seconds": 50,
        "greptile_next_poll_after": 1_120.0,
        "greptile": {"wait_count": 3, "wait_elapsed_seconds": 50},
    }

    greploop_guard._clear_greptile_wait_state(state)

    assert state == {"waiting_greptile_count": 0}


@pytest.mark.asyncio
async def test_gather_or_raise_waits_for_all_tasks_before_raising():
    completed = asyncio.Event()

    async def fail():
        raise RuntimeError("status failed")

    async def finish():
        await asyncio.sleep(0)
        completed.set()
        return {"findings": []}

    with pytest.raises(RuntimeError, match="status failed"):
        await greploop_guard._gather_or_raise(fail(), finish())

    assert completed.is_set()


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
async def test_run_guard_once_fetches_greptile_status_and_comments_concurrently(tmp_path, monkeypatch):
    comments_started = asyncio.Event()

    async def fake_status(**_kwargs):
        await asyncio.wait_for(comments_started.wait(), timeout=0.5)
        return {
            "confidenceScore": 5,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        comments_started.set()
        await asyncio.sleep(0)
        return {"findings": []}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "headRefOid": "abc",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
        },
    )

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"


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
async def test_run_guard_once_active_review_uses_elapsed_poll_windows(tmp_path, monkeypatch):
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

    now = {"value": 1_000.0}
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_TIMEOUT_SECONDS", 600)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_POLL_SECONDS", 120)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: now["value"])
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)

    for timestamp in (1_000.0, 1_001.0, 1_050.0):
        now["value"] = timestamp
        out = await greploop_guard.run_guard_once(66)
        assert out["ok"] is True
        assert out["state"] == "WAITING_GREPTILE"

    state = greploop_guard.read_guard_state(66)
    assert state["waiting_greptile_count"] == 1
    assert state["greptile_wait_started_at"] == 1_000.0
    assert state["greptile"]["wait_elapsed_seconds"] == 50

    now["value"] = 1_121.0
    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["wait"]["poll_due"] is True
    state = greploop_guard.read_guard_state(66)
    assert state["waiting_greptile_count"] == 2


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
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "READY_TO_MERGE"
    assert state["greptile"]["unaddressed_count"] == 0
    assert state["greptile"]["summary_count"] == 1


@pytest.mark.asyncio
async def test_trigger_review_safely_skips_recent_same_head(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("same-head trigger should be deduped")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "TRIGGER_COOLDOWN_SECONDS", 900)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    state = {
        "pr": 66,
        "last_triggered_head_sha": "abc",
        "last_triggered_at": 500.0,
    }

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False},
        state=state,
        source="test",
    )

    assert out["triggered"] is False
    assert out["skipped"] is True
    assert out["reason"] == "recently_triggered_for_head"
    assert out["retry_after_seconds"] == 400
    saved = greploop_guard.read_guard_state(66)
    assert saved["last_trigger_decision"]["reason"] == "recently_triggered_for_head"


@pytest.mark.asyncio
async def test_trigger_review_safely_force_bypasses_same_head_cooldown(tmp_path, monkeypatch):
    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"triggered": True, "ok": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    state = {
        "pr": 66,
        "last_triggered_head_sha": "abc",
        "last_triggered_at": 999.0,
    }

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False},
        state=state,
        force=True,
        source="test-force",
    )

    assert out == {"triggered": True, "ok": True}
    assert triggered["pr_number"] == 66
    assert state["last_triggered_head_sha"] == "abc"
    assert state["last_trigger_source"] == "test-force"


@pytest.mark.asyncio
async def test_trigger_review_safely_does_not_cooldown_failed_trigger(tmp_path, monkeypatch):
    async def fake_trigger(**_kwargs):
        return {"success": False, "error": "provider busy"}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    state = {"pr": 66}

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False},
        state=state,
        source="test-failed-trigger",
    )

    assert out == {"success": False, "error": "provider busy"}
    assert "last_triggered_head_sha" not in state
    assert "last_triggered_at" not in state
    assert state["last_trigger_decision"]["success"] is False
    assert state["last_trigger_decision"]["triggered"] is False
    assert state["last_trigger_decision"]["response"] == out


@pytest.mark.asyncio
async def test_trigger_review_safely_skips_running_review(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("running review should not be retriggered")

    def fail_observation(*_args, **_kwargs):
        raise AssertionError("running review should skip before gh pr view")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", fail_observation)

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": True},
        state={"pr": 66},
        source="test-running",
    )

    assert out["triggered"] is False
    assert out["reason"] == "greptile_review_running"


@pytest.mark.asyncio
async def test_trigger_review_safely_skips_github_running_same_head(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("same-head GitHub running check should not retrigger Greptile")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "headRefOid": "abc",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "IN_PROGRESS", "conclusion": ""},
            ],
        },
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False},
        state={"pr": 66},
        source="test-gh-running",
    )

    assert out["triggered"] is False
    assert out["reason"] == "github_greptile_check_running"
    saved = greploop_guard.read_guard_state(66)
    assert saved["last_trigger_decision"]["reason"] == "github_greptile_check_running"


@pytest.mark.asyncio
async def test_trigger_review_safely_triggers_on_github_head_mismatch(tmp_path, monkeypatch):
    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"success": True, "triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "headRefOid": "old-sha",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "IN_PROGRESS", "conclusion": ""},
            ],
        },
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "new-sha", "reviewIsRunning": False},
        state={"pr": 66},
        source="test-gh-head-mismatch",
    )

    assert out == {"success": True, "triggered": True}
    assert triggered["pr_number"] == 66
    saved = greploop_guard.read_guard_state(66)
    assert saved["last_triggered_head_sha"] == "new-sha"
    assert saved["last_trigger_decision"]["triggered"] is True


@pytest.mark.asyncio
async def test_trigger_review_safely_skips_github_clear_same_head(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("same-head clear GitHub review should not retrigger Greptile")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "headRefOid": "abc",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "unresolved": 0},
    )
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-gh-clear",
    )

    assert out["triggered"] is False
    assert out["reason"] == "github_greptile_review_already_clear"
    assert out["confidence"] == 5
    saved = greploop_guard.read_guard_state(66)
    assert saved["last_trigger_decision"]["reason"] == "github_greptile_review_already_clear"


@pytest.mark.asyncio
async def test_trigger_review_with_guard_lock_reports_lock_contention(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("locked trigger path should not call Greptile")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)

    with greploop_guard.acquire_lock(66):
        out = await greploop_guard.trigger_review_with_guard_lock(pr_number=66, source="test-lock")

    assert out["success"] is False
    assert out["triggered"] is False
    assert out["skipped"] is True
    assert out["reason"] == "guard_already_running"
    assert out["source"] == "test-lock"


@pytest.mark.asyncio
async def test_trigger_review_safely_skips_repo_capacity_for_other_waiting_pr(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("repo-level Greptile capacity should defer new triggers")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_LIMIT", 1)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_STALE_SECONDS", 600)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": False},
    )
    greploop_guard._write_json(
        67,
        "status.json",
        {
            "pr": 67,
            "terminal_state": "WAITING_GREPTILE",
            "greptile_wait_started_at": 900.0,
            "greptile_wait_last_seen_at": 990.0,
            "greptile_next_poll_after": 1_100.0,
        },
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-repo-capacity",
    )

    assert out["triggered"] is False
    assert out["reason"] == "repo_greptile_review_capacity"
    assert out["active_prs"] == [67]
    assert out["retry_after_seconds"] == 100


@pytest.mark.asyncio
async def test_run_guard_once_skips_recent_same_head_trigger(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    async def fail_trigger(**_kwargs):
        raise AssertionError("same-head trigger should be deduped")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "TRIGGER_COOLDOWN_SECONDS", 900)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    greploop_guard._write_json(
        66,
        "status.json",
        {"pr": 66, "last_triggered_head_sha": "abc", "last_triggered_at": 500.0},
    )
    real_write_json = greploop_guard._write_json
    status_writes = []

    def tracked_write_json(pr_number, name, payload):
        if name == "status.json":
            status_writes.append(dict(payload))
        return real_write_json(pr_number, name, payload)

    monkeypatch.setattr(greploop_guard, "_write_json", tracked_write_json)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "WAITING_GREPTILE"
    assert out["triggered_review"]["triggered"] is False
    assert out["triggered_review"]["reason"] == "recently_triggered_for_head"
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "WAITING_GREPTILE"
    assert state["last_trigger_decision"]["reason"] == "recently_triggered_for_head"
    trigger_decision_writes = [item for item in status_writes if "last_trigger_decision" in item]
    assert len(trigger_decision_writes) == 1
    assert trigger_decision_writes[0]["terminal_state"] == "WAITING_GREPTILE"
    assert trigger_decision_writes[0]["triggered_review"]["reason"] == "recently_triggered_for_head"


@pytest.mark.asyncio
async def test_run_guard_once_refreshes_head_after_repair_before_trigger(tmp_path, monkeypatch):
    statuses = [
        {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "old-sha",
            "reviewCompleteness": "0/1 Greptile comments addressed",
        },
        {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "new-sha",
            "reviewCompleteness": "0/1 Greptile comments addressed",
        },
    ]

    async def fake_status(**_kwargs):
        return statuses.pop(0)

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "comment-1",
                    "file_path": "services/zoe-data/example.py",
                    "line": 42,
                    "body": "Fix the narrow issue",
                    "addressed": False,
                }
            ]
        }

    async def fake_runner(*_args, **_kwargs):
        return "OK", "applied focused fix"

    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"success": True, "triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "TRIGGER_COOLDOWN_SECONDS", 900)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(greploop_guard, "_run_cheap_agent", fake_runner)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard, "_local_head_sha", lambda: "local-repaired-sha")
    monkeypatch.setattr(greploop_guard, "_diff_files", lambda base_sha=None: ["services/zoe-data/example.py"])
    monkeypatch.setattr(greploop_guard, "_diff_changed_lines", lambda base_sha=None: 2)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "headRefOid": "new-sha",
            "state": "OPEN",
            "statusCheckRollup": [],
        },
    )
    greploop_guard._write_json(
        66,
        "status.json",
        {"pr": 66, "last_triggered_head_sha": "old-sha", "last_triggered_at": 500.0},
    )

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "WAITING_GREPTILE"
    assert out["triggered_review"]["triggered"] is True
    assert triggered["pr_number"] == 66
    state = greploop_guard.read_guard_state(66)
    assert state["last_triggered_head_sha"] == "new-sha"
    assert state["last_trigger_decision"]["headSha"] == "new-sha"
    assert not statuses


@pytest.mark.asyncio
async def test_run_guard_once_retriggers_low_confidence_pathless_summary(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "Summary-only review below confidence target",
        }

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "summary-1",
                    "file_path": "",
                    "body": "Greptile summary with confidence 4/5 and no inline findings.",
                    "addressed": False,
                }
            ]
        }

    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"triggered": True}

    async def fail_runner(*_args, **_kwargs):
        raise AssertionError("pathless summary comments must not become repair packets")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(greploop_guard, "_run_cheap_agent", fail_runner)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "WAITING_GREPTILE"
    assert out["triggered_review"] == {"triggered": True}
    assert triggered["pr_number"] == 66
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "WAITING_GREPTILE"
    assert state["greptile"]["unaddressed_count"] == 0
    assert state["greptile"]["summary_count"] == 1


def test_filter_actionable_findings_requires_completed_check_for_zero_unresolved_shortcut():
    findings = [
        {
            "id": "comment-1",
            "file_path": "services/zoe-data/example.py",
            "line": 99,
            "body": "MCP stale body that no longer matches GitHub exactly",
            "addressed": False,
        }
    ]
    thread_counts = {"ok": True, "unresolved": 0, "resolved_greptile_keys": []}

    blocked = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts=thread_counts,
        clear_when_no_unresolved=False,
    )
    cleared = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts=thread_counts,
        clear_when_no_unresolved=True,
    )

    assert blocked == findings
    assert cleared == []


def test_filter_actionable_findings_allows_legacy_match_when_no_unresolved_threads():
    body = "Resolved multi-line Greptile body"
    findings = [
        {
            "id": "comment-1",
            "file_path": "services/zoe-data/example.py",
            "line": 10,
            "body": body,
            "addressed": False,
        }
    ]

    out = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts={
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [("path_title", f"services/zoe-data/example.py:{body}")],
        },
    )

    assert out == []


def test_filter_actionable_findings_allows_legacy_match_with_only_human_threads_open():
    body = "Resolved Greptile body while human thread remains open"
    findings = [
        {
            "id": "comment-1",
            "file_path": "services/zoe-data/example.py",
            "line": 10,
            "body": body,
            "addressed": False,
        }
    ]

    out = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts={
            "ok": True,
            "unresolved": 1,
            "unresolved_greptile_threads": 0,
            "resolved_greptile_keys": [("path_title", f"services/zoe-data/example.py:{body}")],
        },
    )

    assert out == []


def test_filter_actionable_findings_keeps_new_line_when_unresolved_threads_exist():
    body = "Resolved multi-line Greptile body"
    findings = [
        {
            "id": "comment-new",
            "file_path": "services/zoe-data/example.py",
            "line": 99,
            "body": body,
            "addressed": False,
        }
    ]

    out = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts={
            "ok": True,
            "unresolved": 1,
            "unresolved_greptile_threads": 1,
            "resolved_greptile_keys": [
                ("path_line_title", f"services/zoe-data/example.py:10:{body}"),
                ("path_title", f"services/zoe-data/example.py:{body}"),
            ],
        },
    )

    assert out == findings


def test_filter_actionable_findings_matches_resolved_comment_url():
    findings = [
        {
            "id": "comment-new",
            "url": "https://github.example/review/comment/1",
            "file_path": "services/zoe-data/example.py",
            "line": 99,
            "body": "Resolved via exact URL",
            "addressed": False,
        }
    ]

    out = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts={
            "ok": True,
            "unresolved": 1,
            "resolved_greptile_keys": [("url", "https://github.example/review/comment/1")],
        },
    )

    assert out == []


def test_filter_actionable_findings_matches_resolved_comment_id():
    findings = [
        {
            "id": "PRRC_abc123",
            "url": "https://github.example/review/comment/2",
            "file_path": "services/zoe-data/example.py",
            "line": 99,
            "body": "Resolved via exact ID",
            "addressed": False,
        }
    ]

    out = greploop_guard._filter_actionable_findings(
        findings,
        pr_number=66,
        thread_counts={
            "ok": True,
            "unresolved": 1,
            "resolved_greptile_keys": [("id", "PRRC_abc123")],
        },
    )

    assert out == []


@pytest.mark.asyncio
async def test_run_guard_once_uses_github_summary_confidence_without_retrigger(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    async def fail_trigger(**_kwargs):
        raise AssertionError("GitHub 5/5 summary should prevent Greptile retrigger")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"
    assert out["greptile"]["confidenceScore"] == 5


@pytest.mark.asyncio
async def test_run_guard_once_waits_when_historical_confidence_has_no_completed_check(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "state": "OPEN", "statusCheckRollup": []},
    )
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "WAITING_GREPTILE"
    assert out["triggered_review"] == {"triggered": True}
    assert triggered["pr_number"] == 66

@pytest.mark.asyncio
async def test_run_guard_once_uses_github_head_ref_for_progress_and_packet(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": None,
            "reviewCompleteness": "0/1 Greptile comments addressed",
        }

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "comment-1",
                    "file_path": "services/zoe-data/example.py",
                    "line": 42,
                    "body": "Fix the narrow issue",
                    "addressed": False,
                }
            ]
        }

    def fail_local_head():
        raise AssertionError("GitHub headRefOid should be used before local HEAD")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(greploop_guard, "_local_head_sha", fail_local_head)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "headRefOid": "github-head-sha",
            "state": "OPEN",
            "statusCheckRollup": [],
        },
    )

    out = await greploop_guard.run_guard_once(66, packet_only=True)

    assert out["ok"] is True
    assert out["state"] == "PACKET_READY"
    assert out["packet"]["head_sha"] == "github-head-sha"
    state = greploop_guard.read_guard_state(66)
    assert state["last_progress_key"] == "github-head-sha:4:1:1"
    packet = json.loads((tmp_path / "pr-66" / "last_packet.json").read_text())
    assert packet["head_sha"] == "github-head-sha"


@pytest.mark.asyncio
async def test_run_guard_once_suppresses_stale_resolved_github_thread(tmp_path, monkeypatch):
    body = "P2 stale comment body"

    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "0/1 Greptile comments addressed",
        }

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "comment-1",
                    "file_path": "services/zoe-data/example.py",
                    "line": 42,
                    "body": body,
                    "addressed": False,
                }
            ]
        }

    async def fail_trigger(**_kwargs):
        raise AssertionError("resolved GitHub thread should not retrigger Greptile")

    async def fail_runner(*_args, **_kwargs):
        raise AssertionError("resolved GitHub thread should not become a repair packet")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(greploop_guard, "_run_cheap_agent", fail_runner)
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [("path_title", f"services/zoe-data/example.py:{body}")],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"
    state = greploop_guard.read_guard_state(66)
    assert state["greptile"]["unaddressed_count"] == 0


@pytest.mark.asyncio
async def test_assess_merge_readiness_ignores_mcp_comment_when_thread_resolved(monkeypatch):
    body = "Resolved stale Greptile body"

    async def fake_status(**_kwargs):
        return {"confidenceScore": None, "reviewIsRunning": False, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "comment-1",
                    "file_path": "services/zoe-data/example.py",
                    "line": 7,
                    "body": body,
                    "addressed": False,
                }
            ]
        }

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [("path_title", f"services/zoe-data/example.py:{body}")],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66)

    assert out["ready"] is True
    assert out["unaddressed_count"] == 0
    assert out["unresolved_review_threads"] == 0


@pytest.mark.asyncio
async def test_assess_merge_readiness_treats_completed_github_check_as_not_running(monkeypatch):
    async def fake_status(**_kwargs):
        return {"confidenceScore": None, "reviewIsRunning": True, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66)

    assert out["ready"] is True
    assert "GREPTILE_REVIEW_RUNNING" not in out["blockers"]

def test_read_observed_guard_state_marks_stale_waiting_as_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    greploop_guard._write_json(
        66,
        "status.json",
        {"pr": 66, "terminal_state": "WAITING_GREPTILE", "waiting_greptile_count": 3},
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "MERGED",
            "url": "https://github.com/o/r/pull/66",
            "statusCheckRollup": [],
        },
    )

    state = greploop_guard.read_observed_guard_state(66)

    assert state["terminal_state"] == "MERGED"
    assert state["historical_terminal_state"] == "WAITING_GREPTILE"
    assert state["observed"]["state"] == "MERGED"


@pytest.mark.asyncio
async def test_run_guard_once_treats_completed_github_check_as_not_running(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": True,
            "headSha": "abc",
            "reviewCompleteness": "REVIEWING_FILES",
            "codeReviews": [{"status": "REVIEWING_FILES"}],
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(
        greploop_guard,
        "_greptile_confidence_from_github_comments",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: 5,
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 0,
            "resolved_greptile_keys": [],
        },
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
    )

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"
    assert greploop_guard.read_guard_state(66)["waiting_greptile_count"] == 0


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

    now = {"value": 1_000.0}
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_POLL_SECONDS", 10)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: now["value"])
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)

    first = await greploop_guard.run_guard_once(66)
    assert first["ok"] is True
    assert first["state"] == "WAITING_GREPTILE"

    now["value"] = 1_061.0
    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is False
    assert out["state"] == "BLOCKED_GREPTILE_STUCK"
    assert out["wait"]["elapsed_seconds"] == 61
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "BLOCKED_GREPTILE_STUCK"
    assert state["no_progress_count"] == 0


@pytest.mark.asyncio
async def test_merge_pr_when_ready_merges_when_assessment_passes(tmp_path, monkeypatch):
    async def fake_assess(_pr_number, **_kwargs):
        return {"ready": True, "blockers": [], "greptile": {}, "gh": {"ok": True}}

    async def fake_status(**_kwargs):
        return {"confidenceScore": 5, "reviewIsRunning": False, "headSha": "abc"}

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
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    out = await greploop_guard.merge_pr_when_ready(66)

    assert out["ok"] is True
    assert out["state"] == "MERGED"
    assert out["merge_commit"] == "deadbeef"
    assert ["pr", "merge", "66", "--squash"] in calls


@pytest.mark.asyncio
async def test_merge_pr_when_ready_returns_waiting_retry_for_running_greptile(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": True,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
            "codeReviews": [{"status": "REVIEWING_FILES"}],
        }

    async def fail_assess(*_args, **_kwargs):
        raise AssertionError("merge-ready fast path should not run full readiness while GitHub check is active")

    def fail_run_gh(*_args, **_kwargs):
        raise AssertionError("merge should not run while Greptile is active")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_POLL_SECONDS", 120)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fail_assess)
    monkeypatch.setattr(greploop_guard, "_run_gh", fail_run_gh)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "IN_PROGRESS", "conclusion": ""},
            ],
        },
    )

    out = await greploop_guard.merge_pr_when_ready(66)

    assert out["ok"] is False
    assert out["state"] == "WAITING_GREPTILE"
    assert out["blockers"] == ["GREPTILE_REVIEW_RUNNING"]
    assert out["retry_after_seconds"] == 120
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "WAITING_GREPTILE"
    assert state["merge_blockers"] == ["GREPTILE_REVIEW_RUNNING"]


def test_verify_pr_checkout_for_repair_accepts_matching_branch_and_head(monkeypatch):
    packet = _packet(pr=66, head_sha="head-sha")

    monkeypatch.setattr(greploop_guard, "_current_branch", lambda: "codex/example")
    monkeypatch.setattr(greploop_guard, "_local_head_sha", lambda: "head-sha")
    monkeypatch.setattr(greploop_guard, "_remote_tracking_branch", lambda: "origin/codex/example")
    monkeypatch.setattr(
        greploop_guard,
        "_pr_checkout_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "headRefName": "codex/example",
            "headRefOid": "head-sha",
        },
    )

    out = greploop_guard.verify_pr_checkout_for_repair(packet)

    assert out["ok"] is True
    assert out["branch"] == "codex/example"
    assert out["expected_head"] == "head-sha"


def test_verify_pr_checkout_for_repair_blocks_branch_mismatch(monkeypatch):
    packet = _packet(pr=66, head_sha="head-sha")

    monkeypatch.setattr(greploop_guard, "_current_branch", lambda: "codex/other")
    monkeypatch.setattr(greploop_guard, "_local_head_sha", lambda: "head-sha")
    monkeypatch.setattr(greploop_guard, "_remote_tracking_branch", lambda: "origin/codex/other")
    monkeypatch.setattr(
        greploop_guard,
        "_pr_checkout_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "headRefName": "codex/example",
            "headRefOid": "head-sha",
        },
    )

    out = greploop_guard.verify_pr_checkout_for_repair(packet)

    assert out["ok"] is False
    assert out["reason"] == "BRANCH_MISMATCH"
    assert out["branch"] == "codex/other"
    assert out["expected_branch"] == "codex/example"


@pytest.mark.parametrize(
    "case,branch,local_head,upstream,observation,packet_head,expected",
    [
        (
            "closed_pr",
            "codex/example",
            "head-sha",
            "origin/codex/example",
            {"ok": True, "state": "MERGED", "headRefName": "codex/example", "headRefOid": "head-sha"},
            "head-sha",
            "PR_NOT_OPEN",
        ),
        (
            "unsafe_main",
            "main",
            "head-sha",
            "origin/main",
            {"ok": True, "state": "OPEN", "headRefName": "codex/example", "headRefOid": "head-sha"},
            "head-sha",
            "UNSAFE_BRANCH",
        ),
        (
            "upstream_mismatch",
            "codex/example",
            "head-sha",
            "origin/codex/other",
            {"ok": True, "state": "OPEN", "headRefName": "codex/example", "headRefOid": "head-sha"},
            "head-sha",
            "UPSTREAM_MISMATCH",
        ),
        (
            "gh_lookup_failed",
            "codex/example",
            "head-sha",
            "origin/codex/example",
            {"ok": False, "reason": "GH_PR_VIEW_FAILED", "detail": "network"},
            "head-sha",
            "GH_PR_VIEW_FAILED",
        ),
        (
            "gh_incomplete",
            "codex/example",
            "head-sha",
            "origin/codex/example",
            {"ok": True, "state": "OPEN", "headRefName": "", "headRefOid": ""},
            "head-sha",
            "GH_PR_VIEW_INCOMPLETE",
        ),
        (
            "packet_head_mismatch",
            "codex/example",
            "head-sha",
            "origin/codex/example",
            {"ok": True, "state": "OPEN", "headRefName": "codex/example", "headRefOid": "head-sha"},
            "other-packet-sha",
            "PACKET_HEAD_MISMATCH",
        ),
    ],
)
def test_verify_pr_checkout_for_repair_blocks_failure_modes(
    monkeypatch,
    case,
    branch,
    local_head,
    upstream,
    observation,
    packet_head,
    expected,
):
    packet = _packet(pr=66, head_sha=packet_head)

    monkeypatch.setattr(greploop_guard, "_current_branch", lambda: branch)
    monkeypatch.setattr(greploop_guard, "_local_head_sha", lambda: local_head)
    monkeypatch.setattr(greploop_guard, "_remote_tracking_branch", lambda: upstream)
    monkeypatch.setattr(
        greploop_guard,
        "_pr_checkout_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: observation,
    )

    out = greploop_guard.verify_pr_checkout_for_repair(packet)

    assert out["ok"] is False, case
    assert out["reason"] == expected


def test_verify_pr_checkout_for_repair_blocks_head_mismatch(monkeypatch):
    packet = _packet(pr=66, head_sha="head-sha")

    monkeypatch.setattr(greploop_guard, "_current_branch", lambda: "codex/example")
    monkeypatch.setattr(greploop_guard, "_local_head_sha", lambda: "local-old-sha")
    monkeypatch.setattr(greploop_guard, "_remote_tracking_branch", lambda: "origin/codex/example")
    monkeypatch.setattr(
        greploop_guard,
        "_pr_checkout_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "headRefName": "codex/example",
            "headRefOid": "head-sha",
        },
    )

    out = greploop_guard.verify_pr_checkout_for_repair(packet)

    assert out["ok"] is False
    assert out["reason"] == "HEAD_MISMATCH"
    assert out["local_head"] == "local-old-sha"
    assert out["expected_head"] == "head-sha"


@pytest.mark.asyncio
async def test_cheap_runner_blocks_wrong_worktree_before_command(monkeypatch):
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_CMD", "python3 -c 'raise SystemExit(99)'")
    monkeypatch.setattr(
        greploop_guard,
        "verify_pr_checkout_for_repair",
        lambda packet: {"ok": False, "reason": "BRANCH_MISMATCH", "branch": "codex/other"},
    )

    status, output = await greploop_guard._run_cheap_agent(_packet())

    assert status == "BLOCKED_WRONG_WORKTREE"
    assert "BRANCH_MISMATCH" in output


@pytest.mark.asyncio
async def test_cheap_runner_command_does_not_expand_shell_metacharacters(monkeypatch):
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_CMD", "python3 -c 'import sys; sys.stdin.read(); print(\"$HOME\")'")
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD", "0")
    monkeypatch.setattr(greploop_guard, "verify_pr_checkout_for_repair", lambda packet: {"ok": True})

    status, output = await greploop_guard._run_cheap_agent(_packet())

    assert status == "OK"
    assert "$HOME" in output


def test_lock_release_leaves_released_lock_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "_HELD_LOCKS", set())
    lock_path = tmp_path / "pr-66" / "lock"

    with greploop_guard.acquire_lock(66):
        active = json.loads(lock_path.read_text(encoding="utf-8"))
        assert active["lock_id"]
        assert "released_at" not in active

    released = json.loads(lock_path.read_text(encoding="utf-8"))
    assert released["lock_id"] == active["lock_id"]
    assert released["released_at"] >= released["created_at"]


def test_lock_registration_cleans_up_when_metadata_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "_HELD_LOCKS", set())
    real_fsync = greploop_guard.os.fsync
    calls = {"count": 0}

    def flaky_fsync(fd):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(greploop_guard.os, "fsync", flaky_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        with greploop_guard.acquire_lock(66):
            pass

    assert 66 not in greploop_guard._HELD_LOCKS
    with greploop_guard.acquire_lock(66):
        assert 66 in greploop_guard._HELD_LOCKS


@pytest.mark.asyncio
async def test_assess_merge_readiness_blocks_actionable_findings(monkeypatch):
    async def fake_status(**_kwargs):
        return {"confidenceScore": 5, "reviewIsRunning": False, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "comment-1",
                    "file_path": "services/zoe-data/example.py",
                    "line": 12,
                    "body": "Fix this narrow bug",
                    "addressed": False,
                }
            ]
        }

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", lambda *args, **kwargs: {"ok": False})
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda *args, **kwargs: {"ok": False, "unresolved": -1, "resolved_greptile_keys": []},
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66, target_confidence=5)

    assert out["ready"] is False
    assert out["unaddressed_count"] == 1
    assert "GREPTILE_ACTIONABLE_FINDINGS:1" in out["blockers"]


# ---------------------------------------------------------------------------
# P2a — active-review cap counts authoritative GitHub state
# ---------------------------------------------------------------------------


def test_gh_open_prs_with_running_greptile_parses_rollup(monkeypatch):
    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        assert args[:2] == ["pr", "list"]
        return type(
            "P",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "number": 688,
                            "statusCheckRollup": [
                                {"name": "Greptile Review", "status": "IN_PROGRESS", "conclusion": ""}
                            ],
                        },
                        {
                            "number": 690,
                            "statusCheckRollup": [
                                {"name": "Greptile Review", "status": "QUEUED", "conclusion": ""}
                            ],
                        },
                        {
                            "number": 691,
                            "statusCheckRollup": [
                                {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"}
                            ],
                        },
                        {"number": 692, "statusCheckRollup": []},
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    out = _ORIGINAL_GH_OPEN_PRS_RUNNING()

    assert out == {688, 690}


def test_gh_open_prs_with_running_greptile_returns_none_on_failure(monkeypatch):
    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    assert _ORIGINAL_GH_OPEN_PRS_RUNNING() is None


@pytest.mark.asyncio
async def test_repo_capacity_counts_github_running_pr_without_local_state(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("GitHub-visible in-flight review must defer new triggers")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_LIMIT", 1)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard, "_gh_pr_observation", lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": False}
    )
    # PR 690 is reviewing on GitHub but has NO local guard state dir.
    monkeypatch.setattr(
        greploop_guard, "_gh_open_prs_with_running_greptile", lambda repo=greploop_guard.DEFAULT_REPO: {690}
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-gh-capacity",
    )

    assert out["triggered"] is False
    assert out["reason"] == "repo_greptile_review_capacity"
    assert out["active_prs"] == [690]
    assert out["github_active_prs"] == [690]
    assert out["local_waiting_prs"] == []
    assert out["active_review_count"] == 1


@pytest.mark.asyncio
async def test_repo_capacity_unions_github_and_local_waiting(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("union capacity should defer new triggers")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_LIMIT", 1)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_STALE_SECONDS", 600)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard, "_gh_pr_observation", lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": False}
    )
    monkeypatch.setattr(
        greploop_guard, "_gh_open_prs_with_running_greptile", lambda repo=greploop_guard.DEFAULT_REPO: {690}
    )
    greploop_guard._write_json(
        67,
        "status.json",
        {
            "pr": 67,
            "terminal_state": "WAITING_GREPTILE",
            "greptile_wait_started_at": 900.0,
            "greptile_wait_last_seen_at": 990.0,
            "greptile_next_poll_after": 1_100.0,
        },
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-union-capacity",
    )

    assert out["triggered"] is False
    assert out["reason"] == "repo_greptile_review_capacity"
    assert out["active_prs"] == [67, 690]
    assert out["github_active_prs"] == [690]
    assert out["local_waiting_prs"] == [67]
    assert out["active_review_count"] == 2


@pytest.mark.asyncio
async def test_repo_capacity_excludes_current_pr_running_on_github(tmp_path, monkeypatch):
    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"success": True, "triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_LIMIT", 1)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(
        greploop_guard, "_gh_pr_observation", lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": False}
    )
    # Only the current PR is "running" on GitHub; it must not count against itself.
    monkeypatch.setattr(
        greploop_guard, "_gh_open_prs_with_running_greptile", lambda repo=greploop_guard.DEFAULT_REPO: {66}
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-self-running",
    )

    assert out == {"success": True, "triggered": True}
    assert triggered["pr_number"] == 66


@pytest.mark.asyncio
async def test_repo_capacity_falls_back_to_local_when_github_unavailable(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("local-only capacity should still defer new triggers")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_LIMIT", 1)
    monkeypatch.setattr(greploop_guard, "GREPTILE_REPO_ACTIVE_REVIEW_STALE_SECONDS", 600)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard, "_gh_pr_observation", lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": False}
    )
    monkeypatch.setattr(
        greploop_guard, "_gh_open_prs_with_running_greptile", lambda repo=greploop_guard.DEFAULT_REPO: None
    )
    greploop_guard._write_json(
        67,
        "status.json",
        {
            "pr": 67,
            "terminal_state": "WAITING_GREPTILE",
            "greptile_wait_started_at": 900.0,
            "greptile_wait_last_seen_at": 990.0,
            "greptile_next_poll_after": 1_100.0,
        },
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-local-fallback",
    )

    assert out["triggered"] is False
    assert out["reason"] == "repo_greptile_review_capacity"
    assert out["active_prs"] == [67]
    assert out["github_active_prs"] == []
    assert out["local_waiting_prs"] == [67]


# ---------------------------------------------------------------------------
# P2b — stuck review escalates inside merge_pr_when_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_pr_when_ready_escalates_stuck_running_review(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": None,
            "reviewIsRunning": True,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
            "codeReviews": [{"status": "REVIEWING_FILES"}],
        }

    async def fail_assess(*_args, **_kwargs):
        raise AssertionError("stuck review must not run readiness assessment")

    def fail_run_gh(*_args, **_kwargs):
        raise AssertionError("stuck review must never merge")

    now = {"value": 1_000.0}
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(greploop_guard, "GREPTILE_WAIT_POLL_SECONDS", 10)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: now["value"])
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fail_assess)
    monkeypatch.setattr(greploop_guard, "_run_gh", fail_run_gh)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "Greptile Review", "status": "IN_PROGRESS", "conclusion": ""}
            ],
        },
    )

    first = await greploop_guard.merge_pr_when_ready(66)
    assert first["state"] == "WAITING_GREPTILE"

    now["value"] = 1_061.0
    out = await greploop_guard.merge_pr_when_ready(66)

    assert out["ok"] is False
    assert out["state"] == "BLOCKED_GREPTILE_STUCK"
    assert out["blockers"] == ["GREPTILE_REVIEW_STUCK"]
    assert out["wait"]["elapsed_seconds"] == 61
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "BLOCKED_GREPTILE_STUCK"
    assert state["merge_blockers"] == ["GREPTILE_REVIEW_STUCK"]


# ---------------------------------------------------------------------------
# P2c — clean check with no findings is mergeable below confidence target
# ---------------------------------------------------------------------------


def _clean_check_observation(pr, repo=greploop_guard.DEFAULT_REPO):
    return {
        "ok": True,
        "state": "OPEN",
        "statusCheckRollup": [
            {"name": "Greptile Review", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
    }


@pytest.mark.asyncio
async def test_run_guard_once_merges_clean_check_below_confidence(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    async def fail_trigger(**_kwargs):
        raise AssertionError("clean sub-confidence review must not retrigger")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", _clean_check_observation)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "READY_TO_MERGE"
    assert out["greptile"]["confidenceScore"] == 4
    state = greploop_guard.read_guard_state(66)
    assert state["terminal_state"] == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_run_guard_once_blocks_clean_check_below_confidence_when_flag_disabled(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "No Greptile review comments",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "CLEAN_MERGE_IGNORES_CONFIDENCE", False)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", _clean_check_observation)

    out = await greploop_guard.run_guard_once(66)

    assert out["ok"] is True
    assert out["state"] == "WAITING_GREPTILE"
    assert triggered["pr_number"] == 66
    assert greploop_guard.read_guard_state(66)["terminal_state"] == "WAITING_GREPTILE"


@pytest.mark.asyncio
async def test_run_guard_once_keeps_blocking_actionable_findings_below_confidence(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "0/1 Greptile comments addressed",
        }

    async def fake_comments(**_kwargs):
        return {
            "findings": [
                {
                    "id": "comment-1",
                    "file_path": "services/zoe-data/example.py",
                    "line": 12,
                    "body": "Fix this narrow bug",
                    "addressed": False,
                }
            ]
        }

    async def fail_trigger(**_kwargs):
        raise AssertionError("actionable findings must not just retrigger")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", _clean_check_observation)
    # An unresolved thread keeps the inline finding actionable.
    monkeypatch.setattr(
        greploop_guard,
        "_gh_thread_counts",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {
            "ok": True,
            "unresolved": 1,
            "resolved_greptile_keys": [],
        },
    )

    out = await greploop_guard.run_guard_once(66, packet_only=True)

    assert out["ok"] is True
    assert out["state"] == "PACKET_READY"
    assert out["state"] != "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_assess_merge_readiness_ready_on_clean_check_below_confidence(monkeypatch):
    async def fake_status(**_kwargs):
        return {"confidenceScore": 4, "reviewIsRunning": False, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", _clean_check_observation)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66, target_confidence=5)

    assert out["ready"] is True
    assert not any("GREPTILE_CONFIDENCE" in b for b in out["blockers"])


@pytest.mark.asyncio
async def test_assess_merge_readiness_blocks_clean_check_below_confidence_when_flag_disabled(monkeypatch):
    async def fake_status(**_kwargs):
        return {"confidenceScore": 4, "reviewIsRunning": False, "headSha": "abc"}

    async def fake_comments(**_kwargs):
        return {"findings": []}

    monkeypatch.setattr(greploop_guard, "CLEAN_MERGE_IGNORES_CONFIDENCE", False)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", _clean_check_observation)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_mergeable_state",
        lambda pr, repo=greploop_guard.DEFAULT_REPO, observation=None: {"ok": True},
    )

    out = await greploop_guard.assess_merge_readiness(66, target_confidence=5)

    assert out["ready"] is False
    assert any("GREPTILE_CONFIDENCE" in b for b in out["blockers"])


@pytest.mark.asyncio
async def test_run_guard_once_escalates_after_no_findings_retrigger_limit(tmp_path, monkeypatch):
    async def fake_status(**_kwargs):
        return {
            "confidenceScore": 4,
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "Summary-only review below confidence target",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    async def fake_trigger(**_kwargs):
        return {"triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "NO_FINDINGS_RETRIGGER_LIMIT", 2)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    # Check has NOT completed (empty rollup), so this is not a clean mergeable pass.
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "state": "OPEN", "statusCheckRollup": []},
    )

    first = await greploop_guard.run_guard_once(66)
    assert first["state"] == "WAITING_GREPTILE"
    second = await greploop_guard.run_guard_once(66)
    assert second["state"] == "WAITING_GREPTILE"
    third = await greploop_guard.run_guard_once(66)

    assert third["ok"] is False
    assert third["state"] == "ESCALATE_HERMES"
    assert third["reason"] == "greptile_no_progress_below_confidence"
    assert greploop_guard.read_guard_state(66)["terminal_state"] == "ESCALATE_HERMES"


async def test_no_findings_retrigger_bound_survives_confidence_fluctuation(tmp_path, monkeypatch):
    # Greptile review-thread fix: the re-trigger counter is anchored to the head
    # SHA only. A confidence score that ping-pongs between polls (same head) must
    # NOT reset the counter, otherwise NO_FINDINGS_RETRIGGER_LIMIT never bounds it.
    scores = iter([4, 3, 4, 3])

    async def fake_status(**_kwargs):
        return {
            "confidenceScore": next(scores, 3),
            "reviewIsRunning": False,
            "headSha": "abc",
            "reviewCompleteness": "Summary-only review below confidence target",
        }

    async def fake_comments(**_kwargs):
        return {"findings": []}

    async def fake_trigger(**_kwargs):
        return {"triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "NO_FINDINGS_RETRIGGER_LIMIT", 2)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 0)
    monkeypatch.setattr("greptile_client.get_pr_status", fake_status)
    monkeypatch.setattr("greptile_client.list_pr_comments", fake_comments)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "state": "OPEN", "statusCheckRollup": []},
    )

    first = await greploop_guard.run_guard_once(66)
    assert first["state"] == "WAITING_GREPTILE"
    second = await greploop_guard.run_guard_once(66)
    assert second["state"] == "WAITING_GREPTILE"
    # Confidence has fluctuated 4->3->4 across the three polls; the bound must
    # still fire on the third because the head SHA never moved.
    third = await greploop_guard.run_guard_once(66)
    assert third["state"] == "ESCALATE_HERMES"
    assert greploop_guard.read_guard_state(66)["terminal_state"] == "ESCALATE_HERMES"


# ---------------------------------------------------------------------------
# P1 — head-stability gating before (re)trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_head_stability_defers_newly_seen_head(tmp_path, monkeypatch):
    async def fail_trigger(**_kwargs):
        raise AssertionError("a freshly observed head must not be triggered immediately")

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 90)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fail_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "headRefOid": "abc", "statusCheckRollup": []},
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66},
        source="test-head-stability",
    )

    assert out["triggered"] is False
    assert out["reason"] == "head_not_stable"
    assert out["retry_after_seconds"] == 90
    saved = greploop_guard.read_guard_state(66)
    assert saved["head_seen_sha"] == "abc"
    assert saved["head_seen_at"] == 1_000.0


@pytest.mark.asyncio
async def test_head_stability_allows_head_stable_past_window(tmp_path, monkeypatch):
    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"success": True, "triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 90)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "headRefOid": "abc", "statusCheckRollup": []},
    )

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False, "confidenceScore": 0},
        state={"pr": 66, "head_seen_sha": "abc", "head_seen_at": 800.0},
        source="test-head-stable",
    )

    assert out == {"success": True, "triggered": True}
    assert triggered["pr_number"] == 66


@pytest.mark.asyncio
async def test_head_stability_force_bypasses_window(tmp_path, monkeypatch):
    triggered = {}

    async def fake_trigger(**kwargs):
        triggered.update(kwargs)
        return {"success": True, "triggered": True}

    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "HEAD_STABILITY_WINDOW_SECONDS", 90)
    monkeypatch.setattr(greploop_guard.time, "time", lambda: 1_000.0)
    monkeypatch.setattr("greptile_client.trigger_review", fake_trigger)

    out = await greploop_guard.trigger_review_safely(
        pr_number=66,
        status={"headSha": "abc", "reviewIsRunning": False},
        state={"pr": 66},
        force=True,
        source="test-head-force",
    )

    assert out == {"success": True, "triggered": True}
    assert triggered["pr_number"] == 66


def test_head_stability_skip_resets_clock_when_head_changes():
    state = {"head_seen_sha": "old", "head_seen_at": 100.0}

    skip = greploop_guard._head_stability_skip(state, head_sha="new", now=1_000.0)

    assert skip is not None
    assert skip["reason"] == "head_not_stable"
    assert state["head_seen_sha"] == "new"
    assert state["head_seen_at"] == 1_000.0


# ---------------------------------------------------------------------------
# P3 — finalize-on-merge sweep (only merged/closed; never mid-flight)
# ---------------------------------------------------------------------------


def test_finalize_merged_only_finalizes_merged_and_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    greploop_guard._write_json(
        66,
        "status.json",
        {
            "pr": 66,
            "terminal_state": "WAITING_GREPTILE",
            "greptile_wait_started_at": 1.0,
            "greptile_wait_last_seen_at": 2.0,
            "waiting_greptile_count": 3,
        },
    )
    greploop_guard._write_json(67, "status.json", {"pr": 67, "terminal_state": "WAITING_GREPTILE"})
    greploop_guard._write_json(68, "status.json", {"pr": 68, "terminal_state": "WAITING_GREPTILE"})
    states = {66: "MERGED", 67: "OPEN", 68: "CLOSED"}
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "state": states[int(pr)]},
    )

    result = greploop_guard.finalize_merged_guard_state()

    assert {item["pr"] for item in result["finalized"]} == {66, 68}
    merged = greploop_guard.read_guard_state(66)
    assert merged["terminal_state"] == "MERGED"
    assert merged["finalized"] is True
    assert merged["waiting_greptile_count"] == 0
    assert "greptile_wait_started_at" not in merged
    closed = greploop_guard.read_guard_state(68)
    assert closed["terminal_state"] == "CLOSED"
    assert closed["finalized"] is True
    # Mid-flight PR 67 must be left completely untouched.
    open_state = greploop_guard.read_guard_state(67)
    assert open_state["terminal_state"] == "WAITING_GREPTILE"
    assert "finalized" not in open_state
    assert {"pr": 67, "reason": "still_open", "state": "OPEN"} in result["skipped"]


def test_finalize_merged_skips_already_finalized(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    greploop_guard._write_json(
        66, "status.json", {"pr": 66, "terminal_state": "MERGED", "finalized": True}
    )

    def fail_observation(*_args, **_kwargs):
        raise AssertionError("already finalized PRs must not be re-observed")

    monkeypatch.setattr(greploop_guard, "_gh_pr_observation", fail_observation)

    result = greploop_guard.finalize_merged_guard_state()

    assert result["finalized"] == []
    assert {"pr": 66, "reason": "already_finalized"} in result["skipped"]


def test_finalize_merged_never_finalizes_open_pr_for_specific_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    greploop_guard._write_json(
        70,
        "status.json",
        {"pr": 70, "terminal_state": "WAITING_GREPTILE", "greptile_wait_last_seen_at": 5.0},
    )
    monkeypatch.setattr(
        greploop_guard,
        "_gh_pr_observation",
        lambda pr, repo=greploop_guard.DEFAULT_REPO: {"ok": True, "state": "OPEN"},
    )

    result = greploop_guard.finalize_merged_guard_state(pr_numbers=[70])

    assert result["finalized"] == []
    state = greploop_guard.read_guard_state(70)
    assert state["terminal_state"] == "WAITING_GREPTILE"
    assert "finalized" not in state
    assert state["greptile_wait_last_seen_at"] == 5.0


# --- Local serial merge queue ------------------------------------------------


def _mq_assessment(*, ready=False, behind=False, blockers=None):
    blk = list(blockers or [])
    gh = {}
    if behind:
        gh = {"ok": False, "reason": "GH_NOT_MERGEABLE", "mergeStateStatus": "BEHIND"}
        if "GH_NOT_MERGEABLE" not in blk:
            blk.append("GH_NOT_MERGEABLE")
    return {"ready": ready, "blockers": blk, "gh": gh}


@pytest.fixture
def _mq_enabled(monkeypatch, tmp_path):
    """Queue enabled with a guaranteed-absent kill file."""
    monkeypatch.setenv("ZOE_MERGE_QUEUE_ENABLED", "1")
    monkeypatch.setattr(greploop_guard, "MERGE_QUEUE_KILL_FILE", tmp_path / "absent.disabled")


def test_blocked_only_behind_logic():
    assert greploop_guard._blocked_only_behind(_mq_assessment(behind=True)) is True
    # behind but a Greptile thread also blocks -> rebase would not help
    assert (
        greploop_guard._blocked_only_behind(
            _mq_assessment(behind=True, blockers=["GREPTILE_UNRESOLVED_THREADS:1"])
        )
        is False
    )
    # not BEHIND (e.g. DIRTY/BLOCKED) -> not a rebase candidate
    assert (
        greploop_guard._blocked_only_behind({"gh": {"mergeStateStatus": "BLOCKED"}, "blockers": ["X"]})
        is False
    )
    # BEHIND but no blocker recorded -> nothing to act on
    assert (
        greploop_guard._blocked_only_behind({"gh": {"mergeStateStatus": "BEHIND"}, "blockers": []})
        is False
    )


async def test_queue_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_MERGE_QUEUE_ENABLED", raising=False)
    listed = {"n": 0}
    monkeypatch.setattr(
        greploop_guard,
        "_merge_queue_candidates",
        lambda **k: listed.__setitem__("n", listed["n"] + 1) or [],
    )
    out = await greploop_guard.run_merge_queue()
    assert out["action"] == "disabled"
    assert listed["n"] == 0  # never even lists candidates when disabled


async def test_queue_kill_file_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MERGE_QUEUE_ENABLED", "1")
    kill = tmp_path / "merge_queue.disabled"
    kill.write_text("stop")
    monkeypatch.setattr(greploop_guard, "MERGE_QUEUE_KILL_FILE", kill)
    out = await greploop_guard.run_merge_queue()
    assert out["action"] == "disabled"


async def test_queue_merges_ready_head_only(monkeypatch, _mq_enabled):
    monkeypatch.setattr(
        greploop_guard,
        "_merge_queue_candidates",
        lambda **k: [
            {"number": 101, "branch": "a", "created_at": "1"},
            {"number": 102, "branch": "b", "created_at": "2"},
        ],
    )
    merged, rebased = [], []

    async def fake_assess(pr, **k):
        return _mq_assessment(ready=(pr == 101))

    async def fake_merge(pr, **k):
        merged.append(pr)
        return {"ok": True, "state": "MERGED"}

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "merge_pr_when_ready", fake_merge)
    monkeypatch.setattr(
        greploop_guard, "_rebase_pr_branch", lambda *a, **k: rebased.append(a) or {"ok": True}
    )
    out = await greploop_guard.run_merge_queue()
    assert out["action"] == "merged" and out["pr"] == 101
    assert merged == [101]  # head only, one merge per cycle
    assert rebased == []


async def test_queue_rebases_behind_head(monkeypatch, _mq_enabled):
    monkeypatch.setattr(
        greploop_guard,
        "_merge_queue_candidates",
        lambda **k: [{"number": 201, "branch": "feat/x", "created_at": "1"}],
    )
    merged, rebased = [], []

    async def fake_assess(pr, **k):
        return _mq_assessment(behind=True)

    async def fake_merge(pr, **k):
        merged.append(pr)
        return {"ok": True}

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "merge_pr_when_ready", fake_merge)
    monkeypatch.setattr(
        greploop_guard,
        "_rebase_pr_branch",
        lambda pr, branch, **k: rebased.append((pr, branch)) or {"ok": True, "branch": branch},
    )
    out = await greploop_guard.run_merge_queue()
    assert out["action"] == "rebased" and out["pr"] == 201
    assert rebased == [(201, "feat/x")]
    assert merged == []  # a behind PR is never merged directly


async def test_queue_skips_blocked_then_acts_on_next(monkeypatch, _mq_enabled):
    monkeypatch.setattr(
        greploop_guard,
        "_merge_queue_candidates",
        lambda **k: [
            {"number": 301, "branch": "a", "created_at": "1"},
            {"number": 302, "branch": "b", "created_at": "2"},
        ],
    )
    merged = []

    async def fake_assess(pr, **k):
        if pr == 301:
            return _mq_assessment(blockers=["GREPTILE_UNRESOLVED_THREADS:2"])
        return _mq_assessment(ready=True)

    async def fake_merge(pr, **k):
        merged.append(pr)
        return {"ok": True}

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "merge_pr_when_ready", fake_merge)
    monkeypatch.setattr(greploop_guard, "_rebase_pr_branch", lambda *a, **k: {"ok": True})
    out = await greploop_guard.run_merge_queue()
    assert out["action"] == "merged" and out["pr"] == 302
    assert merged == [302]
    assert any(s["pr"] == 301 for s in out["skipped"])


async def test_queue_dry_run_no_mutation(monkeypatch):
    monkeypatch.delenv("ZOE_MERGE_QUEUE_ENABLED", raising=False)  # dry-run ignores the gate
    monkeypatch.setattr(
        greploop_guard,
        "_merge_queue_candidates",
        lambda **k: [{"number": 401, "branch": "a", "created_at": "1"}],
    )
    calls = {"merge": 0, "rebase": 0}

    async def fake_assess(pr, **k):
        return _mq_assessment(ready=True)

    async def fake_merge(pr, **k):
        calls["merge"] += 1
        return {"ok": True}

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "merge_pr_when_ready", fake_merge)
    monkeypatch.setattr(
        greploop_guard,
        "_rebase_pr_branch",
        lambda *a, **k: calls.__setitem__("rebase", calls["rebase"] + 1) or {"ok": True},
    )
    out = await greploop_guard.run_merge_queue(dry_run=True)
    assert out["action"] == "would_merge" and out["pr"] == 401
    assert calls == {"merge": 0, "rebase": 0}  # no mutation in dry-run


async def test_queue_dry_run_would_rebase(monkeypatch):
    monkeypatch.delenv("ZOE_MERGE_QUEUE_ENABLED", raising=False)
    monkeypatch.setattr(
        greploop_guard,
        "_merge_queue_candidates",
        lambda **k: [{"number": 402, "branch": "feat/y", "created_at": "1"}],
    )
    calls = {"merge": 0, "rebase": 0}

    async def fake_assess(pr, **k):
        return _mq_assessment(behind=True)

    async def fake_merge(pr, **k):
        calls["merge"] += 1
        return {"ok": True}

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    monkeypatch.setattr(greploop_guard, "merge_pr_when_ready", fake_merge)
    monkeypatch.setattr(
        greploop_guard,
        "_rebase_pr_branch",
        lambda *a, **k: calls.__setitem__("rebase", calls["rebase"] + 1) or {"ok": True},
    )
    out = await greploop_guard.run_merge_queue(dry_run=True)
    assert out["action"] == "would_rebase" and out["pr"] == 402 and out["branch"] == "feat/y"
    assert calls == {"merge": 0, "rebase": 0}


async def test_queue_idle_when_no_candidates(monkeypatch, _mq_enabled):
    monkeypatch.setattr(greploop_guard, "_merge_queue_candidates", lambda **k: [])
    out = await greploop_guard.run_merge_queue()
    assert out["action"] == "idle" and out["checked"] == 0


def test_merge_command_never_admin_or_force():
    # Guardrail against a future edit sneaking in an admin/force merge.
    import inspect

    src = inspect.getsource(greploop_guard.merge_pr_when_ready)
    assert '"--squash"' in src
    assert "--admin" not in src
    assert "--force" not in src


def test_cli_dry_run_requires_queue():
    # --dry-run reads as a safety net, so it must HARD-ERROR without --queue
    # (otherwise `--dry-run --merge-when-ready` would do a real merge). The guard
    # fires before any network/gh, so this subprocess is fast and offline-safe.
    import subprocess
    import sys

    cli = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "greploop_guard.py"
    proc = subprocess.run(
        [sys.executable, str(cli), "--dry-run", "--merge-when-ready", "--pr", "1"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "--dry-run only applies to --queue" in (proc.stderr + proc.stdout)


def test_candidates_none_on_gh_failure(monkeypatch):
    # A broken gh must NOT look like an empty queue: return None, not [].
    def fail_gh(args, **k):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="bad token")

    monkeypatch.setattr(greploop_guard, "_run_gh", fail_gh)
    assert greploop_guard._merge_queue_candidates() is None

    def bad_json_gh(args, **k):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr(greploop_guard, "_run_gh", bad_json_gh)
    assert greploop_guard._merge_queue_candidates() is None

    def ok_gh(args, **k):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(greploop_guard, "_run_gh", ok_gh)
    assert greploop_guard._merge_queue_candidates() == []  # genuinely empty, not failure


async def test_queue_reports_list_failure(monkeypatch, _mq_enabled):
    # gh failure surfaces as a non-ok result so a scheduler sees the error.
    monkeypatch.setattr(greploop_guard, "_merge_queue_candidates", lambda **k: None)
    out = await greploop_guard.run_merge_queue()
    assert out["ok"] is False and out["action"] == "list_failed"


# --- Autonomous close: update-branch on strict-mode BEHIND -------------------

def test_only_behind_blocks_true_when_behind_is_sole_blocker():
    assessment = {
        "ready": False,
        "blockers": ["GH_NOT_MERGEABLE"],
        "gh": {"ok": False, "reason": "GH_NOT_MERGEABLE", "mergeStateStatus": "BEHIND"},
    }
    assert greploop_guard._only_behind_blocks(assessment) is True


def test_only_behind_blocks_false_when_greptile_work_open():
    # SAFETY: never update-branch (which thrashes a re-review) while a Greptile
    # blocker is still open.
    assessment = {
        "ready": False,
        "blockers": ["GREPTILE_UNRESOLVED_THREADS:2", "GH_NOT_MERGEABLE"],
        "gh": {"mergeStateStatus": "BEHIND"},
    }
    assert greploop_guard._only_behind_blocks(assessment) is False


def test_only_behind_blocks_false_when_dirty_not_behind():
    # DIRTY is a real conflict — never auto-update.
    assessment = {
        "ready": False,
        "blockers": ["GH_NOT_MERGEABLE"],
        "gh": {"mergeStateStatus": "DIRTY"},
    }
    assert greploop_guard._only_behind_blocks(assessment) is False


def _behind_assessment():
    return {
        "ready": False,
        "blockers": ["GH_NOT_MERGEABLE"],
        "greptile": {},
        "gh": {"ok": False, "reason": "GH_NOT_MERGEABLE", "mergeStateStatus": "BEHIND"},
    }


async def _behind_status(**_kwargs):
    return {"confidenceScore": 5, "reviewIsRunning": False, "headSha": "abc"}


@pytest.mark.asyncio
async def test_merge_auto_updates_branch_when_only_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_UPDATE_BRANCH", True)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)

    async def fake_assess(_pr, **_k):
        return _behind_assessment()

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)

    calls: list[list[str]] = []

    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        calls.append(list(args))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)

    out = await greploop_guard.merge_pr_when_ready(66)
    assert out["state"] == "UPDATING_BRANCH"
    assert ["pr", "update-branch", "66"] in calls
    # It must NOT have attempted a merge.
    assert not any(c[:2] == ["pr", "merge"] for c in calls)


@pytest.mark.asyncio
async def test_merge_does_not_update_branch_when_flag_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_UPDATE_BRANCH", False)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)

    async def fake_assess(_pr, **_k):
        return _behind_assessment()

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    calls: list[list[str]] = []

    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        calls.append(list(args))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)
    out = await greploop_guard.merge_pr_when_ready(66)
    assert out["state"] == "BLOCKED_NOT_READY"
    assert not any(c[:2] == ["pr", "update-branch"] for c in calls)


@pytest.mark.asyncio
async def test_merge_never_updates_branch_while_greptile_open(tmp_path, monkeypatch):
    # The safety property, exercised through the real merge path.
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_UPDATE_BRANCH", True)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)

    async def fake_assess(_pr, **_k):
        return {
            "ready": False,
            "blockers": ["GREPTILE_UNRESOLVED_THREADS:1", "GH_NOT_MERGEABLE"],
            "greptile": {},
            "gh": {"mergeStateStatus": "BEHIND"},
        }

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)
    calls: list[list[str]] = []

    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        calls.append(list(args))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)
    out = await greploop_guard.merge_pr_when_ready(66)
    assert out["state"] == "BLOCKED_NOT_READY"
    assert not any(c[:2] == ["pr", "update-branch"] for c in calls)


@pytest.mark.asyncio
async def test_update_branch_failure_state_is_consistent(tmp_path, monkeypatch):
    # When update-branch fails, the returned state and the persisted
    # terminal_state must agree, and merge_blockers must be populated.
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_UPDATE_BRANCH", True)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)

    async def fake_assess(_pr, **_k):
        return _behind_assessment()

    monkeypatch.setattr(greploop_guard, "assess_merge_readiness", fake_assess)

    def fake_run_gh(args, *, repo=greploop_guard.DEFAULT_REPO, check=False):
        if args[:2] == ["pr", "update-branch"]:
            return type("P", (), {"returncode": 1, "stdout": "", "stderr": "merge conflict"})()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(greploop_guard, "_run_gh", fake_run_gh)
    out = await greploop_guard.merge_pr_when_ready(66)
    assert out["state"] == "BLOCKED_MERGE_FAILED"
    persisted = greploop_guard._load_status(66)
    assert persisted.get("terminal_state") == "BLOCKED_MERGE_FAILED"
    assert persisted.get("merge_blockers") == ["GH_NOT_MERGEABLE"]


# --- Autonomous close (2/2): resolve Greptile threads -----------------------

def _greptile_thread(tid, resolved=False):
    return {"id": tid, "isResolved": resolved,
            "comments": {"nodes": [{"author": {"login": "greptile-apps[bot]"}}]}}


def _human_thread(tid, resolved=False):
    return {"id": tid, "isResolved": resolved,
            "comments": {"nodes": [{"author": {"login": "jason-easyazz"}}]}}


def test_thread_is_greptile_detects_author():
    assert greploop_guard._thread_is_greptile(_greptile_thread("t1")) is True
    assert greploop_guard._thread_is_greptile(_human_thread("t2")) is False


def test_only_thread_resolution_pending():
    assert greploop_guard._only_thread_resolution_pending(
        {"blockers": ["GREPTILE_UNRESOLVED_THREADS:2", "GH_NOT_MERGEABLE"]}) is True
    # substantive greptile blocker present -> not just bookkeeping
    assert greploop_guard._only_thread_resolution_pending(
        {"blockers": ["GREPTILE_UNRESOLVED_THREADS:2", "GREPTILE_CONFIDENCE:3<5"]}) is False
    assert greploop_guard._only_thread_resolution_pending(
        {"blockers": ["GREPTILE_ACTIONABLE_FINDINGS:1"]}) is False
    assert greploop_guard._only_thread_resolution_pending(
        {"blockers": ["GH_NOT_MERGEABLE"]}) is False  # no thread blocker


def test_resolve_greptile_threads_resolves_all_greptile(monkeypatch):
    threads = [_greptile_thread("a"), _greptile_thread("b", resolved=True), _greptile_thread("c")]
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads", lambda *a, **k: threads)
    resolved = []
    monkeypatch.setattr(greploop_guard, "_gh_resolve_thread", lambda tid: resolved.append(tid) or True)
    out = greploop_guard._resolve_greptile_threads(66)
    assert out["ok"] is True
    assert set(resolved) == {"a", "c"}  # only the unresolved ones


def test_resolve_greptile_threads_refuses_when_human_thread_open(monkeypatch):
    # THE safety property: never auto-resolve anything while a human review
    # thread is unresolved.
    threads = [_greptile_thread("a"), _human_thread("h")]
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads", lambda *a, **k: threads)
    resolve_calls = []
    monkeypatch.setattr(greploop_guard, "_gh_resolve_thread", lambda tid: resolve_calls.append(tid) or True)
    out = greploop_guard._resolve_greptile_threads(66)
    assert out["ok"] is False and out["reason"] == "HUMAN_THREADS_OPEN"
    assert resolve_calls == [], "must resolve NOTHING when a human thread is open"


def _threads_pending_assessment():
    return {
        "ready": False,
        "blockers": ["GREPTILE_UNRESOLVED_THREADS:1", "GH_NOT_MERGEABLE"],
        "greptile": {},
        "gh": {"ok": False, "mergeStateStatus": "BLOCKED"},
    }


@pytest.mark.asyncio
async def test_merge_auto_resolves_then_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_RESOLVE_GREPTILE_THREADS", True)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness",
                        lambda _p, **_k: _async_return(_threads_pending_assessment()))
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads",
                        lambda *a, **k: [_greptile_thread("a")])
    resolved = []
    monkeypatch.setattr(greploop_guard, "_gh_resolve_thread", lambda tid: resolved.append(tid) or True)
    merged = []
    monkeypatch.setattr(greploop_guard, "_run_gh",
                        lambda args, **k: merged.append(args) or type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    out = await greploop_guard.merge_pr_when_ready(66)
    assert out["state"] == "RESOLVING_THREADS"
    assert resolved == ["a"]
    assert not any(a[:2] == ["pr", "merge"] for a in merged), "must not merge in the resolve cycle"


@pytest.mark.asyncio
async def test_merge_never_resolves_while_human_thread_open(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_RESOLVE_GREPTILE_THREADS", True)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness",
                        lambda _p, **_k: _async_return(_threads_pending_assessment()))
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads",
                        lambda *a, **k: [_greptile_thread("a"), _human_thread("h")])
    resolve_calls = []
    monkeypatch.setattr(greploop_guard, "_gh_resolve_thread", lambda tid: resolve_calls.append(tid) or True)
    monkeypatch.setattr(greploop_guard, "_run_gh",
                        lambda args, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    out = await greploop_guard.merge_pr_when_ready(66)
    assert resolve_calls == [], "human thread open -> resolve nothing"
    assert out["state"] == "BLOCKED_NOT_READY"


@pytest.mark.asyncio
async def test_merge_does_not_resolve_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(greploop_guard, "AUTO_RESOLVE_GREPTILE_THREADS", False)
    monkeypatch.setattr("greptile_client.get_pr_status", _behind_status)
    monkeypatch.setattr(greploop_guard, "assess_merge_readiness",
                        lambda _p, **_k: _async_return(_threads_pending_assessment()))
    calls = []
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads", lambda *a, **k: calls.append("read") or [])
    out = await greploop_guard.merge_pr_when_ready(66)
    assert out["state"] == "BLOCKED_NOT_READY"
    assert calls == [], "flag off -> never even reads threads to resolve"


def _async_return(value):
    async def _coro(*_a, **_k):
        return value
    return _coro()


def _mixed_thread(tid):
    # human OPENED it; Greptile replied later — must count as a HUMAN thread.
    return {"id": tid, "isResolved": False, "comments": {"nodes": [
        {"author": {"login": "jason-easyazz"}},
        {"author": {"login": "greptile-apps[bot]"}},
    ]}}


def test_thread_classified_by_opener_not_any_commenter():
    assert greploop_guard._thread_is_greptile(_mixed_thread("m")) is False
    assert greploop_guard._thread_is_greptile(_greptile_thread("g")) is True


def test_resolve_refuses_when_human_opened_thread_has_greptile_reply(monkeypatch):
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads",
                        lambda *a, **k: [_greptile_thread("g"), _mixed_thread("m")])
    calls = []
    monkeypatch.setattr(greploop_guard, "_gh_resolve_thread", lambda tid: calls.append(tid) or True)
    out = greploop_guard._resolve_greptile_threads(66)
    assert out["ok"] is False and out["reason"] == "HUMAN_THREADS_OPEN"
    assert calls == [], "must resolve nothing when a human-opened thread is unresolved"


def test_resolve_handles_thread_without_id(monkeypatch):
    no_id = {"isResolved": False, "comments": {"nodes": [{"author": {"login": "greptile"}}]}}
    monkeypatch.setattr(greploop_guard, "_gh_pr_review_threads",
                        lambda *a, **k: [_greptile_thread("g"), no_id])
    monkeypatch.setattr(greploop_guard, "_gh_resolve_thread", lambda tid: True)
    out = greploop_guard._resolve_greptile_threads(66)
    assert out["resolved"] == ["g"]
    assert out["missing_id"] == 1 and out["ok"] is False
    assert "None" not in out["failed"], "a missing id must not poison failed with 'None'"
