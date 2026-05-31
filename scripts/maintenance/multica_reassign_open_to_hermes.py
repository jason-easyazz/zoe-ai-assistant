#!/usr/bin/env python3
"""Reassign open Multica issues from Self-Improvement Agent to Hermes."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

OPEN_STATUSES = ("todo", "in_progress", "in_review")


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
    from multica_client import (
        get_engineering_multica_agent_id,
        get_multica_client,
        get_self_improvement_multica_agent_id,
    )

    client = get_multica_client()
    if not client.is_configured():
        print("Multica not configured", file=sys.stderr)
        return 1

    from_id = get_self_improvement_multica_agent_id()
    to_id = get_engineering_multica_agent_id()
    secret = os.environ.get("MULTICA_WEBHOOK_SECRET", "").strip()
    if secret:
        print(
            "WARNING: MULTICA_WEBHOOK_SECRET is set — mass reassignment may trigger "
            "webhook dispatch if Multica sends issue.assigned events."
        )
    else:
        print("Preflight: MULTICA_WEBHOOK_SECRET unset — webhook auto-dispatch likely off")

    print(f"From assignee: {from_id}")
    print(f"To assignee:   {to_id}")

    targets: list[dict] = []
    for status in OPEN_STATUSES:
        issues = await client.list_issues(status=status)
        for issue in issues or []:
            if str(issue.get("assignee_id") or "") != from_id:
                continue
            targets.append(issue)

    if args.max:
        targets = targets[: args.max]

    print(f"Would reassign {len(targets)} issue(s) across {OPEN_STATUSES}")
    for issue in targets[:20]:
        ident = issue.get("identifier") or issue.get("id")
        print(f"  - {ident}: {issue.get('title', '')[:60]}")
    if len(targets) > 20:
        print(f"  ... and {len(targets) - 20} more")

    if args.dry_run or not args.execute:
        if not args.execute:
            print("Dry-run (pass --execute to apply)")
        return 0

    updated = 0
    for issue in targets:
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            continue
        result = await client.update_issue(
            issue_id,
            assignee_id=to_id,
            assignee_type="agent",
        )
        if result.get("error"):
            print(f"FAIL {issue.get('identifier') or issue_id}: {result['error']}", file=sys.stderr)
        else:
            updated += 1
            print(f"OK {issue.get('identifier') or issue_id}")
        if args.sleep_ms > 0:
            await asyncio.sleep(args.sleep_ms / 1000.0)

    print(f"Reassigned {updated}/{len(targets)}")
    return 0 if updated == len(targets) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List targets only")
    parser.add_argument("--execute", action="store_true", help="Apply reassignments")
    parser.add_argument("--max", type=int, default=0, help="Pilot cap (0 = all)")
    parser.add_argument("--sleep-ms", type=int, default=200, help="Delay between updates")
    args = parser.parse_args()
    if args.execute and args.dry_run:
        print("Use either --dry-run or --execute, not both", file=sys.stderr)
        return 2
    if not args.execute:
        args.dry_run = True
    _load_dotenv()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
