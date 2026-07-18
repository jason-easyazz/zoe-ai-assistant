"""Pins the prune tool's shared-branch guard.

`prune_worktrees.sh` removes worktrees whose branch is merged into origin/main.
Merged-ness says nothing about whether someone is still STANDING on that branch:
git allows the same branch in two worktrees only via `--force`, and when that
happens, removing one worktree pulls the shared branch out from under the other —
which may be a live agent session.

Observed for real (2026-07-18): an agent worktree and the session driving it both
sat on `claude/memory-hardening-w0-7ff9c7`; the branch was merged, every other
guard passed, and the tool offered to remove it.

These tests build REAL git worktrees in tmp_path — no mocks — so they exercise the
same `git worktree list --porcelain` parsing the script actually depends on.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Slim-dep green: stdlib + git only (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "prune_worktrees.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repo with an `origin/main` and one merged feature branch."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "f.txt").write_text("1\n")
    _git(origin, "add", "f.txt")
    _git(origin, "commit", "-qm", "init")

    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(work)],
        capture_output=True, text=True, check=True,
    )
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    # A branch that IS merged into origin/main (it is main's tip) — so every
    # guard except the shared-branch one will pass.
    _git(work, "branch", "feature/merged")
    return work


def _run_prune(repo: Path, *, min_age_days: str = "0") -> str:
    """Dry-run the real script against `repo`; returns combined output."""
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo.parent),
            "ZOE_ASSISTANT_ROOT": str(repo),
            "ZOE_WORKTREE_MIN_AGE_DAYS": min_age_days,
        },
        cwd=str(repo),
    )
    return proc.stdout + proc.stderr


def test_shared_branch_worktree_is_never_a_candidate(repo: Path, tmp_path: Path):
    """THE REGRESSION: two worktrees on one merged branch ⇒ neither is removable.

    Fails on the unfixed script, which offers to remove one of them.
    """
    a = tmp_path / "wt_a"
    b = tmp_path / "wt_b"
    _git(repo, "worktree", "add", "-q", str(a), "feature/merged")
    # git refuses a second checkout of the same branch without --force; the real
    # incident got here exactly this way (agent worktree + session on one branch).
    _git(repo, "worktree", "add", "-q", "--force", str(b), "feature/merged")

    out = _run_prune(repo)

    assert "would remove" not in out, (
        "a branch checked out by TWO worktrees was offered for removal — "
        "removing one pulls the branch out from under the other:\n" + out
    )
    assert "checked out by another worktree" in out, (
        "expected the shared-branch skip reason:\n" + out
    )


def test_unshared_merged_worktree_is_still_prunable(repo: Path, tmp_path: Path):
    """The guard must not over-block: a lone merged worktree stays removable.

    Without this, 'skip everything' would pass the test above and quietly turn
    the tool into a no-op.
    """
    solo = tmp_path / "wt_solo"
    _git(repo, "worktree", "add", "-q", str(solo), "feature/merged")

    out = _run_prune(repo)

    assert "would remove" in out and str(solo) in out, (
        "a lone merged worktree should still be a prune candidate:\n" + out
    )
