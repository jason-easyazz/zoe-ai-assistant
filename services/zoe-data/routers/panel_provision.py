"""
Panel first-boot provisioning API.

Endpoints:
  POST /api/panels/provision/request           — Pi requests a pairing code (no auth)
  GET  /api/panels/provision/{code}            — Pi polls for status (no auth)
  GET  /api/panels/provision/{code}/public     — Phone reads code info (no auth)
  POST /api/panels/provision/{code}/confirm    — User confirms pairing (requires session)
"""

import hashlib
import logging
import os
import random
import secrets
import string
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panels/provision", tags=["panel-provision"])

_PROVISION_CODE_TTL_S = int(os.environ.get("ZOE_PROVISION_CODE_TTL_S", "300"))  # 5 min
_BASE_URL = os.environ.get("ZOE_BASE_URL", "https://192.168.1.218")

# In-memory rate limit: device_id → list of request timestamps
_rate_limit: dict[str, list[float]] = {}
_RATE_LIMIT_MAX = 3
_RATE_LIMIT_WINDOW_S = 600  # 10 minutes


def _check_rate_limit(device_id: str) -> None:
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW_S
    timestamps = _rate_limit.get(device_id, [])
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Too many provision requests. Try again in {int(_RATE_LIMIT_WINDOW_S / 60)} minutes.",
        )
    timestamps.append(now)
    _rate_limit[device_id] = timestamps


