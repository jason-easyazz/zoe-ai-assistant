#!/usr/bin/env python3
"""Start engineering workflows for Hermes-assigned Multica todos (controlled batch)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))


def _load_dotenv() -> None:
    for path in (
        ROOT / "services" / "zoe-data" / ".env",
        ROOT / ".env",
        Path.home() / ".hermes" / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


async def run(args: argparse.Namespace) -> int:
    from runtime_env import bootstrap_runtime_env

    bootstrap_runtime_env()
    from db_pool import close_pool, get_db_ctx, init_pool
    from engineering_workflow import create_and_start_engineering_task
    from multica_client import get_engineering_multica_agent_id, get_multica_client

    try:
        await init_pool()
    except Exception as exc:
        print(f"Database pool initialisation failed: {exc}", file=sys.stderr)
        return 1

    hermes_id = get_engineering_multica_agent_id()
    client = get_multica_client()
    if not client.is_configured():
        print("Multica not configured in Zoe env", file=sys.stderr)
        return 1

    try:
        candidates: list[dict] = []
        for status in ("todo", "in_progress"):
            for issue in await client.list_issues(status=status) or []:
                if str(issue.get("assignee_id") or "") != hermes_id:
                    continue
                title = issue.get("title") or issue.get("identifier") or ""
                if title.lower().startswith("autopilot:"):
                    continue
                candidates.append(issue)

        if not candidates:
            print("No Hermes-assigned open issues to dispatch")
            return 0

        print(f"Found {len(candidates)} Hermes-assigned open issue(s)")
        dispatched = 0
        for issue in candidates:
            if dispatched >= args.limit:
                break
            issue_id = str(issue.get("id") or "")
            if not issue_id:
                continue
            async with get_db_ctx() as db:
                existing = await db.fetchrow(
                    """SELECT id, phase FROM engineering_tasks
                       WHERE multica_issue_id=$1 AND phase NOT IN ('done', 'cancelled')
                       ORDER BY updated_at DESC LIMIT 1""",
                    issue_id,
                )
            if existing and existing.get("phase") in (
                "queued",
                "hermes_running",
                "pr_open",
                "greptile_wait",
                "fixing",
                "blocked",
                "ready_for_human",
            ):
                ident = issue.get("identifier") or issue_id
                print(f"SKIP {ident}: workflow {existing['id']} phase={existing['phase']}")
                continue

            title = issue.get("title") or issue.get("identifier") or "Multica engineering task"
            description = issue.get("description") or ""
            ident = issue.get("identifier") or issue_id
            if args.dry_run:
                print(f"DRY-RUN would dispatch {ident}: {title[:60]}")
                dispatched += 1
                continue

            workflow = await create_and_start_engineering_task(
                user_id="family-admin",
                title=title,
                task=(
                    f"Work this Hermes-assigned Multica issue.\n\n"
                    f"Title: {title}\n\n{description}"
                ),
                source="board_dispatch_batch",
                source_id=issue_id,
                multica_issue_id=issue_id,
                idempotency_key=f"multica:{issue_id}",
            )
            print(f"OK {ident} -> workflow {workflow.get('id')} phase={workflow.get('phase')}")
            dispatched += 1

        print(f"Dispatched {dispatched} (limit={args.limit})")
        return 0
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=1, help="Max new workflows per run (default 1)")
    args = parser.parse_args()
    _load_dotenv()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
