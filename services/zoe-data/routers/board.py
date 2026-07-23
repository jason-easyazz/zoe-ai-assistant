"""Board review surface — read-only summary of Zoe's autonomous engineering work.

Backs the "Zoe's Work" interface card so the operator can see, at a glance, what
the autonomous board runner has tackled: what is in flight, what shipped (with
PR links to review), and what is blocked and needs feedback.

Truly read-only — resolves the workspace with a SELECT (never registers identity
rows) and only ever reads `issue` + `activity_log`. Multica-unreachable degrades
gracefully; the endpoint requires an authenticated caller like every other
router. A `speech` digest is included so a chat/voice path can answer
"what's in flight / what's blocked" from the same data.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends

from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/board", tags=["board"])

_EXECUTOR_RUNTIME_NAME = "Flue Executor (Zoe)"


def _pr_from_details(details) -> str | None:
    """PR url out of an activity_log.details value (dict or json string)."""
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    url = (details or {}).get("pr_url")
    # Only surface a real https URL — never a javascript:/data: value that could
    # reach an href (defence-in-depth; the card also checks the scheme).
    return url if isinstance(url, str) and url.startswith("https://") else None


def _reason_from_details(details) -> str | None:
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    return (details or {}).get("reason")


async def _resolve_workspace_id(conn) -> str | None:
    """Read-only workspace resolution — prefer the executor runtime's workspace,
    else the oldest workspace. Never INSERTs (unlike ensure_executor_identity)."""
    ws = await conn.fetchval(
        "SELECT workspace_id::text FROM agent_runtime WHERE name=$1 ORDER BY created_at LIMIT 1",
        _EXECUTOR_RUNTIME_NAME,
    )
    if ws:
        return ws
    return await conn.fetchval("SELECT id::text FROM workspace ORDER BY created_at LIMIT 1")


def _entry(row, *, with_reason=False):
    e = {"number": row["number"], "title": row["title"], "pr_url": _pr_from_details(row["details"])}
    if with_reason:
        e["reason"] = _reason_from_details(row["details"])
    return e


@router.get("/summary")
async def board_summary(limit_done: int = 10, user: dict = Depends(get_current_user)) -> dict:
    """Counts by status + the actionable lists (in-flight, in-review, blocked,
    recently done) with PR links + block reasons from the runner's activity_log."""
    from executors.executor_queue_backend import get_pool

    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001 - Multica unreachable is not fatal to the panel
        logger.warning("board_summary: Multica unreachable: %s", exc)
        return {"ok": False, "detail": "board backend unreachable", "counts": {}}

    async with pool.acquire() as conn:
        ws = await _resolve_workspace_id(conn)
        if not ws:
            return {"ok": False, "detail": "no workspace", "counts": {}}

        counts = {
            r["status"]: r["n"]
            for r in await conn.fetch(
                "SELECT status, count(*)::int AS n FROM issue WHERE workspace_id=$1::uuid GROUP BY status",
                ws,
            )
        }

        # Only the statuses the card actually lists, with the done set bounded in
        # SQL — never load every done/backlog/cancelled issue ever recorded.
        async def _rows(statuses, limit):
            return await conn.fetch(
                """SELECT i.number, i.title, i.status, a.details
                     FROM issue i
                     LEFT JOIN LATERAL (
                         SELECT details FROM activity_log
                          WHERE issue_id = i.id ORDER BY created_at DESC LIMIT 1
                     ) a ON true
                    WHERE i.workspace_id=$1::uuid AND i.status = ANY($2::text[])
                    ORDER BY i.updated_at DESC
                    LIMIT $3""",
                ws, statuses, limit,
            )

        active_rows = await _rows(["in_progress", "in_review", "blocked"], 100)
        done_rows = await _rows(["done"], max(0, min(limit_done, 50)))

    in_progress = [_entry(r) for r in active_rows if r["status"] == "in_progress"]
    in_review = [_entry(r) for r in active_rows if r["status"] == "in_review"]
    blocked = [_entry(r, with_reason=True) for r in active_rows if r["status"] == "blocked"]
    done = [_entry(r) for r in done_rows]

    bits = []
    if blocked:
        bits.append(f"{len(blocked)} need feedback")
    if in_progress:
        bits.append(f"{len(in_progress)} in progress")
    if in_review:
        bits.append(f"{len(in_review)} in review")
    bits.append(f"{counts.get('done', 0)} done, {counts.get('todo', 0)} queued")
    speech = "Board: " + "; ".join(bits) + "."

    return {
        "ok": True,
        "counts": counts,
        "in_progress": in_progress,
        "in_review": in_review,
        "blocked": blocked,
        "recently_done": done,
        "needs_attention": len(blocked),
        "speech": speech,
    }
