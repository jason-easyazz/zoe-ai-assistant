"""Executor queue backend — the Phase-2 seam swap for ``kanban_adapter``.

Phase 2 of ``docs/architecture/multica-executor-migration.md``: swap the
dispatch target from the ``hermes kanban`` CLI to the Phase-1 Zoe-native
executor, **keeping every phase, gate and deterministic override untouched**.

The whole point of this module is that `kanban_adapter.py` does NOT change.
It encodes twelve PRs of *discovered* failure modes (stranded chains #592/#597,
finish-without-shipping #520, PR-URL handoff #601, blocking verifiers
#607/#632, flaky reviewers #672/#677, closeout agents that claim success
without merging #679/#681, zombie workers #685, no-op implements #694).
Rebuilding it means rediscovering all of that. So this module implements the
**same six CLI verbs** the adapter already shells — ``list``, ``show``,
``create``, ``block``, ``archive``, ``complete`` — against Multica's own
``agent_task_queue`` + ``activity_log``, and `_run` simply dispatches here
when ``ZOE_KANBAN_BACKEND=executor``.

Two contracts are load-bearing:

1. **The row/detail shapes must match what the adapter reads.** Those fields
   were derived from the adapter itself, not guessed: rows expose
   ``id/title/body/status/block_reason/result/workspace_path`` (plus
   ``idempotency_key``, which `_row_ref_key` prefers over parsing the
   ``zoe-ref:`` body marker), and ``show`` exposes
   ``task/latest_summary/comments/runs/events/metadata``.

2. **Every transition records a reason in `activity_log`, in the same
   transaction as the status change.** Hermes's kanban recorded
   ``blocker_reason`` zero times across 128 blocked tickets, which is why
   June's failure modes had to be found one at a time by hand. Under this
   backend a blocked task's reason is durable and visible in `multica-web`.

Nothing here changes Multica's schema — it is a third-party product. Creates
are made idempotent with a transaction-scoped advisory lock instead of a new
unique index.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg

logger = logging.getLogger(__name__)

# Multica's status vocabulary is not Hermes's. The adapter speaks Hermes, so
# the backend translates on the way out. `failed -> blocked` is deliberate:
# the adapter treats `blocked` as an ACTIVE, recoverable state and already has
# recovery paths for it (converge-noop-implement, PR-handoff recovery), while
# `block_reason` finally carries the durable why.
_STATUS_TO_HERMES = {
    "queued": "ready",
    "dispatched": "running",
    "running": "running",
    "completed": "done",
    "failed": "blocked",
    "cancelled": "archived",
}

_EXECUTOR_AGENT_NAME = "Flue Executor"
_EXECUTOR_RUNTIME_NAME = "Flue Executor (Zoe)"

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()
_identity: dict[str, str] | None = None


class ExecutorBackendError(RuntimeError):
    """Raised when an executor-backed kanban command cannot be satisfied."""


def multica_dsn() -> str:
    """DSN for Multica's OWN database (never Zoe's SoR).

    Derived from the service's POSTGRES_URL with the database name swapped, so
    there is no second credential to rotate. Override with MULTICA_DATABASE_URL.
    """
    override = os.environ.get("MULTICA_DATABASE_URL", "").strip()
    if override:
        return override
    base = os.environ.get("POSTGRES_URL", "").strip()
    if not base:
        raise ExecutorBackendError(
            "No POSTGRES_URL/MULTICA_DATABASE_URL — cannot reach Multica's database."
        )
    parts = urlparse(base)
    db = os.environ.get("MULTICA_DB_NAME", "multica").strip() or "multica"
    return urlunparse(parts._replace(path=f"/{db}"))


async def get_pool() -> asyncpg.Pool:
    """Small dedicated pool for Multica's DB (separate from Zoe's SoR pool)."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                multica_dsn(),
                min_size=1,
                max_size=int(os.environ.get("ZOE_MULTICA_POOL_MAX", "4") or "4"),
                command_timeout=30,
            )
    return _pool


