"""
Web Push Notifications via VAPID.
Generates VAPID keys on first run, stores subscriptions, sends push messages.
"""
import json
import os
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/push", tags=["push"])

VAPID_KEY_PATH = Path(__file__).parent.parent / "data" / "vapid_keys.json"
VAPID_CLAIMS = {"sub": "mailto:admin@zoe.local"}

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
async def get_vapid_public_key():
    keys = _get_vapid_keys()
    if not keys:
        return {"error": "VAPID keys not configured"}
    return {"public_key": keys["public_key"]}


@router.post("/subscribe")
async def subscribe(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    subscription = body.get("subscription")
    if not subscription:
        return {"error": "No subscription data"}

    user_id = user["user_id"]
    async for db in get_db():
        await db.execute(
            """INSERT OR REPLACE INTO push_subscriptions
               (user_id, endpoint, keys_p256dh, keys_auth, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
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
async def unsubscribe(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    endpoint = body.get("endpoint", "")
    async for db in get_db():
        await db.execute(
            "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
            (user["user_id"], endpoint),
        )
        await db.commit()
    return {"status": "unsubscribed"}


async def send_push_to_user(user_id: str, title: str, body: str, url: str = "/"):
    """Send a push notification to all of a user's subscriptions."""
    keys = _get_vapid_keys()
    if not keys:
        return

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed")
        return

    async for db in get_db():
        rows = await db.execute_fetchall(
            "SELECT endpoint, keys_p256dh, keys_auth FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        )
        for row in rows:
            sub_info = {
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["keys_p256dh"], "auth": row["keys_auth"]},
            }
            payload = json.dumps({"title": title, "body": body, "url": url})
            try:
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=keys["private_key"],
                    vapid_claims=VAPID_CLAIMS,
                )
            except WebPushException as e:
                logger.warning(f"Push failed for {row['endpoint'][:40]}...: {e}")
                if "410" in str(e) or "404" in str(e):
                    await db.execute(
                        "DELETE FROM push_subscriptions WHERE endpoint = ?",
                        (row["endpoint"],),
                    )
                    await db.commit()
