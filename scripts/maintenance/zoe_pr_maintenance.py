#!/usr/bin/env python3
"""Bounded PR maintenance phase for Zoe engineering runs.

This command is intentionally a thin gate around the existing Greploop guard:
it checks review/CI readiness, records Multica progress, and only merges when
the guard reports readiness. It does not broaden scope beyond the PR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))


def _run_guard(pr: int, *, merge_when_ready: bool) -> dict:
    from greploop_guard import merge_pr_when_ready as _merge_pr_when_ready
    from greploop_guard import run_guard_once

    async def _run() -> dict:
        if merge_when_ready:
            return await _merge_pr_when_ready(pr)
        # Maintenance status checks should surface the next bounded repair packet
        # without launching a cheap-model repair agent from this wrapper.
        return await run_guard_once(pr, packet_only=True)

    try:
        parsed = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - maintenance must report guard failures as JSON.
        parsed = {"ok": False, "state": "ERROR", "output": str(exc)}
    parsed.setdefault("exit_code", 0 if parsed.get("ok") else 1)
    return parsed


async def _record_issue_progress(issue_id: str | None, result: dict) -> dict:
    if not issue_id:
        return result
    from multica_client import get_multica_client

    client = get_multica_client()
    if not client.is_configured():
        return result
    state = str(result.get("state") or "")
    status = "done" if state == "MERGED" else ("blocked" if state in {"BLOCKED", "ERROR"} else "in_review")
    blocker = None if status != "blocked" else str(result.get("reason") or result.get("output") or state)
    await client.record_progress(
        issue_id,
        phase="closeout",
        greptile_status=str(result.get("greptile") or result.get("state") or ""),
        merge_sha=result.get("merge_commit"),
        blocker=blocker,
        clear_blocker=status in {"done", "in_review"},
        status=status,
    )
    if status == "done":
        from pipeline_store import complete_pipeline_after_external_merge

        try:
            complete_pipeline_after_external_merge(
                f"multica:{issue_id}",
                pr_url=result.get("pr_url"),
                merge_sha=result.get("merge_commit"),
                greptile_status=str(result.get("greptile") or result.get("state") or ""),
                reason="PR maintenance recorded merged PR",
            )
            result["journal_ok"] = True
        except Exception as exc:  # noqa: BLE001 - keep maintenance output machine-readable.
            result["journal_ok"] = False
            result["journal_error"] = str(exc)
            result["exit_code"] = 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded Zoe PR maintenance.")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--issue-id")
    parser.add_argument("--merge-when-ready", action="store_true")
    args = parser.parse_args(argv)

    result = _run_guard(args.pr, merge_when_ready=args.merge_when_ready)
    try:
        result = asyncio.run(_record_issue_progress(args.issue_id, result))
    except Exception as exc:  # noqa: BLE001 - command should print JSON even on progress failures.
        result["progress_error"] = str(exc)
        result["exit_code"] = 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") and not result.get("progress_error") and not result.get("journal_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
