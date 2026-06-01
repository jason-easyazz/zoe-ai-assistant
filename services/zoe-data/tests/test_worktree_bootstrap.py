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


def test_ensure_worktree_is_idempotent(git_repo):
    first = wb.ensure_worktree("t_dup")
    second = wb.ensure_worktree("t_dup")
    assert first == second
    assert first.exists()
