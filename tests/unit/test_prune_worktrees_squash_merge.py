"""prune_worktrees.sh must recognise SQUASH-merged branches.

The repo is squash-only, so a merged PR's commits are never ancestors of
origin/main; the old ancestor-only check reported every merged worktree "not
merged" (2026-11-12: ~130 skipped that way). The script now also treats a branch
whose tip is the head commit of a MERGED PR as merged. The PR list is injected via
ZOE_WORKTREE_MERGED_OIDS so the test needs no network or gh auth.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "prune_worktrees.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "f.txt").write_text("1\n")
    _git(origin, "add", "f.txt")
    _git(origin, "commit", "-qm", "init")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    # A feature branch with a commit that is NOT in main (a squash merge would
    # have landed a different commit).
    _git(work, "checkout", "-q", "-b", "feature/squashed")
    (work / "g.txt").write_text("2\n")
    _git(work, "add", "g.txt")
    _git(work, "commit", "-qm", "feature")
    _git(work, "checkout", "-q", "main")
    return work


def _run(repo: Path, oids: Path | None) -> str:
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(repo.parent),
        "ZOE_ASSISTANT_ROOT": str(repo),
        "ZOE_WORKTREE_MIN_AGE_DAYS": "0",
    }
    if oids is not None:
        env["ZOE_WORKTREE_MERGED_OIDS"] = str(oids)
    p = subprocess.run(["bash", str(_SCRIPT)], capture_output=True, text=True, env=env, cwd=str(repo))
    return p.stdout + p.stderr


def test_squash_merged_branch_is_a_candidate(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt_sq"
    _git(repo, "worktree", "add", "-q", str(wt), "feature/squashed")
    oids = tmp_path / "oids.txt"
    oids.write_text(_git(repo, "rev-parse", "feature/squashed") + "\n")
    out = _run(repo, oids)
    assert "would remove" in out and str(wt) in out, out


def test_unmerged_branch_is_not_a_candidate(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt_un"
    _git(repo, "worktree", "add", "-q", str(wt), "feature/squashed")
    oids = tmp_path / "oids.txt"
    oids.write_text("0" * 40 + "\n")  # some other PR's head
    out = _run(repo, oids)
    assert "would remove" not in out, out
    assert "not merged" in out, out


def test_branch_ahead_of_merged_pr_head_is_kept(repo: Path, tmp_path: Path):
    """Commits added AFTER the PR head must not be swept up as 'merged'."""
    wt = tmp_path / "wt_ahead"
    _git(repo, "worktree", "add", "-q", str(wt), "feature/squashed")
    oids = tmp_path / "oids.txt"
    oids.write_text(_git(repo, "rev-parse", "feature/squashed") + "\n")
    (wt / "h.txt").write_text("3\n")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    _git(wt, "add", "h.txt")
    _git(wt, "commit", "-qm", "after the PR head")
    out = _run(repo, oids)
    assert "would remove" not in out, out
