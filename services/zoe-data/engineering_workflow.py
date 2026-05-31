"""Durable Multica -> Hermes -> PR workflow coordination.

This module owns workflow state. Hermes still does the implementation work; Zoe
tracks phases, links Multica/background task rows, and records review progress.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ACTIVE_PHASES = {
    "queued",
    "hermes_running",
    "pr_open",
    "greptile_wait",
    "fixing",
}
NON_RETRYABLE_PHASES = {"done", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def _parse_pr_url(text: str) -> tuple[str | None, int | None]:
    match = re.search(r"https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)", text or "")
    if not match:
        return None, None
    return match.group(0), int(match.group(2))


def _parse_blocker(text: str) -> str | None:
    match = re.search(r"(?im)^BLOCKER\s*=\s*(.+)$", text or "")
    if match:
        return match.group(1).strip()[:1000]
    return None


def build_hermes_prompt(task: str, *, workflow_id: str, max_rounds: int, target_confidence: int) -> str:
    """Return the structured task sent to Hermes."""
    return (
        "Use the local Zoe engineering workflow for this task.\n"
        "- Use `zoe-engineering` first.\n"
        "- Use `source-code-context` or Graphify when needed.\n"
        "- Keep the change PR-sized and do not push to main.\n"
        "- Create a feature branch, commit verified changes, push, and open a PR.\n"
        "- Use `github-greptile-loop` after the PR exists.\n"
        "- Stop and report BLOCKER=... for missing auth, dirty tree, secrets, destructive operations, "
        "database/dockers changes needing human approval, or ambiguous product decisions.\n\n"
        "Required final response format:\n"
        "PR_URL=<GitHub PR URL, or blank if blocked>\n"
        "BLOCKER=<reason, or blank>\n"
        "TESTS=<commands/checks run>\n"
        "SUMMARY=<short summary>\n\n"
        f"workflow_id={workflow_id}\n"
        f"max_rounds={max_rounds}\n"
        f"target_confidence={target_confidence}\n\n"
        f"Task:\n{task}"
    )


async def create_engineering_task(
    *,
    user_id: str,
    task: str,
    title: str | None = None,
    source: str = "api",
    source_id: str | None = None,
    multica_issue_id: str | None = None,
    idempotency_key: str | None = None,
    max_rounds: int = 5,
    target_confidence: int = 5,
) -> dict[str, Any]:
    """Create or return an idempotent engineering task row."""
    from db_pool import get_db_ctx

    task = task.strip()
    if not task:
        raise ValueError("task is required")
    title = (title or task.splitlines()[0])[:160]
    # Reject autopilot wrapper issues early so they never enter the engineering
    # queue.  These are Multica tracker rows created by _fire_autopilot_job and
    # have titles like "Autopilot: Board Review" — they carry no actionable
    # engineering task and dispatching them causes recursive noise.
    if title.lower().startswith("autopilot:"):
        raise ValueError(
            f"Autopilot wrapper issues must not be dispatched as engineering tasks: {title!r}"
        )
    now = _now()

    async with get_db_ctx() as db:
        if idempotency_key:
            existing = await db.fetchrow(
                "SELECT * FROM engineering_tasks WHERE idempotency_key=$1",
                idempotency_key,
            )
            if existing:
                return dict(existing)
        if multica_issue_id:
            existing = await db.fetchrow(
                """SELECT * FROM engineering_tasks
                   WHERE multica_issue_id=$1 AND phase NOT IN ('done', 'cancelled')
                   ORDER BY updated_at DESC LIMIT 1""",
                multica_issue_id,
            )
            if existing:
                return dict(existing)

        workflow_id = str(uuid.uuid4())
        row = await db.fetchrow(
            """INSERT INTO engineering_tasks
               (id, user_id, title, task, source, source_id, multica_issue_id,
                idempotency_key, phase, status, max_rounds, target_confidence,
                created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'queued','active',$9,$10,$11,$12)
               RETURNING *""",
            workflow_id,
            user_id,
            title,
            task,
            source,
            source_id,
            multica_issue_id,
            idempotency_key,
            max_rounds,
            target_confidence,
            now,
            now,
        )
    return dict(row)


async def get_engineering_task(task_id: str) -> dict[str, Any] | None:
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        return _row(await db.fetchrow("SELECT * FROM engineering_tasks WHERE id=$1", task_id))


async def list_engineering_tasks(limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
    from db_pool import get_db_ctx

    limit = max(1, min(limit, 100))
    async with get_db_ctx() as db:
        if status:
            rows = await db.fetch(
                "SELECT * FROM engineering_tasks WHERE status=$1 ORDER BY updated_at DESC LIMIT $2",
                status,
                limit,
            )
        else:
            rows = await db.fetch(
                "SELECT * FROM engineering_tasks ORDER BY updated_at DESC LIMIT $1",
                limit,
            )
    return [dict(row) for row in rows]


async def start_engineering_task(task_id: str) -> dict[str, Any]:
    """Queue Hermes for a workflow if it has not already been queued."""
    from background_runner import enqueue_background_task
    from db_pool import get_db_ctx

    now = _now()
    async with get_db_ctx() as db:
        task = await db.fetchrow(
            """UPDATE engineering_tasks
               SET phase='hermes_running', updated_at=$1
               WHERE id=$2 AND phase='queued' AND background_task_id IS NULL
               RETURNING *""",
            now,
            task_id,
        )
    if not task:
        existing = await get_engineering_task(task_id)
        if existing:
            return existing
        raise ValueError(f"engineering task not found: {task_id}")
    task = dict(task)

    prompt = build_hermes_prompt(
        task["task"],
        workflow_id=task_id,
        max_rounds=int(task.get("max_rounds") or 5),
        target_confidence=int(task.get("target_confidence") or 5),
    )
    try:
        background_task_id = await enqueue_background_task(
            prompt,
            task["user_id"],
            multica_issue_id=task.get("multica_issue_id"),
        )
    except Exception as exc:
        async with get_db_ctx() as db:
            row = await db.fetchrow(
                """UPDATE engineering_tasks
                   SET phase='blocked', status='blocked', blocker_reason=$1,
                       last_error=$1, updated_at=$2, completed_at=$2
                   WHERE id=$3 RETURNING *""",
                f"Hermes enqueue failed: {exc}",
                _now(),
                task_id,
            )
        if row:
            await sync_multica_issue(dict(row), note=f"Blocked: {row['blocker_reason']}")
        raise
    async with get_db_ctx() as db:
        row = await db.fetchrow(
            """UPDATE engineering_tasks
               SET background_task_id=$1, updated_at=$2
               WHERE id=$3
               RETURNING *""",
            background_task_id,
            _now(),
            task_id,
        )
    await sync_multica_issue(dict(row), note="Hermes implementation started.")
    return dict(row)


async def create_and_start_engineering_task(**kwargs: Any) -> dict[str, Any]:
    task = await create_engineering_task(**kwargs)
    if task.get("phase") == "queued":
        task = await start_engineering_task(task["id"])
    return task


async def reconcile_background_task(background_task_id: int) -> dict[str, Any] | None:
    """Advance an engineering workflow from its linked background task result."""
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        workflow = await db.fetchrow(
            "SELECT * FROM engineering_tasks WHERE background_task_id=$1",
            background_task_id,
        )
        if not workflow:
            return None
        if workflow["phase"] in ("cancelled", "done"):
            return dict(workflow)
        background = await db.fetchrow(
            "SELECT id, status, result FROM background_tasks WHERE id=$1",
            background_task_id,
        )
        if not background:
            return dict(workflow)

        now = _now()
        if background["status"] in ("error", "blocked"):
            row = await db.fetchrow(
                """UPDATE engineering_tasks
                   SET phase='blocked', status='blocked', blocker_reason=$1,
                       last_error=$1, updated_at=$2, completed_at=$2
                   WHERE id=$3 AND phase NOT IN ('cancelled', 'done')
                   RETURNING *""",
                background["result"] or background["status"],
                now,
                workflow["id"],
            )
            if not row:
                return dict(workflow)
            await sync_multica_issue(dict(row), note=f"Blocked: {row['blocker_reason']}")
            return dict(row)

        if background["status"] != "done":
            return dict(workflow)

        result = background["result"] or ""
        blocker = _parse_blocker(result)
        pr_url, pr_number = _parse_pr_url(result)
        if blocker:
            row = await db.fetchrow(
                """UPDATE engineering_tasks
                   SET phase='blocked', status='blocked', blocker_reason=$1,
                       updated_at=$2, completed_at=$2
                   WHERE id=$3 AND phase NOT IN ('cancelled', 'done')
                   RETURNING *""",
                blocker,
                now,
                workflow["id"],
            )
            if not row:
                return dict(workflow)
            await sync_multica_issue(dict(row), note=f"Blocked: {blocker}")
            return dict(row)

        if not pr_url:
            row = await db.fetchrow(
                """UPDATE engineering_tasks
                   SET phase='blocked', status='blocked',
                       blocker_reason='Hermes completed without PR_URL',
                       updated_at=$1, completed_at=$1
                   WHERE id=$2 AND phase NOT IN ('cancelled', 'done')
                   RETURNING *""",
                now,
                workflow["id"],
            )
            if not row:
                return dict(workflow)
            await sync_multica_issue(dict(row), note="Blocked: Hermes completed without PR_URL.")
            return dict(row)

        row = await db.fetchrow(
            """UPDATE engineering_tasks
               SET phase='pr_open', pr_url=$1, pr_number=$2, updated_at=$3
               WHERE id=$4 AND phase NOT IN ('cancelled', 'done')
               RETURNING *""",
            pr_url,
            pr_number,
            now,
            workflow["id"],
        )
        if not row:
            return dict(workflow)
    await sync_multica_issue(dict(row), note=f"PR opened: {pr_url}")
    return dict(row)


async def update_greptile_state(
    task_id: str,
    *,
    greptile_status: str,
    confidence: int | None = None,
    unaddressed_count: int | None = None,
) -> dict[str, Any]:
    from db_pool import get_db_ctx

    current = await get_engineering_task(task_id)
    target_confidence = int((current or {}).get("target_confidence") or 5)
    phase = "ready_for_human"
    status = "active"
    if unaddressed_count and unaddressed_count > 0:
        phase = "greptile_wait"
    elif confidence is not None and confidence < target_confidence:
        phase = "greptile_wait"

    now = _now()
    async with get_db_ctx() as db:
        row = await db.fetchrow(
            """UPDATE engineering_tasks
               SET greptile_status=$1, greptile_confidence=$2,
                   greptile_unaddressed_count=$3, phase=$4, status=$5,
                   updated_at=$6
               WHERE id=$7 AND phase NOT IN ('cancelled', 'done')
               RETURNING *""",
            greptile_status,
            confidence,
            unaddressed_count,
            phase,
            status,
            now,
            task_id,
        )
    if not row:
        current = await get_engineering_task(task_id)
        return current or {}
    note = f"Greptile: {greptile_status}"
    if confidence is not None:
        note += f", confidence {confidence}/5"
    if unaddressed_count is not None:
        note += f", unaddressed {unaddressed_count}"
    await sync_multica_issue(dict(row), note=note)
    return dict(row)


async def check_greptile_for_task(task_id: str) -> dict[str, Any]:
    """Fetch live Greptile state for a workflow PR and persist it."""
    from greptile_client import DEFAULT_REPO, get_pr_status

    task = await get_engineering_task(task_id)
    if not task:
        raise ValueError(f"engineering task not found: {task_id}")
    pr_number = task.get("pr_number")
    if not pr_number and task.get("pr_url"):
        _, pr_number = _parse_pr_url(task["pr_url"])
    if not pr_number:
        raise ValueError("engineering task has no PR number")
    status = await get_pr_status(
        repo=os.environ.get("ZOE_GITHUB_REPO", DEFAULT_REPO),
        pr_number=pr_number,
        default_branch=os.environ.get("ZOE_GITHUB_DEFAULT_BRANCH", "main"),
    )
    return await update_greptile_state(
        task_id,
        greptile_status=status.get("reviewCompleteness") or "reviewed",
        confidence=status.get("confidenceScore"),
        unaddressed_count=status.get("unaddressedCount"),
    )


async def retry_engineering_task(task_id: str) -> dict[str, Any]:
    """Clear blocker state and start another Hermes round."""
    from db_pool import get_db_ctx

    task = await get_engineering_task(task_id)
    if not task:
        raise ValueError(f"engineering task not found: {task_id}")
    if task.get("phase") in NON_RETRYABLE_PHASES:
        return task
    round_count = int(task.get("round_count") or 0)
    max_rounds = int(task.get("max_rounds") or 5)
    if round_count >= max_rounds:
        raise ValueError(f"max rounds reached ({round_count}/{max_rounds})")
    now = _now()
    async with get_db_ctx() as db:
        await db.execute(
            """UPDATE engineering_tasks
               SET phase='queued', status='active', background_task_id=NULL,
                   blocker_reason=NULL, last_error=NULL, round_count=$1,
                   updated_at=$2
               WHERE id=$3 AND phase NOT IN ('done', 'cancelled')""",
            round_count + 1,
            now,
            task_id,
        )
    return await start_engineering_task(task_id)


async def cancel_engineering_task(task_id: str, reason: str = "cancelled") -> dict[str, Any]:
    from db_pool import get_db_ctx

    now = _now()
    async with get_db_ctx() as db:
        row = await db.fetchrow(
            """UPDATE engineering_tasks
               SET phase='cancelled', status='cancelled', blocker_reason=$1,
                   updated_at=$2, completed_at=$2
               WHERE id=$3 AND phase NOT IN ('done', 'cancelled')
               RETURNING *""",
            reason,
            now,
            task_id,
        )
    if not row:
        existing = await get_engineering_task(task_id)
        if existing:
            return existing
        raise ValueError(f"engineering task not found: {task_id}")
    await sync_multica_issue(dict(row), note=f"Cancelled: {reason}")
    return dict(row)


async def sync_multica_issue(task: dict[str, Any], *, note: str = "") -> None:
    """Best-effort Multica status/description update for a workflow."""
    multica_issue_id = task.get("multica_issue_id")
    if not multica_issue_id:
        return
    try:
        from multica_client import get_multica_client

        client = get_multica_client()
        if not client.is_configured():
            return
        issue = await client.get_issue(multica_issue_id)
        current = issue.get("description", "") if isinstance(issue, dict) else ""
        lines = [
            "",
            "---",
            f"Engineering workflow: `{task['id']}`",
            f"Phase: `{task.get('phase')}`",
        ]
        if task.get("pr_url"):
            lines.append(f"PR: {task['pr_url']}")
        if task.get("greptile_status"):
            lines.append(f"Greptile: {task['greptile_status']}")
        if task.get("blocker_reason"):
            lines.append(f"Blocker: {task['blocker_reason']}")
        if note:
            lines.append(f"Update: {note}")
        description = (current.split("\n---\nEngineering workflow:", 1)[0]).rstrip()
        status = {
            "queued": "todo",
            "hermes_running": "in_progress",
            "pr_open": "in_review",
            "greptile_wait": "in_review",
            "fixing": "in_progress",
            "ready_for_human": "in_review",
            "done": "done",
            "blocked": "in_review",
            "cancelled": "cancelled",
        }.get(task.get("phase"))
        await client.update_issue(
            multica_issue_id,
            status=status,
            description=description + "\n" + "\n".join(lines),
        )
    except Exception as exc:
        logger.debug("engineering_workflow: Multica sync failed: %s", exc)