async def close_pool() -> None:
    """Release the Multica pool (tests + shutdown)."""
    global _pool, _identity
    if _pool is not None:
        await _pool.close()
        _pool = None
    _identity = None


async def ensure_executor_identity(conn: asyncpg.Connection) -> dict[str, str]:
    """Idempotently register the executor's `agent` + `agent_runtime` rows.

    Additive only — no schema change, no existing row touched. Multica's FKs
    require both before a task can be queued. Cached per process after the
    first call.
    """
    global _identity
    if _identity is not None:
        return _identity
    workspace_id = os.environ.get("ZOE_MULTICA_WORKSPACE_ID", "").strip()
    if not workspace_id:
        workspace_id = await conn.fetchval(
            "SELECT id::text FROM workspace ORDER BY created_at LIMIT 1"
        )
    if not workspace_id:
        raise ExecutorBackendError("Multica has no workspace row to attach the executor to.")

    runtime_id = await conn.fetchval(
        "SELECT id::text FROM agent_runtime WHERE workspace_id=$1::uuid AND name=$2",
        workspace_id, _EXECUTOR_RUNTIME_NAME,
    )
    if not runtime_id:
        runtime_id = await conn.fetchval(
            """INSERT INTO agent_runtime (workspace_id, name, runtime_mode, provider, status,
                                          device_info, metadata, last_seen_at)
               VALUES ($1::uuid, $2, 'local', 'flue', 'online', 'Zoe Jetson Orin NX',
                       '{"executor":"labs/flue-executor","phase":"2"}'::jsonb, now())
               RETURNING id::text""",
            workspace_id, _EXECUTOR_RUNTIME_NAME,
        )
    agent_id = await conn.fetchval(
        "SELECT id::text FROM agent WHERE workspace_id=$1::uuid AND name=$2",
        workspace_id, _EXECUTOR_AGENT_NAME,
    )
    if not agent_id:
        # `agent` requires runtime_mode + runtime_id (NOT NULL, no defaults).
        # max_concurrent_tasks=1 mirrors the executor's single-lane contract
        # (today's POLL_DISPATCH_LIMIT=1) so multica-web shows the truth.
        # NOTE the vocabularies differ between tables and are CHECK-enforced:
        # agent.status is idle|working|blocked|error|offline (NOT the
        # agent_runtime online|offline set), and description is capped at 255.
        agent_id = await conn.fetchval(
            """INSERT INTO agent (workspace_id, name, runtime_mode, runtime_id,
                                  description, max_concurrent_tasks, status)
               VALUES ($1::uuid, $2, 'local', $3::uuid,
                       'Zoe-native Flue executor (Phase 1): single lane, reason on every transition',
                       1, 'idle')
               RETURNING id::text""",
            workspace_id, _EXECUTOR_AGENT_NAME, runtime_id,
        )
    _identity = {
        "workspace_id": workspace_id,
        "runtime_id": runtime_id,
        "agent_id": agent_id,
    }
    return _identity


