"""Bootstrap git worktrees for Hermes Kanban implement/verify phases."""

from __future__ import annotations

import logging
import os
import re
import sqlite3  # operator-local Hermes Kanban DB (~/.hermes), not Zoe PostgreSQL
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


def kanban_db_path() -> Path:
    """Path to the Hermes Kanban SQLite DB for the active board."""
    override = os.environ.get("ZOE_KANBAN_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    board = os.environ.get("ZOE_KANBAN_BOARD", "default").strip() or "default"
    home = Path.home() / ".hermes"
    if board == "default":
        return home / "kanban.db"
    return home / "kanban" / "boards" / board / "kanban.db"


def pin_kanban_workspace(task_id: str, wt_path: Path | None = None) -> Path:
    """Persist the absolute worktree path on a Kanban task before claim.

    Hermes defaults unset worktree paths to ``<dispatcher-cwd>/.worktrees/<id>``.
    Zoe bootstrap uses ``~/.worktrees/<id>`` (or ``ZOE_WORKTREE_ROOT``). Pin
    the bootstrap path on the task row so workers and ``ensure_worktree`` agree.

    Writes operator-local ``~/.hermes/kanban.db`` (Hermes Kanban), not Zoe's
    PostgreSQL store.
    """
    task_id = _validate_task_id(task_id)
    path = (wt_path or worktree_path(task_id)).resolve()
    abs_path = str(path)
    db = kanban_db_path()
    if not db.exists():
        raise RuntimeError(f"kanban db not found: {db}")

    # Hermes ``hermes kanban create --workspace worktree`` sets workspace_kind to
    # ``worktree``; fall back to id-only update if the row uses another value.
    with sqlite3.connect(str(db)) as conn:
        cur = conn.execute(
            "UPDATE tasks SET workspace_path = ? WHERE id = ? AND workspace_kind = 'worktree'",
            (abs_path, task_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            logger.warning(
                "worktree_bootstrap: no row for %s with workspace_kind='worktree' in %s; "
                "retrying id-only workspace_path update",
                task_id,
                db,
            )
            cur = conn.execute(
                "UPDATE tasks SET workspace_path = ? WHERE id = ?",
                (abs_path, task_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise RuntimeError(
                    f"kanban task {task_id!r} not found in {db}"
                )

    logger.info("worktree_bootstrap: pinned kanban workspace %s -> %s", task_id, abs_path)
    return path


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


def _base_ref(repo: Path, base_branch: str) -> str:
    """Prefer the remote base branch so task worktrees do not start from stale local main."""
    fetch_result = _run_git(["git", "fetch", "origin", base_branch], cwd=str(repo), timeout=120)
    if fetch_result.returncode != 0:
        logger.warning(
            "worktree_bootstrap: fetch origin/%s failed (rc=%d); "
            "falling back to local tracking ref or branch: %s",
            base_branch,
            fetch_result.returncode,
            (fetch_result.stderr or "").strip(),
        )
    remote_ref = f"origin/{base_branch}"
    check = _run_git(["git", "rev-parse", "--verify", remote_ref], cwd=str(repo), timeout=30)
    return remote_ref if check.returncode == 0 else base_branch


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

    base_ref = _base_ref(repo, base_branch)

    wt_path.parent.mkdir(parents=True, exist_ok=True)

    add_cmd = [
        "git",
        "worktree",
        "add",
        str(wt_path),
        "-b",
        branch,
        base_ref,
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


def prepare_kanban_worktree(task_id: str, *, base_branch: str = "main") -> Path:
    """Create the git worktree and pin its path on the Kanban task row."""
    wt_path = ensure_worktree(task_id, base_branch=base_branch)
    try:
        pin_kanban_workspace(task_id, wt_path)
    except RuntimeError as exc:
        # Pin is best-effort: dispatch must not orphan an already-created chain.
        logger.warning(
            "worktree_bootstrap: could not pin kanban workspace for %s: %s",
            task_id,
            exc,
        )
    return wt_path
