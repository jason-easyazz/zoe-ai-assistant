"""phone_app - the box side of Zoe's own iPhone app, M0: pairing by panel QR and push-token registration (flag-dark).

Design and API contract: ``docs/architecture/zoe-iphone-app.md`` (owner decision 2026-10-18: build Zoe's OWN native Swift app,
not Element X; research ``lane-M-iphone-app.md``, deep dive round 2).

``ZOE_PHONE_APP`` = off (DEFAULT; anything else is off) | on, read per call. Off = every ``/api/phone`` route answers 404, as if
it did not exist. Nothing here sends a push: the APNs sender needs the owner's Apple Developer account and a .p8 key in the vault
(owner steps), and lands in a later phase. This module only lets a paired phone REGISTER where pushes would go.

Pairing (adults only; guests and children get no app, they keep the PWA):

1. A signed-in adult member asks for a pairing code (``start_pairing``). The code is 256 bits, single use, lives
   ``PAIR_TTL_S`` (300 s) in THIS process only (zoe-data is one process; like ``setup_qr``), and only its SHA-256 is kept. The
   reply is a ``zoe-pair:v1:`` payload for the panel / desktop to show as a QR: the box URL, the code and (optionally) the household
   CA's SHA-256 fingerprint, so the app can trust the box's private CA without a profile install.
2. The phone scans it and calls ``complete_pairing`` with the code, a device name and the public half of a Secure Enclave P-256
   key. The code is consumed atomically (a second use, an expired or unknown code all fail the same way). The box stores the
   device and returns a device credential ONCE; only its SHA-256 is stored. The public key is stored for the signed-challenge
   step-up of a later phase; M0 does not enforce it.
3. The app registers its push tokens (alert / voip / pushtotalk / liveactivity, sandbox or production) with that credential.
   A topic must be the configured bundle id with Apple's suffix for the push type, so a token for another app cannot be stored.

The member lists and revokes their own phones; an admin may revoke any. Revoking deletes the device's push tokens. NEVER logs a
code, a credential or a push token.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

FLAG = "ZOE_PHONE_APP"
PAIR_TTL_S = 300
MAX_PENDING_CODES_PER_MEMBER = 5
MAX_DEVICES_PER_MEMBER = 10
PUSH_TYPES = ("alert", "voip", "pushtotalk", "liveactivity")
ENVIRONMENTS = ("sandbox", "production")
# Apple's topic per push type: the bundle id, plus a suffix for VoIP / PushToTalk / Live Activities (lane M section 3.1).
TOPIC_SUFFIX = {"alert": "", "voip": ".voip", "pushtotalk": ".voip-ptt", "liveactivity": ".push-type.liveactivity"}
PLATFORMS = ("ios",)
PAYLOAD_PREFIX = "zoe-pair:v1:"

_HEX = re.compile(r"^[0-9a-fA-F]{64,400}$")
_BUNDLE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def enabled() -> bool:
    from typed_env import env_str

    return env_str("ZOE_PHONE_APP", "off").strip().lower() == "on"  # literal on purpose: tools/audit/flag_inventory.py greps it


def base_url() -> str:
    """The box URL the app talks to (``ZOE_PHONE_APP_BASE_URL``, e.g. the relay's public name). Empty = not configured."""
    from typed_env import env_str

    return env_str("ZOE_PHONE_APP_BASE_URL", "").strip().rstrip("/")


def ca_fingerprint() -> str:
    """SHA-256 (hex) of the household CA the app should trust for ``base_url`` (``ZOE_PHONE_APP_CA_SHA256``); empty when the
    box URL has a publicly trusted certificate."""
    from typed_env import env_str

    fp = env_str("ZOE_PHONE_APP_CA_SHA256", "").strip().replace(":", "").lower()
    return fp if re.fullmatch(r"[0-9a-f]{64}", fp) else ""


def bundle_id() -> str:
    from typed_env import env_str

    b = env_str("ZOE_PHONE_APP_BUNDLE_ID", "").strip()
    return b if _BUNDLE.match(b) else ""


class PhoneAppError(Exception):
    """A refusal with an HTTP status and a short, secret-free reason."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ── pairing codes: in process, hashed, single use ──────────────────────────────────────────────────────────────────────

_CODES: dict[str, tuple[str, float]] = {}   # sha256(code) -> (user_id, expires_at)
_LOCK = threading.Lock()


def _prune(now: float) -> None:
    for h in [h for h, (_, exp) in _CODES.items() if exp <= now]:
        _CODES.pop(h, None)


def reset() -> None:
    with _LOCK:
        _CODES.clear()


def start_pairing(user_id: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """Mint one pairing code for ``user_id`` (the caller has already checked: a signed-in adult member). Returns the QR payload."""
    url = base_url()
    if not url:
        raise PhoneAppError(503, "the phone app's box URL is not configured")
    now = time.time() if now is None else now
    code = secrets.token_urlsafe(32)
    with _LOCK:
        _prune(now)
        if sum(1 for uid, _ in _CODES.values() if uid == user_id) >= MAX_PENDING_CODES_PER_MEMBER:
            raise PhoneAppError(429, "too many pairing codes waiting; use one or wait a few minutes")
        _CODES[_sha(code)] = (user_id, now + PAIR_TTL_S)
    body = {"v": 1, "u": url, "c": code}
    fp = ca_fingerprint()
    if fp:
        body["fp"] = fp
    payload = PAYLOAD_PREFIX + base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    return {"pair_payload": payload, "expires_in_s": PAIR_TTL_S}


def _consume(code: str, now: float) -> Optional[str]:
    with _LOCK:
        _prune(now)
        hit = _CODES.pop(_sha(code or ""), None)
    if hit is None or hit[1] <= now:
        return None
    return hit[0]


def decode_payload(payload: str) -> dict[str, Any]:
    """The app's side of the QR (also used by the tests): ``zoe-pair:v1:`` + base64url JSON."""
    if not payload.startswith(PAYLOAD_PREFIX):
        raise ValueError("not a Zoe pairing payload")
    raw = payload[len(PAYLOAD_PREFIX):]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def valid_public_key(b64: str) -> bool:
    """A P-256 public key as the Secure Enclave exports it: 65-byte X9.63 (0x04 || X || Y) or 91-byte SubjectPublicKeyInfo DER."""
    try:
        raw = base64.b64decode(b64 or "", validate=True)
    except (ValueError, TypeError):
        return False
    return (len(raw) == 65 and raw[0] == 4) or (len(raw) == 91 and raw[:2] == b"\x30\x59")


@dataclass(frozen=True)
class Device:
    id: str
    user_id: str
    name: str
    platform: str


async def complete_pairing(db, *, code: str, device_name: str, platform: str, public_key: str, app_version: str = "",
                           now: Optional[float] = None) -> dict[str, Any]:
    """Consume ``code`` and register the phone. Returns ``{device_id, device_token}``; the token is shown ONCE."""
    now = time.time() if now is None else now
    name = re.sub(r"\s+", " ", str(device_name or "")).strip()[:60] or "iPhone"
    platform = str(platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise PhoneAppError(400, "unsupported platform")
    if not valid_public_key(public_key):
        raise PhoneAppError(400, "the device key is not a P-256 public key")
    user_id = _consume(code, now)
    if not user_id:
        raise PhoneAppError(401, "that pairing code is not valid any more; show a new one")
    async with db.execute("SELECT COUNT(*) FROM phone_devices WHERE user_id = ? AND revoked = 0", (user_id,)) as cur:
        row = await cur.fetchone()
    if row and int(row[0]) >= MAX_DEVICES_PER_MEMBER:
        raise PhoneAppError(409, "too many phones paired; remove one first")
    device_id = uuid.uuid4().hex[:16]
    token = secrets.token_urlsafe(32)
    await db.execute(
        """INSERT INTO phone_devices (id, user_id, name, platform, token_hash, public_key, app_version, created_at, last_seen_at,
                                      revoked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (device_id, user_id, name, platform, _sha(token), public_key, str(app_version or "")[:32], now, now),
    )
    await db.commit()
    return {"device_id": device_id, "device_token": token}


async def device_for_token(db, token: str, *, now: Optional[float] = None) -> Optional[Device]:
    """The live (unrevoked) device holding this credential, or None. Touches ``last_seen_at``."""
    if not token or len(token) > 200:
        return None
    async with db.execute("SELECT id, user_id, name, platform FROM phone_devices WHERE token_hash = ? AND revoked = 0",
                          (_sha(token),)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    await db.execute("UPDATE phone_devices SET last_seen_at = ? WHERE id = ?", (time.time() if now is None else now, row[0]))
    await db.commit()
    return Device(str(row[0]), str(row[1]), str(row[2] or ""), str(row[3] or ""))


def check_push_registration(push_type: str, token: str, environment: str, topic: str) -> None:
    """Shape rules for a push token. Raises ``PhoneAppError(400)``. Pure."""
    if push_type not in PUSH_TYPES:
        raise PhoneAppError(400, "unknown push type")
    if environment not in ENVIRONMENTS:
        raise PhoneAppError(400, "unknown APNs environment")
    if not _HEX.match(token or ""):
        raise PhoneAppError(400, "a push token is hex")
    bundle = bundle_id()
    if not bundle:
        raise PhoneAppError(503, "the app's bundle id is not configured")
    if topic != bundle + TOPIC_SUFFIX[push_type]:
        raise PhoneAppError(400, "that topic is not this app's")


async def register_push(db, device: Device, *, push_type: str, token: str, environment: str, topic: str) -> None:
    check_push_registration(push_type, token, environment, topic)
    now = time.time()
    await db.execute("DELETE FROM phone_push_tokens WHERE device_id = ? AND push_type = ?", (device.id, push_type))
    await db.execute(
        """INSERT INTO phone_push_tokens (device_id, push_type, token, environment, topic, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (device.id, push_type, token.lower(), environment, topic, now),
    )
    await db.commit()


async def unregister_push(db, device: Device, push_type: str) -> bool:
    async with db.execute("DELETE FROM phone_push_tokens WHERE device_id = ? AND push_type = ?", (device.id, push_type)) as cur:
        n = getattr(cur, "rowcount", 0) or 0
    await db.commit()
    return n > 0


async def list_devices(db, user_id: str) -> list[dict[str, Any]]:
    """The member's own phones (never a hash, a key or a push token), with which push types are registered."""
    async with db.execute(
        """SELECT d.id, d.name, d.platform, d.app_version, d.created_at, d.last_seen_at,
                  (SELECT COUNT(*) FROM phone_push_tokens t WHERE t.device_id = d.id) AS pushes
           FROM phone_devices d WHERE d.user_id = ? AND d.revoked = 0 ORDER BY d.created_at""",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append({"device_id": r[0], "name": r[1], "platform": r[2], "app_version": r[3] or "", "paired_at": r[4],
                    "last_seen_at": r[5], "push_registered": int(r[6] or 0) > 0})
    return out


async def revoke_device(db, device_id: str, *, user_id: str, is_admin: bool = False) -> bool:
    """Revoke a phone: the member's own, or any for an admin. Deletes its push tokens. True when a live device was revoked."""
    if is_admin:
        sql, args = "UPDATE phone_devices SET revoked = 1, revoked_at = ? WHERE id = ? AND revoked = 0", (time.time(), device_id)
    else:
        sql, args = ("UPDATE phone_devices SET revoked = 1, revoked_at = ? WHERE id = ? AND user_id = ? AND revoked = 0",
                     (time.time(), device_id, user_id))
    async with db.execute(sql, args) as cur:
        n = getattr(cur, "rowcount", 0) or 0
    if n:
        await db.execute("DELETE FROM phone_push_tokens WHERE device_id = ?", (device_id,))
    await db.commit()
    return n > 0