async def _log_activity(
    conn: asyncpg.Connection,
    identity: dict[str, str],
    task_id: str,
    action: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write the reason through to Multica's activity_log.

    Called INSIDE the caller's transaction so a status change and its reason
    commit together — a reason can never be lost to a crash between two stores.
    An empty reason is refused: silence is the failure mode this migration
    exists to end.
    """
    if not (reason or "").strip():
        raise ExecutorBackendError(
            f"refusing to record '{action}' without a reason — "
            "every transition must carry one (Hermes logged 0/128)"
        )
    details = {"task_id": task_id, "reason": reason, **(extra or {})}
    await conn.execute(
        """INSERT INTO activity_log (workspace_id, issue_id, actor_type, actor_id, action, details)
           VALUES ($1::uuid, NULL, 'agent', $2::uuid, $3, $4::jsonb)""",
        identity["workspace_id"], identity["agent_id"], action, json.dumps(details),
    )


def _row_to_hermes(record: asyncpg.Record) -> dict[str, Any]:
    """Map an agent_task_queue row into the shape the adapter reads."""
    ctx = json.loads(record["context"] or "{}") if isinstance(record["context"], str) else (record["context"] or {})
    result = record["result"]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"summary": result}
    result = result or {}
    status = _STATUS_TO_HERMES.get(record["status"], record["status"])
    return {
        "id": str(record["id"]),
        "title": ctx.get("title") or "",
        "body": ctx.get("body") or "",
        "status": status,
        # The adapter reads block_reason on blocked rows; failure_reason is
        # where the executor records WHY, so surface it under both names.
        "block_reason": record["failure_reason"] if status == "blocked" else None,
        "result": result.get("summary") or result.get("result") or "",
        # Always the RESOLVED path — recovery paths in the adapter treat this
        # as a real directory (it greps the worktree for unpushed commits).
        "workspace_path": record["work_dir"] or "",
        "idempotency_key": ctx.get("idempotency_key") or "",
    }


def _parse_flags(args: list[str], flags_with_values: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split argv into {flag: [values]} plus positionals, mirroring the CLI."""
    parsed: dict[str, list[str]] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            name = token[2:]
            if name in flags_with_values and i + 1 < len(args):
                parsed.setdefault(name, []).append(args[i + 1])
                i += 2
                continue
            parsed.setdefault(name, []).append("")
            i += 1
            continue
        positional.append(token)
        i += 1
    return parsed, positional


_CREATE_VALUE_FLAGS = {
    "assignee", "workspace", "idempotency-key", "max-runtime", "max-retries",
    "created-by", "body", "skill", "parent", "goal", "goal-mode",
}


def resolve_workspace(selector: str, task_id: str) -> str:
    """Turn the adapter's SYMBOLIC ``--workspace`` selector into a real path.

    The adapter passes ``worktree`` (meaning "the task's own worktree") or
    ``dir:<abs path>`` (retro runs read-only from the main repo). Under Hermes
    the selector was resolved by the CLI, and `worktree_bootstrap`
    additionally pinned the absolute path into `~/.hermes/kanban.db` — a store
    this backend never reads. So the selector MUST be resolved here: the
    executor treats `work_dir` as a concrete filesystem path for the worker's
    sandbox, result file and logs, and would otherwise try to use the literal
    string "worktree" as a directory and fail before the worker ran.

    `worktree_path()` is deterministic (``~/.worktrees/<task_id>``, or
    ``ZOE_WORKTREE_ROOT``), so the path is correct even though the adapter
    creates the worktree a moment AFTER this create call returns.
    """
    sel = (selector or "").strip()
    if sel.startswith("dir:"):
        return sel[4:]
    if sel in ("", "worktree"):
        from worktree_bootstrap import worktree_path  # lazy: heavy import

        return str(worktree_path(task_id))
    return sel


async def run_kanban_command(args: list[str], *, expect_json: bool = False) -> Any:
    """Execute one `hermes kanban`-shaped command against the executor queue.

    `args` is the argv the adapter already builds, so the adapter needs no
    changes beyond choosing this backend.
    """
    if not args:
        raise ExecutorBackendError("empty kanban command")
    verb, rest = args[0], args[1:]
    pool = await get_pool()
    async with pool.acquire() as conn:
        identity = await ensure_executor_identity(conn)
        if verb == "list":
            return await _cmd_list(conn, identity)
        if verb == "show":
            return await _cmd_show(conn, rest)
        if verb == "create":
            return await _cmd_create(conn, identity, rest)
        if verb == "block":
            return await _cmd_block(conn, identity, rest)
        if verb == "archive":
            return await _cmd_archive(conn, identity, rest)
        if verb == "complete":
            return await _cmd_complete(conn, identity, rest)
    raise ExecutorBackendError(f"unsupported kanban verb for the executor backend: {verb}")


async def _cmd_list(conn: asyncpg.Connection, identity: dict[str, str]) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT id, status, failure_reason, result, context, work_dir
             FROM agent_task_queue
            WHERE runtime_id = $1::uuid
            ORDER BY created_at""",
        identity["runtime_id"],
    )
    return [_row_to_hermes(r) for r in rows]


async def _cmd_show(conn: asyncpg.Connection, rest: list[str]) -> dict[str, Any]:
    _, positional = _parse_flags(rest, set())
    if not positional:
        raise ExecutorBackendError("show requires a task id")
    task_id = positional[0]
    row = await conn.fetchrow(
        """SELECT id, status, failure_reason, result, context, work_dir
             FROM agent_task_queue WHERE id = $1::uuid""",
        task_id,
    )
    if row is None:
        raise ExecutorBackendError(f"no such task: {task_id}")
    mapped = _row_to_hermes(row)
    events = await conn.fetch(
        """SELECT action, details, created_at FROM activity_log
            WHERE details->>'task_id' = $1 ORDER BY created_at, id""",
        str(row["id"]),
    )
    event_list = [
        {
            "action": e["action"],
            "reason": (json.loads(e["details"]) if isinstance(e["details"], str) else e["details"] or {}).get("reason", ""),
            "created_at": e["created_at"].isoformat() if e["created_at"] else None,
        }
        for e in events
    ]
    result = row["result"]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"summary": result}
    result = result or {}
    ctx = row["context"]
    if isinstance(ctx, str):
        ctx = json.loads(ctx or "{}")
    ctx = ctx or {}
    # latest_summary is what the adapter mines for the worker's terminal
    # handoff (PR_URL=/BLOCKER=/TESTS=/SUMMARY=). Prefer the worker's own
    # summary; fall back to the failure reason so a blocked task is never
    # silent to the adapter's recovery paths.
    latest_summary = result.get("summary") or mapped["result"] or row["failure_reason"] or ""
    return {
        "task": {"workspace_path": mapped["workspace_path"], **mapped},
        "latest_summary": latest_summary,
        "comments": [],
        "runs": [
            {
                "summary": latest_summary,
                "error": row["failure_reason"] or "",
            }
        ],
        "events": event_list,
        "metadata": ctx.get("metadata") or {},
    }


async def _cmd_create(
    conn: asyncpg.Connection, identity: dict[str, str], rest: list[str]
) -> dict[str, Any]:
    flags, positional = _parse_flags(rest, _CREATE_VALUE_FLAGS)
    title = positional[0] if positional else ""
    idem = (flags.get("idempotency-key") or [""])[0]
    if not idem:
        raise ExecutorBackendError("create requires --idempotency-key")
    body = (flags.get("body") or [""])[0]
    workspace = (flags.get("workspace") or [""])[0]
    assignee = (flags.get("assignee") or [""])[0]
    parent = (flags.get("parent") or [None])[0]
    try:
        max_retries = int((flags.get("max-retries") or ["1"])[0] or "1")
    except ValueError:
        max_retries = 1
    # Route heavy work to the Omnigent lane; the executor decides at spawn time
    # (migration doc §3 unknown 3). Phase names come from the adapter's chain.
    phase = idem.rsplit(":", 1)[-1] if ":" in idem else ""
    context = {
        "title": title,
        "body": body,
        "idempotency_key": idem,
        "assignee": assignee,
        # Keep the raw selector for provenance; work_dir carries the resolved
        # concrete path (see resolve_workspace).
        "workspace_selector": workspace,
        "phase": phase,
        "lane": "heavy" if phase == "implement" else "light",
        "skills": flags.get("skill") or [],
        "max_runtime": (flags.get("max-runtime") or [""])[0],
        "created_by": (flags.get("created-by") or [""])[0],
    }

    async with conn.transaction():
        # Idempotency without touching Multica's schema: serialize creates for
        # this key so the check-then-insert cannot race a concurrent create.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", idem)
        existing = await conn.fetchval(
            """SELECT id::text FROM agent_task_queue
                WHERE runtime_id = $1::uuid AND context->>'idempotency_key' = $2
                ORDER BY created_at LIMIT 1""",
            identity["runtime_id"], idem,
        )
        if existing:
            return {"id": existing, "deduplicated": True}
        task_id = await conn.fetchval(
            """INSERT INTO agent_task_queue
                 (agent_id, issue_id, status, priority, runtime_id, work_dir, context,
                  max_attempts, parent_task_id, trigger_summary)
               VALUES ($1::uuid, NULL, 'queued', 0, $2::uuid, NULL, $3::jsonb,
                       $4, $5::uuid, $6)
               RETURNING id::text""",
            identity["agent_id"], identity["runtime_id"],
            json.dumps(context), max(1, max_retries), parent, title[:500],
        )
        # work_dir needs the task id (worktree paths are ~/.worktrees/<id>), so
        # it is resolved and written here — same transaction, so a row can
        # never be visible to the executor without its concrete work_dir.
        work_dir = resolve_workspace(workspace, task_id)
        await conn.execute(
            "UPDATE agent_task_queue SET work_dir=$2 WHERE id=$1::uuid", task_id, work_dir
        )
        await _log_activity(
            conn, identity, task_id, "task_created",
            f"queued phase '{phase or 'unknown'}' for {idem} on the "
            f"{context['lane']} lane (assignee={assignee or 'unset'}) in {work_dir}",
            {"idempotency_key": idem, "lane": context["lane"], "work_dir": work_dir},
        )
    return {"id": task_id, "deduplicated": False}


async def _cmd_block(
    conn: asyncpg.Connection, identity: dict[str, str], rest: list[str]
) -> str:
    _, positional = _parse_flags(rest, set())
    if len(positional) < 2:
        raise ExecutorBackendError("block requires <task_id> <reason>")
    task_id, reason = positional[0], positional[1]
    async with conn.transaction():
        updated = await conn.execute(
            """UPDATE agent_task_queue
                  SET status='failed', failure_reason=$2, completed_at=now()
                WHERE id=$1::uuid AND status <> 'completed'""",
            task_id, reason,
        )
        if updated.endswith(" 0"):
            # Already terminal — do not overwrite a completed result.
            return ""
        await _log_activity(conn, identity, task_id, "task_blocked", reason)
    return ""


async def _cmd_archive(
    conn: asyncpg.Connection, identity: dict[str, str], rest: list[str]
) -> str:
    _, positional = _parse_flags(rest, set())
    if not positional:
        raise ExecutorBackendError("archive requires a task id")
    task_id = positional[0]
    async with conn.transaction():
        await conn.execute(
            "UPDATE agent_task_queue SET status='cancelled', completed_at=now() WHERE id=$1::uuid",
            task_id,
        )
        await _log_activity(
            conn, identity, task_id, "task_archived",
            "archived by the Zoe bridge (chain complete or superseded)",
        )
    return ""


async def _cmd_complete(
    conn: asyncpg.Connection, identity: dict[str, str], rest: list[str]
) -> str:
    flags, positional = _parse_flags(rest, {"result", "summary", "metadata"})
    if not positional:
        raise ExecutorBackendError("complete requires a task id")
    task_id = positional[0]
    summary = (flags.get("summary") or [""])[0]
    result_text = (flags.get("result") or [""])[0]
    metadata_raw = (flags.get("metadata") or ["{}"])[0]
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {"raw": metadata_raw}
    payload = {"summary": summary or result_text, "result": result_text, "metadata": metadata}
    async with conn.transaction():
        await conn.execute(
            """UPDATE agent_task_queue
                  SET status='completed', completed_at=now(), result=$2::jsonb,
                      failure_reason=NULL,
                      context = coalesce(context,'{}'::jsonb) || $3::jsonb
                WHERE id=$1::uuid""",
            task_id, json.dumps(payload), json.dumps({"metadata": metadata}),
        )
        await _log_activity(
            conn, identity, task_id, "task_completed",
            summary or result_text or "completed without a summary from the worker",
            {"metadata": metadata},
        )
    return ""
