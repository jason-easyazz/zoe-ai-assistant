"""Emit Multica-shaped webhook events to Zoe's board webhook receiver.

Zoe implements ``POST /api/agent/board/webhook`` for ``issue.assigned``,
``issue.status_changed``, and ``issue.created``. Stock Multica (ghcr backend)
does not register outbound issue webhooks via REST; it only has *inbound*
autopilot webhooks (``/api/webhooks/autopilots/{token}``).

Until Multica is rebuilt with ``zoe_webhook_listener`` (see the local Multica
source tree), this module is the **outbound bridge**: the poll loop detects board
changes and POSTs authenticated events to the Zoe receiver so dispatch stays
on one code path (``executor_registry`` → Kanban).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def webhook_target_url() -> str:
    return os.environ.get(
        "MULTICA_WEBHOOK_TARGET_URL",
        "http://127.0.0.1:8000/api/agent/board/webhook",
    ).strip()


def webhook_secret() -> str:
    return os.environ.get("MULTICA_WEBHOOK_SECRET", "").strip()


def is_configured() -> bool:
    return bool(webhook_secret())


async def emit_event(event: str, issue: dict[str, Any]) -> dict[str, Any]:
    """POST one webhook event to the Zoe board receiver."""
    secret = webhook_secret()
    if not secret:
        return {"ok": False, "reason": "MULTICA_WEBHOOK_SECRET unset", "skipped": True}
    url = webhook_target_url()
    payload = {"event": event, "issue": issue}
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "X-Multica-Webhook-Token": secret,
    }
    try:
        # Keep below the 30s poll-loop interval so one slow loopback POST can't
        # consume a whole cycle (compounds when ZOE_MULTICA_POLL_DISPATCH_LIMIT > 1).
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            body: Any = {}
            try:
                body = resp.json()
            except Exception:
                body = {"raw": (resp.text or "")[:500]}
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "reason": f"HTTP {resp.status_code}",
                    "body": body,
                }
            return {"ok": True, "status_code": resp.status_code, "body": body}
    except Exception as exc:
        logger.warning("multica_webhook_emitter: POST %s failed: %s", event, exc)
        return {"ok": False, "reason": str(exc)}


async def emit_issue_assigned(issue: dict[str, Any]) -> dict[str, Any]:
    return await emit_event("issue.assigned", issue)


async def emit_issue_status_changed(issue: dict[str, Any]) -> dict[str, Any]:
    return await emit_event("issue.status_changed", issue)
