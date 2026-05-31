#!/usr/bin/env python3
"""Ensure MULTICA_WEBHOOK_SECRET is set and verify the Zoe board webhook receiver.

Multica does not expose a REST API to register outbound issue webhooks on the
stock ghcr image. Zoe bridges board changes via multica_webhook_emitter.py (poll)
and optionally via Multica's zoe_webhook_listener.go when the backend is rebuilt
with ZOE_BOARD_WEBHOOK_URL + ZOE_BOARD_WEBHOOK_SECRET in docker-compose.modules.yml.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env_paths() -> list[Path]:
    return [
        ROOT / "services" / "zoe-data" / ".env",
        ROOT / ".env",
    ]


def _ensure_secret(dry_run: bool) -> str:
    import os

    existing = os.environ.get("MULTICA_WEBHOOK_SECRET", "").strip()
    if existing:
        return existing
    for path in _env_paths():
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MULTICA_WEBHOOK_SECRET="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    token = secrets.token_urlsafe(32)
    if dry_run:
        print(f"DRY-RUN would append MULTICA_WEBHOOK_SECRET to {ROOT / '.env'}")
        return token
    target = ROOT / ".env"
    line = f"\n# Multica→Zoe board webhook auth (issue.assigned dispatch)\nMULTICA_WEBHOOK_SECRET={token}\n"
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(f"Wrote MULTICA_WEBHOOK_SECRET to {target}")
    return token


async def _probe_webhook(secret: str) -> bool:
    import httpx

    url = "http://127.0.0.1:8000/api/agent/board/webhook"
    payload = {
        "event": "issue.created",
        "issue": {"id": "probe", "identifier": "PROBE-0", "title": "webhook probe"},
    }
    headers = {
        "Authorization": f"Bearer {secret}",
        "X-Multica-Webhook-Token": secret,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            return resp.status_code < 500
    except Exception as exc:
        print(f"Webhook probe failed (is zoe-data up on :8000?): {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--probe", action="store_true", help="POST a harmless issue.created to the receiver")
    args = parser.parse_args()

    secret = _ensure_secret(args.dry_run)
    print("MULTICA_WEBHOOK_SECRET is configured.")
    print("Zoe poll + sync_multica_to_kanban will POST issue.assigned to:")
    print("  http://127.0.0.1:8000/api/agent/board/webhook")
    print("For native Multica push, rebuild multica-backend with zoe_webhook_listener.go and set:")
    print("  ZOE_BOARD_WEBHOOK_URL=http://host.docker.internal:8000/api/agent/board/webhook")
    print("  ZOE_BOARD_WEBHOOK_SECRET=<same value>")

    if args.probe and not args.dry_run:
        import asyncio

        ok = asyncio.run(_probe_webhook(secret))
        if not ok:
            return 1
        print("Webhook probe OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
