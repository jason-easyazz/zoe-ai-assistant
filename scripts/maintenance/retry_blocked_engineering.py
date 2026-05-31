#!/usr/bin/env python3
"""Retry blocked engineering_tasks workflows (401 bucket by default)."""
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


def _bucket_match(blocker: str | None, bucket: str) -> bool:
    text = (blocker or "").lower()
    if bucket == "http401":
        return (
            "401 unauthorized" in text
            or "client error '401" in text
            or ("401" in text and "127.0.0.1:8642" in text)
        )
    if bucket == "multica_auth":
        return (
            "multica not configured" in text
            or "missing auth to multica" in text
            or "multica board access is unavailable" in text
        )
    if bucket == "dirty_tree":
        return "dirty" in text and "tree" in text
    if bucket == "401":
        # Legacy alias: Hermes HTTP 401 only (not Multica MCP auth).
        return _bucket_match(blocker, "http401")
    return True


async def run(args: argparse.Namespace) -> int:
    from runtime_env import bootstrap_runtime_env

    bootstrap_runtime_env()
    from db_pool import close_pool, get_db_ctx, init_pool
    from engineering_workflow import retry_engineering_task

    try:
        await init_pool()
    except Exception as exc:
        print(f"Database pool initialisation failed: {exc}", file=sys.stderr)
        return 1

    try:
        if args.bucket == "dirty_tree" and not args.force:
            print(
                "dirty_tree bucket: list-only (use --force to attempt retry after cleaning repo)"
            )
            list_only = True
        else:
            list_only = False

        async with get_db_ctx() as db:
            rows = await db.fetch(
                """SELECT id, multica_issue_id, blocker_reason, phase
                   FROM engineering_tasks
                   WHERE phase = 'blocked'
                   ORDER BY updated_at ASC"""
            )

        selected = []
        for row in rows:
            record = dict(row)
            blocker = record.get("blocker_reason")
            if not _bucket_match(blocker, args.bucket):
                continue
            selected.append(record)

        if not selected:
            print(f"No blocked workflows match bucket={args.bucket!r}")
            return 0

        print(f"Found {len(selected)} blocked workflow(s) for bucket={args.bucket!r}")
        preview = selected[: args.limit] if args.limit else selected
        for row in preview:
            wid = row["id"]
            ref = row.get("multica_issue_id") or "(no multica)"
            blocker = (row.get("blocker_reason") or "")[:120]
            print(f"  - {wid} multica={ref} blocker={blocker!r}")

        if list_only:
            return 0

        to_run = selected[: args.limit] if args.limit else selected
        if args.dry_run:
            print(f"Dry-run: would retry {len(to_run)} workflow(s)")
            return 0

        ok = 0
        for row in to_run:
            wid = row["id"]
            try:
                updated = await retry_engineering_task(wid)
                phase = updated.get("phase")
                bt = updated.get("background_task_id")
                print(f"OK {wid} -> phase={phase} background_task_id={bt}")
                ok += 1
            except Exception as exc:
                print(f"FAIL {wid}: {exc}", file=sys.stderr)
        print(f"Retried {ok}/{len(to_run)}")
        return 0 if ok == len(to_run) else 1
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print targets only")
    parser.add_argument("--limit", type=int, default=5, help="Max workflows to retry")
    parser.add_argument(
        "--bucket",
        choices=("http401", "401", "dirty_tree", "multica_auth", "all"),
        default="http401",
        help="Filter bucket: http401, multica_auth, dirty_tree, all (401=http401 alias)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow retry for dirty_tree (not recommended)",
    )
    args = parser.parse_args()
    _load_dotenv()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
