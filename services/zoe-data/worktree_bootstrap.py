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
_GITHUB_PR_RE = re.compile(r"/pull/(\d+)(?:\D|$)")


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


def _branch_is_shared(repo: Path, branch: str) -> bool:
    """True when MORE THAN ONE worktree has ``branch`` checked out.

    Removing such a worktree deletes the shared branch out from under the other
    one — which may be a live agent session. Merged-ness says nothing about
    whether someone is still standing on a branch, so callers must check this
    independently of :func:`_branch_merged`.

    Git refuses a double-checkout without ``--force``, but the forced state does
    occur in practice (2026-07-18: an agent worktree and the session driving it
    shared one merged branch; every other guard passed and the pruner offered to
    remove it).

    Fails CLOSED: if ``git worktree list`` cannot be read we report True, because
    an unverifiable state must not authorise a destructive ``--force`` removal.
    """
    if not branch:
        return False
    listed = _run_git(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo),
        timeout=30,
    )
    if listed.returncode != 0:
        logger.warning(
            "worktree_bootstrap: cannot list worktrees to check whether %s is shared; "
            "treating as shared (fail-closed)",
            branch,
        )
        return True
    target = f"branch refs/heads/{branch}"
    return sum(1 for line in listed.stdout.splitlines() if line.strip() == target) > 1


