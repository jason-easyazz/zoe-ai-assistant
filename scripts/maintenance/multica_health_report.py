#!/usr/bin/env python3
"""Report whether Zoe can use Multica as the engineering ticket source of truth."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))


async def run(*, ensure_shape: bool = False) -> dict:
    from multica_client import get_engineering_multica_agent_id, get_multica_client

    client = get_multica_client()
    report: dict = {
        "configured": client.is_configured(),
        "base_url": bool(os.environ.get("MULTICA_BASE_URL")),
        "workspace_id": bool(os.environ.get("MULTICA_WORKSPACE_ID")),
        "api_token": bool(os.environ.get("MULTICA_API_TOKEN")),
        "webhook_secret": bool(os.environ.get("MULTICA_WEBHOOK_SECRET")),
        "hermes_agent_id": get_engineering_multica_agent_id(),
        "checks": {},
    }
    if not client.is_configured():
        report["ok"] = False
        return report

    issues = await client.list_issues(status="todo")
    labels = await client.list_labels()
    projects = await client.list_projects()
    report["checks"]["issues_api"] = isinstance(issues, list)
    report["checks"]["labels_api"] = isinstance(labels, list)
    report["checks"]["projects_api"] = isinstance(projects, list)

    required_labels = [
        "needs-split",
        "blocked-external",
        "in-review",
        "greptile",
        "ci-failed",
        "audit-only",
        "user-feedback",
        "harness-fix",
        "operator-task",
    ]
    if ensure_shape:
        for label in required_labels:
            await client.ensure_label(label)
        labels = await client.list_labels()
    present_labels = {str(label.get("name") or "").lower() for label in labels}
    report["checks"]["required_labels"] = {
        label: label.lower() in present_labels for label in required_labels
    }
    report["ok"] = (
        report["configured"]
        and report["checks"]["issues_api"]
        and report["checks"]["labels_api"]
        and report["checks"]["projects_api"]
        and all(report["checks"]["required_labels"].values())
        and report["webhook_secret"]
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Zoe/Multica product surface health.")
    parser.add_argument("--ensure-shape", action="store_true", help="Create missing canonical labels.")
    args = parser.parse_args(argv)
    report = asyncio.run(run(ensure_shape=args.ensure_shape))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
