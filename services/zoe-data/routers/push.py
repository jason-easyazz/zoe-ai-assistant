"""
Web Push Notifications via VAPID.
Generates VAPID keys on first run, stores subscriptions, sends push messages.
"""
import asyncio
import json
import os
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/push", tags=["push"])

VAPID_KEY_PATH = Path(__file__).parent.parent / "data" / "vapid_keys.json"
VAPID_CLAIMS = {"sub": "https://zoe.the411.life"}

_vapid_keys = None


def _get_vapid_keys():
    global _vapid_keys
    if _vapid_keys:
        return _vapid_keys

    if VAPID_KEY_PATH.exists():
        _vapid_keys = json.loads(VAPID_KEY_PATH.read_text())
        return _vapid_keys

    try:
        from py_vapid import Vapid
        import base64
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        vapid = Vapid()
        vapid.generate_keys()
        pub_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        priv_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        _vapid_keys = {
            "public_key": base64.urlsafe_b64encode(pub_raw).decode().rstrip("="),
            "private_key": base64.urlsafe_b64encode(priv_raw).decode().rstrip("="),
        }
        VAPID_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        VAPID_KEY_PATH.write_text(json.dumps(_vapid_keys, indent=2))
        logger.info("Generated new VAPID keys")
        return _vapid_keys
    except Exception as e:
        logger.error(f"Failed to generate VAPID keys: {e}")
        return None


@router.get("/vapid-public-key")
async def get_vapid_public_key(user: dict = Depends(get_current_user), db=Depends(get_db)):
    await require_feature_access(db, user, feature="push", action="read")
    keys = _get_vapid_keys()
    if not keys:
        return {"error": "VAPID keys not configured"}
    return {"public_key": keys["public_key"]}


@router.post("/subscribe")
async def subscribe(request: Request, user: dict = Depends(get_current_user), db=Depends(get_db)):
    await require_feature_access(db, user, feature="push", action="subscribe")
    body = await request.json()
    subscription = body.get("subscription")
    if not subscription:
        return {"error": "No subscription data"}

    user_id = user["user_id"]
    await db.execute(
        """INSERT INTO push_subscriptions
           (user_id, endpoint, keys_p256dh, keys_auth, created_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT (user_id, endpoint) DO UPDATE SET
               keys_p256dh = EXCLUDED.keys_p256dh,
               keys_auth = EXCLUDED.keys_auth""",
        (
            user_id,
            subscription.get("endpoint", ""),
            subscription.get("keys", {}).get("p256dh", ""),
            subscription.get("keys", {}).get("auth", ""),
        ),
    )
    await db.commit()
    return {"status": "subscribed"}


@router.delete("/subscribe")
async def unsubscribe(request: Request, user: dict = Depends(get_current_user), db=Depends(get_db)):
    await require_feature_access(db, user, feature="push", action="unsubscribe")
    body = await request.json()
    endpoint = body.get("endpoint", "")
    await db.execute(
        "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
        (user["user_id"], endpoint),
    )
    await db.commit()
    return {"status": "unsubscribed"}


async def send_push_to_user(
    user_id: str,
    message: str = "",
    title: str = "Zoe",
    body: str = "",
    url: str = "/",
    extra: dict | None = None,
):
    """
    Send a push notification to all of a user's subscriptions.

    ``message`` is a convenience arg: if supplied and ``body`` is empty, it
    becomes the notification body.  ``extra`` dict is merged into the payload
    (e.g. ``{"url": "/chat.html?p=<id>"}`` for proactive deep-linking).
    """
    if message and not body:
        body = message
    if extra and "url" in extra:
        url = extra["url"]

    keys = _get_vapid_keys()
    if not keys:
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed")
        return 0

    payload_data: dict = {"title": title, "body": body, "url": url}
    if extra:
        payload_data.update({k: v for k, v in extra.items() if k != "url"})

    # Step 1: short-lived pooled connection — read subscriptions, then release
    # it BEFORE making any outbound push HTTP calls. webpush() is synchronous
    # (requests-based); holding a pooled DB connection across it — and calling
    # it directly on the event loop instead of via an executor — would block
    # every other request/WebSocket/scheduler callback in the process for the
    # duration of each push (potentially unbounded without an explicit timeout).
    rows = []
    async for db in get_db():
        async with db.execute(
            "SELECT endpoint, keys_p256dh, keys_auth FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()

    # Step 2: NO pooled connection held here. Each webpush() call runs in a
    # thread executor so it can't block the event loop.
    sent = 0
    dead_endpoints: list[str] = []
    loop = asyncio.get_running_loop()
    for row in rows:
        sub_info = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["keys_p256dh"], "auth": row["keys_auth"]},
        }
        payload = json.dumps(payload_data)
        try:
            await loop.run_in_executor(
                None,
                lambda sub_info=sub_info, payload=payload: webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=keys["private_key"],
                    vapid_claims=VAPID_CLAIMS.copy(),
                    content_encoding="aes128gcm",
                    timeout=10,
                ),
            )
            sent += 1
        except WebPushException as e:
            logger.warning(f"Push failed for {row['endpoint'][:40]}...: {e}")
            # Self-prune PERMANENTLY dead subscriptions. Two classes:
            #   * 410/404 — the push service says the subscription is gone.
            #   * malformed crypto keys ("Invalid p256dh key" / "Invalid auth key") —
            #     raised client-side BEFORE any HTTP status, so the old
            #     410/404-only check never matched and the row was retried on
            #     EVERY send forever (a test.example.com junk sub did exactly
            #     this, failing every brief since June). Bad keys cannot heal;
            #     transient network errors carry neither marker and are kept.
            msg = str(e).lower()
            if (
                "410" in msg
                or "404" in msg
                or "invalid p256dh" in msg
                or "invalid auth key" in msg
            ):
                dead_endpoints.append(row["endpoint"])

    # Step 3: fresh short-lived connection only if there's cleanup to do.
    # Scope by user_id too: uniqueness is (user_id, endpoint), so the same
    # endpoint can belong to another user whose subscription is still valid.
    if dead_endpoints:
        async for db in get_db():
            for endpoint in dead_endpoints:
                await db.execute(
                    "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
                    (user_id, endpoint),
                )
            await db.commit()
    return sent
