"""Board review surface — read-only summary of Zoe's autonomous engineering work.

Backs the "Zoe's Work" interface card so the operator can see, at a glance, what
the autonomous board runner has tackled: what is in flight, what shipped (with
PR links to review), and what is blocked and needs feedback.

Read-only. Reads Multica's own DB (issue + activity_log) through the shared
executor pool — the same DB the board runner writes progress into. No writes,
no side effects; safe to poll from the panel.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/board", tags=["board"])

_ACTIVE = ("todo", "in_progress", "in_review", "blocked", "done", "backlog", "cancelled")


def _pr_from_details(details) -> str | None:
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    return (details or {}).get("pr_url") or None


@router.get("/summary")
async def board_summary(limit_done: int = 10) -> dict:
    """Counts by status + the actionable lists (in-flight, in-review, blocked,
    recently done) with PR links + block reasons pulled from the runner's
    activity_log trail."""
    from executors.executor_queue_backend import ensure_executor_identity, get_pool

    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001 - Multica unreachable is not fatal to the panel
        logger.warning("board_summary: Multica unreachable: %s", exc)
        return {"ok": False, "detail": "board backend unreachable", "counts": {}}

    async with pool.acquire() as conn:
        identity = await ensure_executor_identity(conn)
        ws = identity["workspace_id"]

        counts_rows = await conn.fetch(
            "SELECT status, count(*)::int AS n FROM issue WHERE workspace_id=$1::uuid GROUP BY status",
            ws,
        )
        counts = {r["status"]: r["n"] for r in counts_rows}

        # For each issue, its latest activity_log entry (holds pr_url + reason).
        rows = await conn.fetch(
            """SELECT i.number, i.title, i.status, i.updated_at,
                      a.action, a.details, a.created_at AS act_at
                 FROM issue i
                 LEFT JOIN LATERAL (
                     SELECT action, details, created_at FROM activity_log
                      WHERE issue_id = i.id ORDER BY created_at DESC LIMIT 1
                 ) a ON true
                WHERE i.workspace_id=$1::uuid
                  AND i.status = ANY($2::text[])
                ORDER BY i.updated_at DESC""",
            ws, list(_ACTIVE),
        )

    def _entry(r, *, with_reason=False):
        d = r["details"]
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except json.JSONDecodeError:
                d = {}
        e = {"number": r["number"], "title": r["title"], "pr_url": (d or {}).get("pr_url")}
        if with_reason:
            e["reason"] = (d or {}).get("reason")
        return e

    in_progress = [_entry(r) for r in rows if r["status"] == "in_progress"]
    in_review = [_entry(r) for r in rows if r["status"] == "in_review"]
    blocked = [_entry(r, with_reason=True) for r in rows if r["status"] == "blocked"]
    done = [_entry(r) for r in rows if r["status"] == "done"][:limit_done]

    return {
        "ok": True,
        "counts": counts,
        "in_progress": in_progress,
        "in_review": in_review,
        "blocked": blocked,
        "recently_done": done,
        "needs_attention": len(blocked),
    }
