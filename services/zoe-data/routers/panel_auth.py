"""
Panel device token authentication endpoints.

Pi voice daemons and kiosk software use X-Device-Token for API access.
Tokens are SHA-256 hashed before storage.  The raw token is returned once on issue.

Routes:
  POST /api/panels/register         — create panel record (admin only)
  POST /api/panels/{panel_id}/token — issue device token (admin only)
  DELETE /api/panels/{panel_id}/token/{token_id} — revoke token (admin only)
  GET  /api/panels                  — list panels (admin only)
  POST /api/panels/auth/pin         — submit PIN for a challenge (any authenticated user)
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panels", tags=["panels"])

_PIN_MAX_ATTEMPTS = int(os.environ.get("ZOE_PIN_MAX_ATTEMPTS", "5"))
_PIN_LOCKOUT_S = int(os.environ.get("ZOE_PIN_LOCKOUT_S", "300"))  # 5 min lockout after max attempts
_CHALLENGE_TTL_S = int(os.environ.get("ZOE_PIN_CHALLENGE_TTL_S", "120"))

# In-memory cache for device tokens (panel_id → list of token info dicts).
# Populated from DB on startup via load_device_tokens() called from main.py.
_token_cache: dict[str, dict] = {}  # token_hash → {panel_id, role, scopes, expires_at, revoked}

# PIN brute-force protection: track failed attempts per challenge (challenge_id → count, lockout_until)
_pin_attempts: dict[str, dict] = {}  # challenge_id → {"count": int, "locked_until": float}


def _pin_check_locked(challenge_id: str) -> tuple[bool, int]:
    """Returns (is_locked, remaining_lockout_seconds)."""
    info = _pin_attempts.get(challenge_id)
    if not info:
        return False, 0
    if info.get("locked_until", 0) > time.time():
        remaining = int(info["locked_until"] - time.time())
        return True, remaining
    return False, 0


def _pin_record_failure(challenge_id: str) -> int:
    """Record a failed PIN attempt; return remaining attempts before lockout."""
    if challenge_id not in _pin_attempts:
        _pin_attempts[challenge_id] = {"count": 0, "locked_until": 0.0}
    _pin_attempts[challenge_id]["count"] += 1
    count = _pin_attempts[challenge_id]["count"]
    remaining = max(0, _PIN_MAX_ATTEMPTS - count)
    if count >= _PIN_MAX_ATTEMPTS:
        _pin_attempts[challenge_id]["locked_until"] = time.time() + _PIN_LOCKOUT_S
        logger.warning("panel_auth: PIN lockout triggered for challenge %s after %d failures", challenge_id, count)
    return remaining


def _pin_clear(challenge_id: str) -> None:
    _pin_attempts.pop(challenge_id, None)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def lookup_device_token(raw_token: str) -> dict | None:
    """Check cache for a valid (non-revoked, non-expired) device token."""
    h = _hash_token(raw_token)
    info = _token_cache.get(h)
    if not info or info.get("revoked"):
        return None
    exp = info.get("expires_at")
    if exp and datetime.fromisoformat(exp) < datetime.now(tz=timezone.utc):
        return None
    return info


async def _resolve_device_token_user(raw_token: str) -> dict | None:
    """Resolve a device token to a user dict for use in get_current_user().

    Returns a user dict compatible with auth.py user format, or None if the
    token is invalid. Panel device tokens get role 'member' to allow chat access.
    """
    info = lookup_device_token(raw_token)
    if not info:
        return None
    panel_id = info.get("panel_id", "unknown")
    role = info.get("role", "voice")
    # Map device roles to user roles: voice/panel → member (can chat, not admin)
    user_role = "member" if role in ("voice", "panel", "kiosk") else "admin" if role == "admin" else "member"
    return {
        "user_id": "family-admin",  # Panel devices act as the household default user
        "role": user_role,
        "username": f"panel:{panel_id}",
        "permissions": ["chat", "voice"],
        "panel_id": panel_id,
        "device_token_role": role,
    }


async def load_device_tokens(db) -> None:
    """Load all active device tokens from DB into the in-memory cache (call at startup)."""
    _token_cache.clear()
    cursor = await db.execute(
        "SELECT token_hash, panel_id, role, scopes, expires_at, revoked FROM device_tokens"
    )
    rows = await cursor.fetchall()
    for row in rows:
        _token_cache[row["token_hash"]] = dict(row)
    logger.info("panel_auth: loaded %d device tokens", len(_token_cache))


@router.get("")
async def list_panels(admin: dict = Depends(_require_admin), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT panel_id, name, location, panel_type, is_active, last_seen_at, created_at FROM panels ORDER BY created_at DESC"
    )
    rows = await cur.fetchall()
    return {"panels": [dict(r) for r in rows]}


@router.post("/register")
async def register_panel(payload: dict, admin: dict = Depends(_require_admin), db=Depends(get_db)):
    """Register a new panel (kiosk device)."""
    panel_id = str(payload.get("panel_id") or f"panel-{uuid.uuid4().hex[:8]}").strip()
    name = str(payload.get("name") or panel_id).strip()
    location = payload.get("location") or None
    ip = payload.get("ip_address") or None
    panel_type = payload.get("panel_type") or "kiosk"
    notes = payload.get("notes") or None

    existing = await (await db.execute("SELECT panel_id FROM panels WHERE panel_id = ?", (panel_id,))).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail=f"Panel '{panel_id}' already registered")

    await db.execute(
        "INSERT INTO panels (panel_id, name, location, ip_address, panel_type, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (panel_id, name, location, ip, panel_type, notes),
    )
    await db.commit()
    logger.info("panel_auth: registered panel %s", panel_id)
    return {"ok": True, "panel_id": panel_id, "name": name}


@router.post("/{panel_id}/token")
async def issue_token(panel_id: str, payload: dict, admin: dict = Depends(_require_admin), db=Depends(get_db)):
    """Issue a new device token for a panel daemon."""
    panel = await (await db.execute("SELECT panel_id FROM panels WHERE panel_id = ?", (panel_id,))).fetchone()
    if not panel:
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    token_id = uuid.uuid4().hex
    name = str(payload.get("name") or "voice-daemon").strip()
    role = str(payload.get("role") or "voice-daemon").strip()
    scopes = json.dumps(payload.get("scopes") or ["voice"])
    expires_at = payload.get("expires_at") or None

    await db.execute(
        "INSERT INTO device_tokens (id, panel_id, token_hash, name, role, scopes, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token_id, panel_id, token_hash, name, role, scopes, expires_at),
    )
    await db.commit()

    # Update in-memory cache.
    _token_cache[token_hash] = {
        "panel_id": panel_id,
        "role": role,
        "scopes": scopes,
        "expires_at": expires_at,
        "revoked": 0,
        "token_id": token_id,
    }
    logger.info("panel_auth: issued token %s for panel %s role %s", token_id, panel_id, role)
    return {
        "ok": True,
        "token_id": token_id,
        "panel_id": panel_id,
        "token": raw_token,  # Only returned here — store securely on the device.
        "role": role,
        "note": "Store this token securely. It cannot be retrieved again.",
    }


@router.delete("/{panel_id}/token/{token_id}")
async def revoke_token(panel_id: str, token_id: str, admin: dict = Depends(_require_admin), db=Depends(get_db)):
    """Revoke a device token immediately."""
    row = await (await db.execute(
        "SELECT token_hash FROM device_tokens WHERE id = ? AND panel_id = ?", (token_id, panel_id)
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")

    now = datetime.now(tz=timezone.utc).isoformat()
    await db.execute(
        "UPDATE device_tokens SET revoked = 1, revoked_at = ? WHERE id = ?", (now, token_id)
    )
    await db.commit()

    h = row["token_hash"]
    if h in _token_cache:
        _token_cache[h]["revoked"] = 1
    logger.info("panel_auth: revoked token %s for panel %s", token_id, panel_id)
    return {"ok": True, "token_id": token_id, "revoked": True}


@router.post("/auth/challenge")
async def create_pin_challenge(payload: dict, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """
    Create a PIN auth challenge for a high-privilege panel action.
    Returns a challenge_id; the panel shows a PIN pad to the user.
    """
    panel_id = str(payload.get("panel_id") or "").strip()
    action_context = payload.get("action_context") or None
    if not panel_id:
        raise HTTPException(status_code=400, detail="panel_id required")

    challenge_id = uuid.uuid4().hex
    expires_at = datetime.fromtimestamp(time.time() + _CHALLENGE_TTL_S, tz=timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO panel_auth_challenges (challenge_id, panel_id, user_id, action_context, status, expires_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        (challenge_id, panel_id, user.get("user_id"), json.dumps(action_context), expires_at),
    )
    await db.commit()

    # Broadcast to the panel so it shows the PIN pad.
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "panel_pin_request", {
            "panel_id": panel_id,
            "challenge_id": challenge_id,
            "action_context": action_context,
            "expires_at": expires_at,
        })
    except Exception as exc:
        logger.warning("panel_auth: broadcast failed: %s", exc)

    return {"ok": True, "challenge_id": challenge_id, "expires_at": expires_at}


@router.post("/auth/pin")
async def submit_pin(payload: dict, db=Depends(get_db)):
    """
    Submit a PIN for an outstanding challenge (called by the touch panel JS).
    PINs are validated against the user record in zoe-auth.
    """
    challenge_id = str(payload.get("challenge_id") or "").strip()
    pin = str(payload.get("pin") or "").strip()
    if not challenge_id or not pin:
        raise HTTPException(status_code=400, detail="challenge_id and pin required")

    row = await (await db.execute(
        "SELECT * FROM panel_auth_challenges WHERE challenge_id = ?", (challenge_id,)
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Challenge already {row['status']}")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=410, detail="Challenge expired")

    # Validate PIN against users table (pin_hash stored as SHA-256 hex).
    # Lockout check before attempting PIN validation
    locked, remaining_s = _pin_check_locked(challenge_id)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed PIN attempts. Locked out for {remaining_s}s.",
            headers={"Retry-After": str(remaining_s)},
        )

    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    user_row = None
    if row["user_id"]:
        user_row = await (await db.execute(
            "SELECT user_id, pin_hash FROM users WHERE user_id = ?", (row["user_id"],)
        )).fetchone()
    else:
        user_row = await (await db.execute(
            "SELECT user_id, pin_hash FROM users WHERE pin_hash = ? LIMIT 1", (pin_hash,)
        )).fetchone()

    pin_valid = user_row and hmac.compare_digest(user_row["pin_hash"] or "", pin_hash)

    status = "approved" if pin_valid else "rejected"
    now = datetime.now(tz=timezone.utc).isoformat()
    await db.execute(
        "UPDATE panel_auth_challenges SET status = ?, resolved_at = ? WHERE challenge_id = ?",
        (status, now, challenge_id),
    )
    await db.commit()

    if pin_valid:
        _pin_clear(challenge_id)
    else:
        remaining_attempts = _pin_record_failure(challenge_id)

    # Notify panel of result.
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "panel_pin_result", {
            "panel_id": row["panel_id"],
            "challenge_id": challenge_id,
            "status": status,
        })
    except Exception:
        pass

    if not pin_valid:
        attempts_info = _pin_attempts.get(challenge_id, {})
        raise HTTPException(
            status_code=403,
            detail=f"Incorrect PIN. {max(0, _PIN_MAX_ATTEMPTS - attempts_info.get('count', 0))} attempt(s) remaining.",
        )

    return {
        "ok": True,
        "challenge_id": challenge_id,
        "status": status,
        "panel_id": row["panel_id"],
    }
