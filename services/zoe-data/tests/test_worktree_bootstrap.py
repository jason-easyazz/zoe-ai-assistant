"""Tests for worktree_bootstrap."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import worktree_bootstrap as wb


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