def _current_branch(path: Path) -> str:
    result = _run_git(
        ["git", "branch", "--show-current"],
        cwd=str(path),
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot inspect worktree branch for {path}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    return result.stdout.strip()


def _working_tree_clean(path: Path) -> bool:
    result = _run_git(
        ["git", "status", "--porcelain"],
        cwd=str(path),
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot inspect worktree status for {path}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    return not result.stdout.strip()


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
        actual_branch = _current_branch(wt_path)
        if actual_branch != branch:
            raise RuntimeError(
                f"existing worktree {wt_path} is on {actual_branch!r}, expected fresh task branch {branch!r}"
            )
        if not _working_tree_clean(wt_path):
            raise RuntimeError(
                f"existing worktree {wt_path} has uncommitted changes; refusing to reuse stale ticket branch"
            )
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


def _is_ancestor(repo: Path, ref: str, base_ref: str) -> bool:
    """True when ``ref`` is reachable from ``base_ref`` (a real, non-squash merge)."""
    result = _run_git(
        ["git", "merge-base", "--is-ancestor", ref, base_ref],
        cwd=str(repo),
        timeout=30,
    )
    return result.returncode == 0


def _pr_is_merged(branch: str, *, cwd: str) -> bool:
    """Best-effort: True when ``branch`` has a merged GitHub PR (squash-merge case).

    Squash-merged branches are not git ancestors of ``main``, so the ancestor
    check alone never reclaims them — that is what let hundreds of stale task
    worktrees accumulate. ``gh`` is optional; any failure (gh absent, no PR,
    network) is treated as "not known-merged" so we fail safe and keep the tree.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "state,mergedAt"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        import json

        data = json.loads(result.stdout or "{}")
    except ValueError:
        return False
    return (data.get("state") or "").upper() == "MERGED" and bool(data.get("mergedAt"))


def _branch_merged(repo: Path, branch: str, base_ref: str, *, consult_pr: bool) -> bool:
    """True when ``branch`` is merged into ``base_ref`` (ancestor or squash-merged PR)."""
    tip = _run_git(["git", "rev-parse", "--verify", branch], cwd=str(repo), timeout=30)
    if tip.returncode != 0:
        # No local branch ref (e.g. detached worktree); treat as not-merged here.
        return False
    if _is_ancestor(repo, branch, base_ref):
        return True
    return consult_pr and _pr_is_merged(branch, cwd=str(repo))


def _worktree_is_dirty(wt_path: Path) -> bool:
    status = _run_git(["git", "status", "--porcelain"], cwd=str(wt_path), timeout=30)
    # On any error, be conservative and treat as dirty so we never discard work.
    return status.returncode != 0 or bool(status.stdout.strip())


def resolve_base_ref(base_branch: str = "main") -> str:
    """Public wrapper: fetch+resolve ``origin/<base_branch>`` once.

    Callers cleaning several task worktrees in one pass (e.g. a completed chain)
    should resolve the base ref once and pass it to ``remove_task_worktree`` via
    ``base_ref`` so each removal does not re-run ``git fetch``.
    """
    return _base_ref(Path(zoe_repo_root()), base_branch)


def remove_task_worktree(
    task_id: str,
    *,
    base_branch: str = "main",
    base_ref: str | None = None,
    require_merged: bool = True,
    consult_pr: bool = True,
) -> bool:
    """Remove a task's worktree and its ``wt/<task_id>`` branch once merged.

    Self-guarding and idempotent: safe to call from the poll loop on any task
    that reached terminal ``done``. It only removes when the worktree is
    registered, has no uncommitted changes, and (when ``require_merged``) the
    branch is merged into ``origin/<base_branch>`` — a true ancestor merge or a
    squash-merged PR. Otherwise it is a no-op and returns ``False``.

    Pass ``base_ref`` (from :func:`resolve_base_ref`) to skip the per-call
    ``git fetch`` when cleaning many worktrees at once.

    Returns ``True`` only when the worktree was actually removed.
    """
    task_id = _validate_task_id(task_id)
    repo = Path(zoe_repo_root())
    wt_path = worktree_path(task_id)
    branch = worktree_branch(task_id)

    if not _worktree_registered(repo, wt_path):
        return False

    if _worktree_is_dirty(wt_path):
        logger.info("worktree_bootstrap: keeping %s — uncommitted changes", wt_path)
        return False

    if _branch_is_shared(repo, branch):
        # Another worktree holds this same branch; removing this one would delete
        # the branch out from under it (possibly a live agent session). Merged-ness
        # is irrelevant here, so this is checked before the merge test below.
        logger.info(
            "worktree_bootstrap: keeping %s — branch %s checked out by another worktree",
            wt_path,
            branch,
        )
        return False

    if require_merged:
        base_ref = base_ref or _base_ref(repo, base_branch)
        if not _branch_merged(repo, branch, base_ref, consult_pr=consult_pr):
            logger.info(
                "worktree_bootstrap: keeping %s — %s not merged into %s",
                wt_path,
                branch,
                base_ref,
            )
            return False

    removed = _run_git(
        ["git", "worktree", "remove", "--force", str(wt_path)],
        cwd=str(repo),
        timeout=60,
    )
    if removed.returncode != 0:
        logger.warning(
            "worktree_bootstrap: could not remove worktree %s: %s",
            wt_path,
            (removed.stderr or removed.stdout or "").strip() or "unknown error",
        )
        return False

    deleted = _run_git(["git", "branch", "-D", branch], cwd=str(repo), timeout=30)
    if deleted.returncode != 0:
        logger.warning(
            "worktree_bootstrap: removed worktree %s but could not delete branch %s: %s",
            wt_path,
            branch,
            (deleted.stderr or deleted.stdout or "").strip() or "unknown error",
        )
    logger.info("worktree_bootstrap: pruned task worktree %s (%s)", wt_path, branch)
    return True


def prune_merged_worktrees(
    *,
    base_branch: str = "main",
    min_age_days: int = 7,
    execute: bool = True,
    consult_pr: bool = True,
) -> list[dict[str, str]]:
    """Sweep stale worktrees whose branches are merged into ``origin/<base>``.

    Orphan safety net for tasks that crashed or lost their terminal handoff and
    never self-cleaned. Mirrors ``scripts/maintenance/prune_worktrees.sh`` but in
    process so the Multica harness can run it on its maintenance tick. Skips the
    live checkout, locked worktrees, dirty worktrees, branches not merged into
    ``origin/<base>`` (ancestor or squash-merged PR), and worktrees with activity
    newer than ``min_age_days``.

    Returns one record per worktree acted on or considered, with a ``decision``
    of ``removed`` / ``would-remove`` / ``skip:<reason>``.
    """
    repo = Path(zoe_repo_root())
    base_ref = _base_ref(repo, base_branch)
    live_root = _run_git(["git", "rev-parse", "--show-toplevel"], cwd=str(repo), timeout=30)
    live_path = (live_root.stdout or "").strip()
    min_age_seconds = max(0, min_age_days) * 86400

    import time

    now = time.time()
    listed = _run_git(["git", "worktree", "list", "--porcelain"], cwd=str(repo), timeout=60)
    if listed.returncode != 0:
        raise RuntimeError(
            f"git worktree list failed: {(listed.stderr or '').strip() or 'unknown error'}"
        )

    # Branches held by MORE THAN ONE worktree. Removing one of them deletes the
    # shared branch out from under the other — which may be a LIVE agent session.
    # Merged-ness says nothing about whether someone is still standing on a
    # branch, so this is checked independently of _branch_merged. Git refuses a
    # double-checkout without --force, but the forced state does occur in
    # practice (seen 2026-07-18: an agent worktree and the session driving it
    # shared one merged branch, and every other guard passed).
    #
    # Derived from the SAME --porcelain output already fetched above, so this
    # costs no extra git call.
    _branch_counts: dict[str, int] = {}
    for _line in listed.stdout.splitlines():
        if _line.startswith("branch refs/heads/"):
            _br = _line.removeprefix("branch refs/heads/").strip()
            _branch_counts[_br] = _branch_counts.get(_br, 0) + 1
    shared_branches = {b for b, n in _branch_counts.items() if n > 1}

    results: list[dict[str, str]] = []
    path = ""
    branch = ""
    locked = False

    def _flush() -> None:
        nonlocal path, branch, locked
        if not path:
            return
        record = {"worktree": path, "branch": branch}
        reason = ""
        if path == live_path:
            reason = "live checkout"
        elif locked:
            reason = "locked"
        elif not branch:
            # Classify detached HEADs before touching the tree — a missing/half-
            # removed path would otherwise read as "dirty" and hide the real state.
            reason = "detached HEAD"
        elif branch in shared_branches:
            # Checked BEFORE the merge test: a shared branch is unsafe to remove
            # regardless of whether it merged.
            reason = "branch checked out by another worktree"
        elif _worktree_is_dirty(Path(path)):
            reason = "dirty"
        else:
            age_src = _run_git(["git", "log", "-1", "--format=%ct"], cwd=path, timeout=30)
            try:
                activity = float((age_src.stdout or "0").strip() or 0)
            except ValueError:
                activity = 0.0
            if now - activity < min_age_seconds:
                reason = f"too recent (<{min_age_days}d idle)"
            elif not _branch_merged(repo, branch, base_ref, consult_pr=consult_pr):
                reason = "branch not merged"
        if reason:
            record["decision"] = f"skip:{reason}"
        elif not execute:
            record["decision"] = "would-remove"
        else:
            rm = _run_git(
                ["git", "worktree", "remove", "--force", path], cwd=str(repo), timeout=60
            )
            if rm.returncode != 0:
                record["decision"] = "skip:remove-failed"
            else:
                _run_git(["git", "branch", "-D", branch], cwd=str(repo), timeout=30)
                record["decision"] = "removed"
        results.append(record)
        path, branch, locked = "", "", False

    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            _flush()
            path = line.removeprefix("worktree ").strip()
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/").strip()
        elif line.startswith("locked"):
            locked = True
    _flush()

    if execute:
        _run_git(["git", "worktree", "prune"], cwd=str(repo), timeout=60)
    return results


def _extract_pr_number(pr_url: str) -> str:
    match = _GITHUB_PR_RE.search((pr_url or "").strip())
    if not match:
        raise ValueError(f"cannot extract GitHub PR number from {pr_url!r}")
    return match.group(1)


def prepare_existing_pr_revision_worktree(task_id: str, pr_url: str, *, base_branch: str = "main") -> Path:
    """Create/pin a task worktree and reset it to the existing PR head.

    Revision tasks must not rely on the worker prompt to check out the PR. Zoe
    prepares the workspace before the Hermes worker is allowed to inspect files;
    if the fetch/reset fails, dispatch can block the Kanban task without burning
    a paid agent run on the wrong branch.
    """
    wt_path = prepare_kanban_worktree(task_id, base_branch=base_branch)
    pr_number = _extract_pr_number(pr_url)
    pr_ref = f"refs/wt-pr/{task_id}"

    fetch = _run_git(
        ["git", "fetch", "origin", f"+pull/{pr_number}/head:{pr_ref}"],
        cwd=str(wt_path),
        timeout=120,
    )
    if fetch.returncode != 0:
        raise RuntimeError(
            f"git fetch origin +pull/{pr_number}/head:{pr_ref} failed for {task_id}:"
            f" {(fetch.stderr or fetch.stdout or '').strip() or 'unknown error'}"
        )

    reset = _run_git(
        ["git", "reset", "--hard", pr_ref],
        cwd=str(wt_path),
        timeout=60,
    )
    if reset.returncode != 0:
        raise RuntimeError(
            f"git reset --hard {pr_ref} failed for {task_id}:"
            f" {(reset.stderr or reset.stdout or '').strip() or 'unknown error'}"
        )

    cleanup = _run_git(["git", "update-ref", "-d", pr_ref], cwd=str(wt_path), timeout=30)
    if cleanup.returncode != 0:
        logger.warning(
            "worktree_bootstrap: could not delete temporary PR ref %s for %s: %s",
            pr_ref,
            task_id,
            (cleanup.stderr or cleanup.stdout or "").strip() or "unknown error",
        )

    logger.info(
        "worktree_bootstrap: prepared existing PR revision workspace %s -> PR #%s",
        task_id,
        pr_number,
    )
    return wt_path
