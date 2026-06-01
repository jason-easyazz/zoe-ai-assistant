"""Bootstrap git worktrees for Hermes Kanban implement/verify phases."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from hermes_http import zoe_repo_root

logger = logging.getLogger(__name__)

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _run_git(args: list[str], *, cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git command timed out after {timeout}s: {' '.join(args)}"
        ) from exc


def worktree_root() -> Path:
    override = os.environ.get("ZOE_WORKTREE_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".worktrees"


def worktree_path(task_id: str) -> Path:
    return worktree_root() / task_id


def worktree_branch(task_id: str) -> str:
    return f"wt/{task_id}"


def _validate_task_id(task_id: str) -> str:
    cleaned = task_id.strip()
    if not cleaned:
        raise ValueError("task_id is required")
    if not _TASK_ID_RE.fullmatch(cleaned):
        raise ValueError(f"task_id contains invalid characters: {task_id!r}")
    return cleaned


def _worktree_registered(repo: Path, wt_path: Path) -> bool:
    listed = _run_git(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo),
        timeout=30,
    )
    if listed.returncode != 0:
        return False
    target = str(wt_path.resolve())
    block_path = ""
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            block_path = line.removeprefix("worktree ").strip()
            continue
        if block_path == target and line.startswith("branch "):
            return True
    return False


def ensure_worktree(task_id: str, *, base_branch: str = "main") -> Path:
    """Create ``~/.worktrees/<task_id>`` on ``wt/<task_id>`` if missing.

    Hermes Kanban resolves worktree workspace paths at claim time but does not
    run ``git worktree add``. Workers used to self-bootstrap; Zoe pre-creates
    the tree at dispatch so implement does not block with WORKTREE_NOT_READY.
    """
    task_id = _validate_task_id(task_id)

    wt_path = worktree_path(task_id)
    branch = worktree_branch(task_id)
    repo = Path(zoe_repo_root())

    if _worktree_registered(repo, wt_path):
        return wt_path

    wt_path.parent.mkdir(parents=True, exist_ok=True)

    add_cmd = [
        "git",
        "worktree",
        "add",
        str(wt_path),
        "-b",
        branch,
        base_branch,
    ]
    result = _run_git(add_cmd, cwd=str(repo), timeout=120)
    if result.returncode == 0:
        logger.info("worktree_bootstrap: created %s on %s", wt_path, branch)
        return wt_path

    # Branch may already exist from a prior partial run — attach worktree to it.
    retry = _run_git(
        ["git", "worktree", "add", str(wt_path), branch],
        cwd=str(repo),
        timeout=120,
    )
    if retry.returncode == 0:
        logger.info("worktree_bootstrap: attached %s to existing %s", wt_path, branch)
        return wt_path

    stderr = (retry.stderr or result.stderr or "").strip()
    raise RuntimeError(f"git worktree add failed for {task_id}: {stderr or 'unknown error'}")
