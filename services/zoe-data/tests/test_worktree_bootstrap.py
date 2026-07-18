"""Tests for worktree_bootstrap."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import worktree_bootstrap as wb

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("ZOE_REPO_ROOT", str(repo))
    monkeypatch.setenv("ZOE_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    return repo


def test_ensure_worktree_creates_branch_and_path(git_repo, monkeypatch):
    wt = wb.ensure_worktree("t_test123")
    assert wt.exists()
    assert (wt / ".git").exists()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch == "wt/t_test123"


def test_ensure_worktree_uses_origin_main_when_local_main_is_stale(git_repo, tmp_path):
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=git_repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=git_repo, check=True, capture_output=True)

    (git_repo / "REMOTE_ONLY.md").write_text("remote tip\n", encoding="utf-8")
    subprocess.run(["git", "add", "REMOTE_ONLY.md"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "remote tip"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=git_repo, check=True, capture_output=True)

    wt = wb.ensure_worktree("t_remote")
    assert (wt / "REMOTE_ONLY.md").read_text(encoding="utf-8") == "remote tip\n"


def test_base_ref_warns_when_fetch_fails(git_repo, caplog):
    caplog.set_level("WARNING")

    ref = wb._base_ref(git_repo, "main")

    assert ref == "main"
    assert "fetch origin/main failed" in caplog.text


def test_ensure_worktree_is_idempotent(git_repo, monkeypatch):
    first = wb.ensure_worktree("t_dup")

    calls = []
    original_run_git = wb._run_git

    def spy_run_git(args, *, cwd, timeout):
        calls.append(args)
        return original_run_git(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(wb, "_run_git", spy_run_git)

    second = wb.ensure_worktree("t_dup")
    assert first == second
    assert first.exists()
    assert not any(args[:3] == ["git", "fetch", "origin"] for args in calls)


def test_ensure_worktree_rejects_dirty_existing_task_branch(git_repo):
    wt = wb.ensure_worktree("t_dirty")
    (wt / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        wb.ensure_worktree("t_dirty")


def test_ensure_worktree_rejects_wrong_existing_task_branch(git_repo):
    wt = wb.ensure_worktree("t_wrong_branch")
    subprocess.run(
        ["git", "checkout", "-b", "not-the-task-branch"],
        cwd=wt,
        check=True,
        capture_output=True,
    )

    with pytest.raises(RuntimeError, match="expected fresh task branch"):
        wb.ensure_worktree("t_wrong_branch")


def test_ensure_worktree_rejects_invalid_task_id():
    with pytest.raises(ValueError, match="invalid characters"):
        wb.ensure_worktree("../escape")


def test_extract_pr_number():
    assert wb._extract_pr_number("https://github.com/o/r/pull/213") == "213"
    assert wb._extract_pr_number("https://github.com/o/r/pull/213/files") == "213"
    with pytest.raises(ValueError, match="cannot extract"):
        wb._extract_pr_number("https://github.com/o/r/issues/213")


def test_prepare_existing_pr_revision_worktree_resets_to_pr_head(git_repo, tmp_path):
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=git_repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=git_repo, check=True, capture_output=True)

    (git_repo / "PR_ONLY.md").write_text("pr head\n", encoding="utf-8")
    subprocess.run(["git", "add", "PR_ONLY.md"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "pr head"], cwd=git_repo, check=True, capture_output=True)
    expected_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", "origin", "HEAD:refs/pull/213/head"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=git_repo, check=True, capture_output=True)

    wt = wb.prepare_existing_pr_revision_worktree(
        "t_pr_revision",
        "https://github.com/o/r/pull/213",
    )

    assert (wt / "PR_ONLY.md").read_text(encoding="utf-8") == "pr head\n"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == expected_head
    task_ref = subprocess.run(
        ["git", "rev-parse", "refs/wt-pr/t_pr_revision"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=False,
    )
    assert task_ref.returncode != 0

    (git_repo / "PR_ONLY.md").write_text("force-pushed pr head\n", encoding="utf-8")
    subprocess.run(["git", "add", "PR_ONLY.md"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--amend", "-m", "force-pushed pr head"], cwd=git_repo, check=True, capture_output=True)
    force_pushed_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", "--force", "origin", "HEAD:refs/pull/213/head"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )

    wt = wb.prepare_existing_pr_revision_worktree(
        "t_pr_revision",
        "https://github.com/o/r/pull/213",
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == force_pushed_head
    assert (wt / "PR_ONLY.md").read_text(encoding="utf-8") == "force-pushed pr head\n"


def test_ensure_worktree_raises_when_repo_not_git(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_REPO_ROOT", str(tmp_path / "not-git"))
    monkeypatch.setenv("ZOE_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    (tmp_path / "not-git").mkdir()
    with pytest.raises(RuntimeError, match="git worktree add failed"):
        wb.ensure_worktree("t_badrepo")


def test_pin_kanban_workspace_updates_task_row(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            workspace_kind TEXT,
            workspace_path TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO tasks (id, workspace_kind, workspace_path) VALUES (?, ?, ?)",
        ("t_pin", "worktree", None),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ZOE_KANBAN_DB_PATH", str(db))
    wt = tmp_path / "worktrees" / "t_pin"
    wt.mkdir(parents=True)

    pinned = wb.pin_kanban_workspace("t_pin", wt)
    assert pinned == wt.resolve()

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT workspace_path FROM tasks WHERE id = ?", ("t_pin",)
    ).fetchone()
    conn.close()
    assert row[0] == str(wt.resolve())


def test_kanban_db_path_default_board(monkeypatch):
    monkeypatch.delenv("ZOE_KANBAN_DB_PATH", raising=False)
    monkeypatch.delenv("ZOE_KANBAN_BOARD", raising=False)
    path = wb.kanban_db_path()
    assert path.name == "kanban.db"
    assert path.parent.name == ".hermes"


def _commit_on_worktree(wt: Path, name: str) -> None:
    (wt / name).write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=wt, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", name], cwd=wt, check=True, capture_output=True)


def test_remove_task_worktree_removes_merged_worktree(git_repo):
    wt = wb.ensure_worktree("t_merged")
    # Fresh wt/<id> off main with no new commits is an ancestor of main → merged.
    assert wb.remove_task_worktree("t_merged", consult_pr=False) is True
    assert not wt.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", "wt/t_merged"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "wt/t_merged" not in branches


def test_remove_task_worktree_keeps_unmerged_worktree(git_repo):
    wt = wb.ensure_worktree("t_ahead")
    _commit_on_worktree(wt, "feature.txt")  # now ahead of main → not merged
    assert wb.remove_task_worktree("t_ahead", consult_pr=False) is False
    assert wt.exists()


def test_remove_task_worktree_keeps_dirty_worktree(git_repo):
    wt = wb.ensure_worktree("t_dirty")
    (wt / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")  # dirty
    assert wb.remove_task_worktree("t_dirty", consult_pr=False) is False
    assert wt.exists()


def test_remove_task_worktree_noop_when_not_registered(git_repo):
    assert wb.remove_task_worktree("t_absent", consult_pr=False) is False


def test_remove_task_worktree_uses_pr_merge_for_squash(git_repo, monkeypatch):
    wt = wb.ensure_worktree("t_squash")
    _commit_on_worktree(wt, "squash.txt")  # ahead of main, not an ancestor
    # Simulate a squash-merged PR: not a git ancestor, but gh reports MERGED.
    monkeypatch.setattr(wb, "_pr_is_merged", lambda branch, *, cwd: True)
    assert wb.remove_task_worktree("t_squash", consult_pr=True) is True
    assert not wt.exists()
    # Without consulting the PR, the same branch must be kept.
    wt2 = wb.ensure_worktree("t_squash2")
    _commit_on_worktree(wt2, "squash2.txt")
    assert wb.remove_task_worktree("t_squash2", consult_pr=False) is False
    assert wt2.exists()


def test_prune_merged_worktrees_removes_merged_keeps_others(git_repo):
    merged = wb.ensure_worktree("t_sweep_merged")
    ahead = wb.ensure_worktree("t_sweep_ahead")
    _commit_on_worktree(ahead, "ahead.txt")
    dirty = wb.ensure_worktree("t_sweep_dirty")
    (dirty / "scratch.txt").write_text("x\n", encoding="utf-8")

    results = wb.prune_merged_worktrees(min_age_days=0, consult_pr=False)
    decisions = {r["worktree"]: r["decision"] for r in results}

    assert decisions[str(merged.resolve())] == "removed"
    assert not merged.exists()
    assert decisions[str(ahead.resolve())] == "skip:branch not merged"
    assert ahead.exists()
    assert decisions[str(dirty.resolve())] == "skip:dirty"
    assert dirty.exists()
    # Never touches the live checkout.
    assert decisions[str(git_repo.resolve())] == "skip:live checkout"


def test_prune_merged_worktrees_respects_min_age(git_repo):
    wt = wb.ensure_worktree("t_recent")
    # Recent commit (just now) with a 7-day idle floor → kept.
    results = wb.prune_merged_worktrees(min_age_days=7, consult_pr=False)
    decisions = {r["worktree"]: r["decision"] for r in results}
    assert decisions[str(wt.resolve())].startswith("skip:too recent")
    assert wt.exists()


def test_prune_classifies_detached_head_before_dirty(git_repo):
    wt = wb.ensure_worktree("t_detach")
    _commit_on_worktree(wt, "extra.txt")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", head], cwd=wt, check=True, capture_output=True)

    results = wb.prune_merged_worktrees(min_age_days=0, consult_pr=False)
    decisions = {r["worktree"]: r["decision"] for r in results}
    assert decisions[str(wt.resolve())] == "skip:detached HEAD"
    assert wt.exists()


def test_remove_task_worktree_skips_fetch_when_base_ref_passed(git_repo, monkeypatch):
    wb.ensure_worktree("t_nofetch")

    def _no_fetch(repo, base_branch):
        raise AssertionError("_base_ref should not be called when base_ref is provided")

    monkeypatch.setattr(wb, "_base_ref", _no_fetch)
    # Fresh wt branch off main is an ancestor of main → merged with the passed ref.
    assert wb.remove_task_worktree("t_nofetch", base_ref="main", consult_pr=False) is True


def test_prune_merged_worktrees_dry_run_reports_without_removing(git_repo):
    wt = wb.ensure_worktree("t_dryrun")
    results = wb.prune_merged_worktrees(min_age_days=0, execute=False, consult_pr=False)
    decisions = {r["worktree"]: r["decision"] for r in results}
    assert decisions[str(wt.resolve())] == "would-remove"
    assert wt.exists()


def test_pin_kanban_workspace_falls_back_without_worktree_kind(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            workspace_kind TEXT,
            workspace_path TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO tasks (id, workspace_kind, workspace_path) VALUES (?, ?, ?)",
        ("t_fallback", "git_worktree", None),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ZOE_KANBAN_DB_PATH", str(db))
    wt = tmp_path / "worktrees" / "t_fallback"
    wt.mkdir(parents=True)

    pinned = wb.pin_kanban_workspace("t_fallback", wt)
    assert pinned == wt.resolve()

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT workspace_path FROM tasks WHERE id = ?", ("t_fallback",)
    ).fetchone()
    conn.close()
    assert row[0] == str(wt.resolve())


# ── shared-branch guard ──────────────────────────────────────────────────────
# Removing a worktree whose branch ANOTHER worktree also holds deletes that
# branch out from under the other one — which may be a live agent session.
# Merged-ness says nothing about whether someone is still standing on a branch.
#
# Seen 2026-07-18: an agent worktree and the session driving it shared one merged
# branch; every other guard (live/locked/dirty/merged/age) passed, and the pruner
# offered to remove it. The manual shell tool and this in-process pruner mirror
# each other, so both need the guard (Greptile review, PR #1406).


def _force_second_checkout(repo, wt_path, branch):
    """Put `branch` in a SECOND worktree. git needs --force for a double-checkout;
    the real incident got there exactly this way."""
    subprocess.run(
        ["git", "worktree", "add", "--force", str(wt_path), branch],
        cwd=repo, check=True, capture_output=True,
    )


def test_remove_task_worktree_keeps_a_shared_branch(git_repo, tmp_path):
    """THE REGRESSION: a branch held by two worktrees is never removed."""
    wt = wb.ensure_worktree("t_shared01")
    branch = wb.worktree_branch("t_shared01")
    _force_second_checkout(git_repo, tmp_path / "second", branch)

    removed = wb.remove_task_worktree("t_shared01", require_merged=False, consult_pr=False)

    assert removed is False, "removed a worktree whose branch another worktree holds"
    assert wt.exists(), "worktree was deleted despite the shared branch"


def test_remove_task_worktree_still_removes_an_unshared_branch(git_repo):
    """The guard must not over-block — otherwise 'never remove' would pass the
    test above and silently turn the pruner into a no-op."""
    wt = wb.ensure_worktree("t_solo001")

    removed = wb.remove_task_worktree("t_solo001", require_merged=False, consult_pr=False)

    assert removed is True, "a lone worktree should still be removable"
    assert not wt.exists()


def test_branch_is_shared_fails_closed_when_git_fails(git_repo, monkeypatch):
    """An unverifiable state must not authorise a --force removal."""
    def _boom(*_a, **_k):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"
        return R()

    monkeypatch.setattr(wb, "_run_git", _boom)
    assert wb._branch_is_shared(git_repo, "wt/t_whatever") is True


def test_prune_merged_worktrees_skips_a_shared_branch(git_repo, tmp_path):
    """The scheduled sweep honours the same guard as the per-task path."""
    wb.ensure_worktree("t_shared02")
    branch = wb.worktree_branch("t_shared02")
    _force_second_checkout(git_repo, tmp_path / "second2", branch)

    results = wb.prune_merged_worktrees(min_age_days=0, execute=False, consult_pr=False)

    decisions = {r["worktree"]: r.get("decision", "") for r in results}
    shared = [d for d in decisions.values() if "another worktree" in d]
    assert shared, f"expected a shared-branch skip, got: {decisions}"
    assert not [d for d in decisions.values() if d in ("removed", "would-remove")], (
        f"offered to remove a worktree on a shared branch: {decisions}"
    )
