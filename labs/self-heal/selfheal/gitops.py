"""The few git operations the loop needs. Every call is logged to CMD_LOG so tests can prove
what ran (e.g. no fetch / push / gh on the local-landing path).

Why not services/zoe-data/worktree_bootstrap.ensure_worktree: it always `git fetch`es and, when
that fails, silently falls back to the LOCAL base branch. A GitHub heal must start from a fresh
origin/main or not at all, and a local (no-GitHub) heal must make no network call at all.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import inside_refused
from .safety import DiffReport, rc_summary

CMD_LOG: list = []
JUNK = re.compile(r"(^|/)(__pycache__|\.pytest_cache)(/|$)|\.py[co]$")


class GitError(RuntimeError):
    pass


def _guard(argv: list, cwd: str | None) -> None:
    """Hard refusal: no command runs IN the live checkout, and no git -C points into it."""
    paths = [cwd] if cwd else []
    if argv[:2] == ["git", "-C"] and len(argv) > 2:
        paths.append(argv[2])
    for p in paths:
        if inside_refused(p):
            raise GitError("refused: the live checkout")


def run(argv: list, *, cwd: str | None = None, timeout: int = 120, check: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    _guard(argv, cwd)
    CMD_LOG.append(list(argv))
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise GitError(f"{os.path.basename(argv[0])} {argv[3] if argv[:2] == ['git', '-C'] and len(argv) > 3 else ''} rc={r.returncode}")
    return r


def git(repo: str, *args, **kw) -> str:
    return run(["git", "-C", repo, *args], **kw).stdout.strip()


def rev(repo: str, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def add_worktree(repo: str, path: str, *, base: str, branch: str | None = None) -> None:
    if inside_refused(path) or inside_refused(os.path.dirname(os.path.abspath(path))):
        raise GitError("refused: a worktree inside the live checkout")
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


def inspect_worktree(wt: str, base_sha: str) -> DiffReport:
    """Measure the engine's changes WITHOUT staging anything: `git status` (untracked files
    included, renames off), line counts from the unstaged diff + the untracked files' contents,
    and a symlink check. The index is untouched until the policy passes."""
    rep = DiffReport()
    if rev(wt, "HEAD") != base_sha:
        rep.engine_committed = True
    # The runner alone stages, and only after the checks: ANY entry the engine put in the index
    # (even an allowed file) discards the attempt.
    if run(["git", "-C", wt, "diff", "--cached", "--quiet", "HEAD"], check=False).returncode != 0:
        rep.index_dirty = True
    out = run(["git", "-C", wt, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"]).stdout
    untracked = []
    for entry in filter(None, out.split("\0")):
        code, path = entry[:2], entry[3:]
        if JUNK.search(path):
            continue   # interpreter / test-runner caches: never measured, never staged
        rep.files.append(path)
        if code == "??":
            untracked.append(path)
    for line in git(wt, "diff", "--no-renames", "--numstat", base_sha).splitlines():
        a, d, _path = line.split("\t", 2)
        rep.added += int(a) if a != "-" else 10**6   # binary: over any cap
        rep.deleted += int(d) if d != "-" else 0
    root = Path(wt).resolve()
    for path in rep.files:
        full = Path(wt) / path
        if full.is_symlink():
            target = full.resolve()
            if target != root and root not in target.parents:
                rep.escaping_symlinks.append(path)
            continue
        if path in untracked and full.is_file():
            data = full.read_bytes()
            rep.added += 10**6 if b"\0" in data[:8192] else data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    rep.files.sort()
    return rep


def stage_checked(wt: str, files: list, base_sha: str) -> None:
    """Stage exactly the paths the policy checked (no blanket `add -A`), then prove the index
    holds nothing else."""
    git(wt, "add", "--", *files)
    staged = sorted(filter(None, git(wt, "diff", "--cached", "--no-renames", "--name-only", base_sha).splitlines()))
    if staged != sorted(files):
        raise GitError("refused: staged set differs from the checked set")


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
            return ReproResult(False, "timeout")
        return ReproResult(r.returncode == 0, rc_summary(r.returncode, r.stdout))
    finally:
        remove_worktree(repo, path)
