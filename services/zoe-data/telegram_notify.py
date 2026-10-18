"""telegram_notify - zoe-data's client for the Telegram bot's loopback ``POST /internal/notify`` route.

The route lives in the live bot (``labs/flue-zoe-telegram-2x/src/notify.ts``, PR #2108): loopback only,
``Authorization: Bearer <ZOE_INTERNAL_TOKEN>``, and the recipient must be a LINKED member (the bot asks zoe-data's
resolver), so this client cannot message an arbitrary chat even if a caller passed one. One process holds the bot
token; this client never reads one (design: docs/architecture/communication-preferences.md section 5.5, owner decision 6).

Same contract and env as ``auth_handoff``'s "send to my phone" (``ZOE_TELEGRAM_NOTIFY_URL``, default below, ``off``
disables; no ``ZOE_INTERNAL_TOKEN`` = unavailable). Body ``{telegram_id, text, url?, label?}``; text <= 1000 chars.

``send`` never raises and never logs the token or the text. Its outcome says whether a retry is SAFE:
  * ``sent``      - HTTP 200: delivered.
  * ``retry``     - definitely not delivered (could not connect, 401/429/5xx/502 "telegram refused"): safe to retry.
  * ``refused``   - will never succeed as asked (400 bad request, 403 not a linked member): do not retry.
  * ``uncertain`` - the request may have reached Telegram (a timeout after it was sent): never re-send it
                    (no provider offers an idempotency key, so the rule is at most once, never silently lost).
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from typed_env import env_str

logger = logging.getLogger(__name__)

DEFAULT_NOTIFY_URL = "http://127.0.0.1:3582/internal/notify"
MAX_TEXT = 1000  # the route's MAX_NOTIFY_TEXT
MAX_LABEL = 64

Outcome = Literal["sent", "retry", "refused", "uncertain"]


def notify_url() -> str:
    """The bot's loopback notify route, or "" when it cannot be used (disabled, or no shared secret)."""
    raw = env_str("ZOE_TELEGRAM_NOTIFY_URL")
    if raw.lower() in ("off", "0", "false", "none") or not env_str("ZOE_INTERNAL_TOKEN"):
        return ""
    return raw or DEFAULT_NOTIFY_URL


def available() -> bool:
    return bool(notify_url())


async def send(telegram_id: str, text: str, url: Optional[str] = None, label: Optional[str] = None,
               *, client=None) -> Outcome:
    """POST one message to the bot. ``client`` (an ``httpx.AsyncClient``) is injectable for tests."""
    target = notify_url()
    if not target:
        return "retry"  # not configured yet: nothing was sent, so a later attempt is safe
    if not (isinstance(telegram_id, str) and telegram_id.isdigit()) or not text.strip():
        return "refused"
    import httpx

    body: dict = {"telegram_id": telegram_id, "text": text[:MAX_TEXT]}
    if url:
        body["url"] = url
        body["label"] = (label or "Open")[:MAX_LABEL]
    headers = {"Authorization": f"Bearer {env_str('ZOE_INTERNAL_TOKEN')}"}
    try:
        if client is not None:
            r = await client.post(target, json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as c:
                r = await c.post(target, json=body, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return "retry"  # the request never left: the bot is down or restarting
    except Exception as exc:  # noqa: BLE001 - a read timeout etc.: the bot may already have sent it
        logger.info("telegram_notify: outcome unknown (%s)", type(exc).__name__)
        return "uncertain"
    if r.status_code == 200:
        return "sent"
    if r.status_code in (400, 403):
        logger.info("telegram_notify: the bot refused the message (HTTP %s)", r.status_code)
        return "refused"
    logger.info("telegram_notify: the bot could not send now (HTTP %s)", r.status_code)
    return "retry"
