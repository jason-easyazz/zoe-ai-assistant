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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))


def _run_guard(pr: int, *, merge_when_ready: bool) -> dict:
    guard = Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "greploop_guard.py"
    cmd = [sys.executable, str(guard), "--pr", str(pr)]
    if merge_when_ready:
        cmd.append("--merge-when-ready")
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    output = (proc.stdout or proc.stderr or "").strip()
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = {"ok": False, "state": "ERROR", "output": output}
    parsed.setdefault("exit_code", proc.returncode)
    return parsed


async def _record_issue_progress(issue_id: str | None, result: dict) -> None:
    if not issue_id:
        return
    from multica_client import get_multica_client

    client = get_multica_client()
    if not client.is_configured():
        return
    state = str(result.get("state") or "")
    status = "done" if state == "MERGED" else ("blocked" if state in {"BLOCKED", "ERROR"} else "in_review")
    blocker = None if status != "blocked" else str(result.get("reason") or result.get("output") or state)
    await client.record_progress(
        issue_id,
        phase="closeout",
        greptile_status=str(result.get("greptile") or result.get("state") or ""),
        merge_sha=result.get("merge_commit"),
        blocker=blocker,
        status=status,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded Zoe PR maintenance.")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--issue-id")
    parser.add_argument("--merge-when-ready", action="store_true")
    args = parser.parse_args(argv)

    result = _run_guard(args.pr, merge_when_ready=args.merge_when_ready)
    asyncio.run(_record_issue_progress(args.issue_id, result))
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
