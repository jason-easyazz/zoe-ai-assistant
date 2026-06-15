#!/usr/bin/env python3
"""Sync Hermes-assigned Multica issues into active Hermes Kanban phases (cheap DeepSeek).

Replaces the old engineering_workflow/background_runner (Sonnet) dispatch path.
For each Hermes-assigned ``todo`` Multica issue whose current ready phase has no
Kanban row yet, dispatch exactly that one phase via the executor registry, then
set the Multica issue to ``in_progress``.

Idempotent: phases are keyed ``multica:{issue_id}:<phase>`` so re-runs are safe.
"""
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
    from executor_registry import poll_ref
    from multica_client import get_engineering_multica_agent_id, get_multica_client
    from multica_dispatch_control import dispatch_is_paused, pause_reason
    from multica_poll_dispatch import chain_needs_dispatch
    from multica_webhook_emitter import emit_issue_assigned, is_configured as webhooks_configured

    hermes_id = get_engineering_multica_agent_id()
    client = get_multica_client()
    if not client.is_configured():
        print("Multica not configured in Zoe env", file=sys.stderr)
        return 1
    if dispatch_is_paused() and not args.dry_run:
        print(f"Dispatch paused: {pause_reason()}", file=sys.stderr)
        return 3

    candidates: list[dict] = []
    for issue in await client.list_issues(status="todo") or []:
        if str(issue.get("assignee_id") or "") != str(hermes_id):
            continue
        title = (issue.get("title") or issue.get("identifier") or "")
        if title.lower().startswith("autopilot:"):
            continue
        candidates.append(issue)

    if not candidates:
        print("No Hermes-assigned todo issues to dispatch")
        return 0

    # Live dispatch routes through the Zoe board webhook receiver; the secret is
    # a batch-wide prerequisite. Dry runs should still work without webhook auth.
    if not args.dry_run and not webhooks_configured():
        print(
            "ERROR: MULTICA_WEBHOOK_SECRET unset — set it in .env so dispatch uses /api/agent/board/webhook",
            file=sys.stderr,
        )
        return 2

    print(f"Found {len(candidates)} Hermes-assigned todo issue(s)")
    dispatched = 0
    for issue in candidates:
        if dispatched >= args.limit:
            break
        issue_id = str(issue.get("id") or "")
        ident = issue.get("identifier") or issue_id
        if not issue_id:
            continue

        # Isolate each candidate: a poll/webhook failure on one issue must not
        # abort the whole batch and skip the remaining candidates. Mirrors the
        # outer try/except that guards board_approve and the Multica webhook handler.
        try:
            existing = await poll_ref(f"multica:{issue_id}", issue=issue)
            if not chain_needs_dispatch(existing):
                print(f"SKIP {ident}: chain status={existing.get('status')}")
                continue

            if args.dry_run:
                print(f"DRY-RUN would dispatch {ident}: {(issue.get('title') or '')[:60]}")
                dispatched += 1
                continue

            result = await emit_issue_assigned(issue)
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not abort the batch
            print(f"ERROR {ident}: dispatch failed: {exc}", file=sys.stderr)
            continue

        body = result.get("body") or {}
        dispatch = body.get("dispatch") if isinstance(body, dict) else None
        if not result.get("ok"):
            print(f"SKIP {ident}: webhook emit failed: {result.get('reason')}")
            continue
        # Receiver returns {"ok": True} with no "dispatched" key if its own
        # dispatch raised; treat any non-truthy "dispatched" as "not dispatched"
        # so we never mark the issue in_progress without a journaled phase task.
        if not (isinstance(body, dict) and body.get("dispatched")):
            print(f"SKIP {ident}: {(body or {}).get('reason', 'webhook did not dispatch')}")
            continue
        try:
            await client.update_issue(issue_id, status="in_progress")
        except Exception as exc:  # noqa: BLE001 - best-effort status sync
            print(f"WARN {ident}: could not set in_progress: {exc}", file=sys.stderr)
        chain = (dispatch or {}).get("chain") if isinstance(dispatch, dict) else None
        print(f"OK {ident} -> webhook dispatch chain={chain}")
        dispatched += 1

    print(f"Dispatched {dispatched} (limit={args.limit})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--limit", type=int, default=1, help="Max new chains per run (default 1; respect kanban.max_in_progress)"
    )
    args = parser.parse_args()
    _load_dotenv()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
