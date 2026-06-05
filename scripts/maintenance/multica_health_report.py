#!/usr/bin/env python3
"""Report whether Zoe can use Multica as the engineering ticket source of truth."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))


def _uploads_configured() -> bool:
    if os.environ.get("LOCAL_UPLOAD_DIR") and os.environ.get("LOCAL_UPLOAD_BASE_URL"):
        return True
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.modules.yml"
    try:
        compose = compose_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        'LOCAL_UPLOAD_DIR: "/data/uploads"' in compose
        and 'LOCAL_UPLOAD_BASE_URL: "http://localhost:8080"' in compose
        and "multica-uploads:/data/uploads" in compose
    )


def _oidc_client_id_configured() -> bool:
    return bool(os.environ.get("MULTICA_OIDC_CLIENT_ID"))


async def _probe(url: str, *, headers: dict[str, str] | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        return {
            "ok": 200 <= response.status_code < 300,
            "status": response.status_code,
            "url": url,
        }
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


async def run(*, ensure_shape: bool = False) -> dict:
    from multica_client import get_engineering_multica_agent_id, get_multica_client

    client = get_multica_client()
    report: dict = {
        "configured": client.is_configured(),
        "base_url": bool(os.environ.get("MULTICA_BASE_URL")),
        "workspace_id": bool(os.environ.get("MULTICA_WORKSPACE_ID")),
        "api_token": bool(os.environ.get("MULTICA_API_TOKEN")),
        "webhook_secret": bool(os.environ.get("MULTICA_WEBHOOK_SECRET")),
        "oidc_client_id": _oidc_client_id_configured(),
        "oidc_client_secret": bool(os.environ.get("MULTICA_OIDC_CLIENT_SECRET")),
        "uploads_configured": _uploads_configured(),
        "email_configured": bool(
            os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST")
        ),
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
    base_url = os.environ.get("MULTICA_BASE_URL", "").rstrip("/")
    report["checks"]["backend_health"] = await _probe(f"{base_url}/health")
    report["checks"]["web_direct"] = await _probe(
        os.environ.get("MULTICA_WEB_URL", "http://127.0.0.1:3000")
    )
    report["checks"]["web_proxy"] = await _probe(
        os.environ.get("MULTICA_PROXY_URL", "http://127.0.0.1/multica/")
    )
    report["checks"]["api_proxy"] = await _probe(
        os.environ.get("MULTICA_API_PROXY_HEALTH_URL", "http://127.0.0.1/multica-api/health")
    )
    report["checks"]["oidc_discovery"] = await _probe(
        os.environ.get(
            "MULTICA_OIDC_DISCOVERY_URL",
            "http://127.0.0.1/.well-known/openid-configuration",
        )
    )

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
            await client.ensure_label(label, existing=labels)
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
        and all(
            report["checks"][name]["ok"]
            for name in (
                "backend_health",
                "web_direct",
                "web_proxy",
                "api_proxy",
                "oidc_discovery",
            )
        )
        and report["uploads_configured"]
        and (
            report["email_configured"]
            or os.environ.get("MULTICA_REQUIRE_EMAIL", "false").lower() != "true"
        )
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
