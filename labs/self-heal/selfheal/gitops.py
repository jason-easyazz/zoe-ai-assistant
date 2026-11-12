"""The few git operations the loop needs. Every call is logged to CMD_LOG so tests can prove
what ran (e.g. no fetch / push / gh on the local-landing path).

Why not services/zoe-data/worktree_bootstrap.ensure_worktree: it always `git fetch`es and, when
that fails, silently falls back to the LOCAL base branch. A GitHub heal must start from a fresh
origin/main or not at all, and a local (no-GitHub) heal must make no network call at all.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .safety import DiffReport

CMD_LOG: list = []


class GitError(RuntimeError):
    pass


def run(argv: list, *, cwd: str | None = None, timeout: int = 120, check: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    CMD_LOG.append(list(argv))
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise GitError(f"{' '.join(argv[:4])}... rc={r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def git(repo: str, *args, **kw) -> str:
    return run(["git", "-C", repo, *args], **kw).stdout.strip()


def rev(repo: str, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def add_worktree(repo: str, path: str, *, base: str, branch: str | None = None) -> None:
    if Path(path).exists():
        raise GitError(f"worktree path already exists: {path}")
    if branch:
        git(repo, "worktree", "add", "-q", "-b", branch, path, base)
    else:
        git(repo, "worktree", "add", "-q", "--detach", path, base)


def remove_worktree(repo: str, path: str, *, branch: str | None = None) -> None:
    if Path(path).exists():
        git(repo, "worktree", "remove", "--force", path, check=False)
    git(repo, "worktree", "prune", check=False)
    if branch:
        git(repo, "branch", "-D", branch, check=False)


def checked_out_branches(repo: str) -> set:
    out = git(repo, "worktree", "list", "--porcelain")
    return {line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("branch ")}


def diff_report(wt: str, base_sha: str) -> DiffReport:
    """Stage everything (incl. untracked) and measure the diff against the base commit."""
    git(wt, "add", "-A")
    rep = DiffReport()
    for line in git(wt, "diff", "--cached", "--no-renames", "--numstat", base_sha).splitlines():
        a, d, path = line.split("\t", 2)
        rep.files.append(path)
        # binary files show "-": count them as over any cap
        rep.added += int(a) if a != "-" else 10**6
        rep.deleted += int(d) if d != "-" else 0
    root = Path(wt).resolve()
    for line in git(wt, "diff", "--cached", "--no-renames", "--raw", base_sha).splitlines():
        meta, path = line.split("\t", 1)
        if meta.split()[1] == "120000":   # new mode = symlink
            target = (Path(wt) / path).resolve()
            if target != root and root not in target.parents:
                rep.escaping_symlinks.append(path)
    return rep


def commit(wt: str, message: str) -> str:
    git(wt, "commit", "-q", "--no-verify", "-m", message,
        env={"GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "zoe-self-heal"),
             "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "zoe-self-heal"),
             "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "self-heal@zoe.local"),
             "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "self-heal@zoe.local")})
    return rev(wt, "HEAD")


@dataclass
class ReproResult:
    ok: bool
    detail: str


def run_repro(cfg, repo: str, ref: str, ticket, path: str, lock: list) -> ReproResult:
    """The fault's reproduction at `ref`, with the reproducing files restored to how they were
    when the fault was detected, so a 'fix' that weakened the test does not count as healed."""
    add_worktree(repo, path, base=ref)
    try:
        if ticket.repro_files:
            git(path, "checkout", ticket.base_sha, "--", *ticket.repro_files)
        try:
            r = subprocess.run([*lock, *ticket.repro_cmd], cwd=path, capture_output=True, text=True,
                               timeout=cfg.gate_timeout_s)
        except subprocess.TimeoutExpired:
            return ReproResult(False, "reproduction timed out")
        tail = (r.stdout.strip() or r.stderr.strip()).splitlines()
        return ReproResult(r.returncode == 0, f"rc={r.returncode} {(tail[-1] if tail else '')[:200]}")
    finally:
        remove_worktree(repo, path)