def _generate_code() -> str:
    """Generate a 6-character alphanumeric code (uppercase, no ambiguous chars)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # omit O, 0, I, 1
    return "".join(random.choices(alphabet, k=6))


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_utc(seconds: int) -> str:
    dt = datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc) > exp
    except Exception:
        return True


@router.post("/request")
async def provision_request(payload: dict, request: Request, db=Depends(get_db)):
    """
    Pi calls this during first-boot to get a pairing code.
    No authentication required. Rate-limited to 3 requests per device per 10 minutes.

    Body: { "device_id": "<MAC address>" }
    Returns: { "code": "A3F7K2", "pair_url": "...", "expires_in": 300 }
    """
    device_id = str(payload.get("device_id") or "").strip().lower()
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")
    if len(device_id) > 64:
        raise HTTPException(status_code=400, detail="device_id too long")

    _check_rate_limit(device_id)

    # Expire any existing pending codes for this device
    await db.execute(
        "UPDATE panel_provision_codes SET status = 'expired' WHERE device_id = ? AND status = 'pending'",
        (device_id,),
    )

    code = _generate_code()
    # Ensure uniqueness (collision extremely unlikely with 6-char code, but be safe)
    for _ in range(5):
        existing = await (await db.execute(
            "SELECT code FROM panel_provision_codes WHERE code = ? AND status = 'pending'",
            (code,),
        )).fetchone()
        if not existing:
            break
        code = _generate_code()

    expires_at = _expires_utc(_PROVISION_CODE_TTL_S)
    await db.execute(
        """INSERT INTO panel_provision_codes (code, device_id, status, created_at, expires_at)
           VALUES (?, ?, 'pending', ?, ?)""",
        (code, device_id, _now_utc(), expires_at),
    )
    await db.commit()

    pair_url = f"{_BASE_URL}/touch/pair.html?code={code}"
    logger.info("provision_request: code=%s device=%s", code, device_id)
    return {
        "code": code,
        "pair_url": pair_url,
        "expires_in": _PROVISION_CODE_TTL_S,
    }


@router.get("/{code}")
async def provision_poll(code: str, db=Depends(get_db)):
    """
    Pi polls this to check if the user has confirmed pairing.
    Token is returned ONCE when status=confirmed, then cleared from DB.

    No authentication required (Pi has no session at this point).
    """
    row = await (await db.execute(
        "SELECT code, status, token, expires_at FROM panel_provision_codes WHERE code = ?",
        (code,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown provision code")

    status = row["status"]
    expires_at = row["expires_at"]

    # Mark expired if TTL passed
    if status == "pending" and _is_expired(expires_at):
        await db.execute(
            "UPDATE panel_provision_codes SET status = 'expired' WHERE code = ?", (code,)
        )
        await db.commit()
        return {"status": "expired"}

    if status == "confirmed":
        token = row["token"]
        if token:
            # Clear the token from DB after delivery (one-time pickup)
            await db.execute(
                "UPDATE panel_provision_codes SET token = NULL WHERE code = ?", (code,)
            )
            await db.commit()
            panel_row = await (await db.execute(
                "SELECT panel_id FROM panel_provision_codes WHERE code = ?", (code,)
            )).fetchone()
            panel_id = panel_row["panel_id"] if panel_row else None
            return {"status": "confirmed", "token": token, "panel_id": panel_id}
        else:
            # Token already delivered
            return {"status": "confirmed"}

    return {"status": status}


@router.get("/{code}/public")
async def provision_public_info(code: str, db=Depends(get_db)):
    """
    Phone reads this after scanning QR to show what's connecting.
    No authentication required.
    """
    row = await (await db.execute(
        "SELECT code, device_id, status, expires_at FROM panel_provision_codes WHERE code = ?",
        (code,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown provision code")

    status = row["status"]
    if status == "pending" and _is_expired(row["expires_at"]):
        status = "expired"

    return {
        "code": code,
        "device_id": row["device_id"],
        "status": status,
    }


@router.post("/{code}/confirm")
async def provision_confirm(code: str, payload: dict, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """
    User confirms pairing from their phone (must be logged in).
    Creates the panel record, issues a device token, and stores it for the Pi to pick up.

    Body: { "name": "Living Room", "location": "Living Room", "panel_id": "living-room-panel" }
    """
    row = await (await db.execute(
        "SELECT code, device_id, status, expires_at FROM panel_provision_codes WHERE code = ?",
        (code,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown provision code")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Code is already {row['status']}")
    if _is_expired(row["expires_at"]):
        raise HTTPException(status_code=410, detail="Provision code has expired")

    device_id = row["device_id"]
    name = str(payload.get("name") or f"Panel-{code}").strip()
    location = payload.get("location") or None
    panel_id = str(payload.get("panel_id") or f"panel-{code.lower()}").strip()

    # Check for duplicate panel_id
    existing = await (await db.execute(
        "SELECT panel_id FROM panels WHERE panel_id = ?", (panel_id,)
    )).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail=f"Panel ID '{panel_id}' is already taken. Choose a different name.")

    # Derive IP from device_id context (panels registered at first-boot won't have IP yet)
    # The IP can be updated later via PATCH /api/panels/{id}

    # Register panel
    await db.execute(
        """INSERT INTO panels (panel_id, name, location, panel_type, allow_guest, ssh_user)
           VALUES (?, ?, ?, 'kiosk', 1, 'pi')""",
        (panel_id, name, location),
    )

    # Issue device token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO device_tokens (id, panel_id, token_hash, role, scopes, revoked)
           VALUES (?, ?, ?, 'kiosk', 'chat,voice', 0)""",
        (token_id, panel_id, token_hash),
    )

    # Bind confirming user as default user for this panel
    binding_id = str(uuid.uuid4())
    user_id = user.get("user_id") or user.get("sub") or "family-admin"
    await db.execute(
        """INSERT INTO panel_user_bindings (id, panel_id, user_id, binding_type)
           VALUES (?, ?, ?, 'default')""",
        (binding_id, panel_id, user_id),
    )

    # Mark provision code confirmed, store raw token for Pi pickup
    await db.execute(
        """UPDATE panel_provision_codes
           SET status = 'confirmed', panel_id = ?, token = ?, confirmed_by = ?
           WHERE code = ?""",
        (panel_id, raw_token, user_id, code),
    )
    await db.commit()

    logger.info("provision_confirm: panel=%s confirmed_by=%s code=%s", panel_id, user_id, code)

    # Reload device token cache (non-blocking best-effort)
    try:
        from routers.panel_auth import _token_cache
        _token_cache[token_hash] = {
            "panel_id": panel_id,
            "role": "kiosk",
            "scopes": "chat,voice",
            "expires_at": None,
            "revoked": 0,
        }
    except Exception:
        pass

    return {
        "ok": True,
        "panel_id": panel_id,
        "name": name,
        "message": f"'{name}' is now connected to Zoe.",
    }
