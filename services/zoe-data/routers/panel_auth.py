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
from typing import Optional

import asyncio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from auth import get_current_user, require_admin
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


def _request_has_panel_device_token(request: Request | None, panel_id: str) -> bool:
    """True iff the request carries a valid device token issued for ``panel_id``.

    Used as the authority proof on /auth/pin for a panel that has NO
    panel_user_bindings rows yet (first-boot / panel-daemon). Without it, a bare
    authenticated session could create a challenge for an arbitrary active panel
    and approve it with its own PIN — see the zero-binding branch in submit_pin.
    """
    try:
        raw = request.headers.get("X-Device-Token", "") if request is not None else ""
    except Exception:
        raw = ""
    if not raw:
        return False
    info = lookup_device_token(raw)
    return bool(info and str(info.get("panel_id")) == str(panel_id))


async def _resolve_device_token_user(raw_token: str) -> dict | None:
    """Resolve a device token to a user dict for use in get_current_user().

    A device token authenticates the DEVICE, not a person. The acting user is
    the one bound to that panel in panel_user_bindings (binding_type='default').
    A device with NO such binding is treated as **guest** (fail-closed): an
    unassigned device gets only guest-allowed capabilities, never a real user's
    (let alone admin's) data. This mirrors the session model where an absent
    identity resolves to guest. Bind a panel to a user to give its voice/kiosk
    that user's personal context. (ZOE-4321; unbound-device→guest hardening.)
    """
    info = lookup_device_token(raw_token)
    if not info:
        return None
    panel_id = info.get("panel_id", "unknown")
    role = info.get("role", "voice")

    bound_user_id: Optional[str] = None
    lookup_failed = False
    try:
        # get_db_ctx, not `async for db in get_db()`: the `break` leaked the
        # pooled connection (#953 / the 2026-07-03 pool drain).
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            row = await (
                await db.execute(
                    """SELECT user_id FROM panel_user_bindings
                       WHERE panel_id = ? AND binding_type = 'default' LIMIT 1""",
                    (panel_id,),
                )
            ).fetchone()
            if row and row["user_id"]:
                bound_user_id = row["user_id"]
    except Exception as exc:
        # Could NOT determine the binding (transient pool exhaustion, connection
        # drop, schema mismatch). We still fail closed to guest — the safe, least-
        # privilege direction — but a bound panel (e.g. the kitchen kiosk) may lose
        # its personal context here, so this must be VISIBLE, not a debug whisper.
        lookup_failed = True
        logger.warning(
            "panel binding lookup FAILED for %s (%s) — treating device as guest; "
            "a bound panel will lose personal context until the DB recovers",
            panel_id, exc,
        )

    # No binding (or lookup failed) → guest (fail-closed). No personal data.
    if not bound_user_id:
        if not lookup_failed:
            logger.info(
                "device token for panel %s has no default user binding — acting as guest",
                panel_id,
            )
        return {
            "user_id": "guest",
            "role": "guest",
            "username": f"panel:{panel_id}",
            "permissions": [],
            "panel_id": panel_id,
            "device_token_role": role,
        }

    user_role = "member" if role in ("voice", "panel", "kiosk") else "admin" if role == "admin" else "member"
    return {
        "user_id": bound_user_id,
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
async def list_panels(admin: dict = Depends(require_admin), db=Depends(get_db)):
    cur = await db.execute(
        """SELECT p.panel_id, p.name, p.location, p.panel_type, p.is_active, p.allow_guest,
                  p.ip_address, p.ssh_user, p.ssh_key_path, p.ssh_port,
                  p.last_seen_at, p.created_at,
                  (SELECT user_id FROM panel_user_bindings b
                     WHERE b.panel_id = p.panel_id AND b.binding_type = 'default' LIMIT 1) AS default_user_id,
                  (SELECT COUNT(*) FROM panel_user_bindings b
                     WHERE b.panel_id = p.panel_id AND b.binding_type = 'allowed') AS allowed_count
             FROM panels p
             ORDER BY p.created_at DESC"""
    )
    rows = await cur.fetchall()
    return {"panels": [dict(r) for r in rows]}


@router.get("/{panel_id}/public")
async def panel_public_info(panel_id: str, db=Depends(get_db)):
    """Public (no-auth) panel metadata for the login page.

    Returns only non-sensitive fields so the touch kiosk can render its own
    name/branding before a user logs in. The list of bound users is exposed
    through /api/auth/profiles?panel_id=… which zoe-auth serves.
    """
    row = await (await db.execute(
        "SELECT panel_id, name, location, panel_type, allow_guest FROM panels WHERE panel_id = ?",
        (panel_id,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Panel not found")
    return dict(row)


@router.get("/{panel_id}/status")
async def panel_status(panel_id: str, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Live status for a panel: last_seen_at, SSH reachability, active kiosk URL, default user."""
    row = await (await db.execute(
        """SELECT p.panel_id, p.name, p.location, p.is_active, p.ip_address,
                  p.ssh_user, p.ssh_port, p.last_seen_at,
                  (SELECT user_id FROM panel_user_bindings b
                     WHERE b.panel_id = p.panel_id AND b.binding_type = 'default' LIMIT 1) AS default_user_id
             FROM panels p WHERE p.panel_id = ?""",
        (panel_id,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Panel not found")

    data = dict(row)
    ip = data.get("ip_address")
    ssh_port = data.get("ssh_port") or 22

    ssh_reachable: Optional[bool] = None
    if ip:
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "ssh",
                    "-o", "ConnectTimeout=5",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "BatchMode=yes",
                    "-p", str(ssh_port),
                    f"{data.get('ssh_user') or 'pi'}@{ip}",
                    "echo ok",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=8.0,
            )
            await proc.communicate()
            ssh_reachable = proc.returncode == 0
        except Exception:
            ssh_reachable = False

    return {
        "panel_id": panel_id,
        "name": data.get("name"),
        "location": data.get("location"),
        "is_active": bool(data.get("is_active")),
        "ip_address": ip,
        "ssh_reachable": ssh_reachable,
        "last_seen_at": data.get("last_seen_at"),
        "default_user_id": data.get("default_user_id"),
    }


@router.post("/register")
async def register_panel(payload: dict, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Register a new panel (kiosk device)."""
    panel_id = str(payload.get("panel_id") or f"panel-{uuid.uuid4().hex[:8]}").strip()
    name = str(payload.get("name") or panel_id).strip()
    location = payload.get("location") or None
    ip = payload.get("ip_address") or None
    panel_type = payload.get("panel_type") or "kiosk"
    notes = payload.get("notes") or None
    allow_guest = 1 if payload.get("allow_guest", True) else 0
    ssh_user = payload.get("ssh_user") or "pi"
    ssh_key_path = payload.get("ssh_key_path") or None
    ssh_port = int(payload.get("ssh_port") or 22)

    existing = await (await db.execute("SELECT panel_id FROM panels WHERE panel_id = ?", (panel_id,))).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail=f"Panel '{panel_id}' already registered")

    await db.execute(
        """INSERT INTO panels (panel_id, name, location, ip_address, panel_type, notes, allow_guest,
                               ssh_user, ssh_key_path, ssh_port)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (panel_id, name, location, ip, panel_type, notes, allow_guest, ssh_user, ssh_key_path, ssh_port),
    )
    await db.commit()
    logger.info("panel_auth: registered panel %s", panel_id)
    return {"ok": True, "panel_id": panel_id, "name": name}


@router.patch("/{panel_id}")
async def update_panel(panel_id: str, payload: dict, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Update mutable panel metadata (name, location, allow_guest, notes)."""
    row = await (await db.execute("SELECT panel_id FROM panels WHERE panel_id = ?", (panel_id,))).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Panel not found")

    fields = []
    values: list = []
    for key in ("name", "location", "notes", "panel_type"):
        if key in payload and payload[key] is not None:
            fields.append(f"{key} = ?")
            values.append(str(payload[key]).strip())
    if "allow_guest" in payload:
        fields.append("allow_guest = ?")
        values.append(1 if payload["allow_guest"] else 0)
    if "is_active" in payload:
        fields.append("is_active = ?")
        values.append(1 if payload["is_active"] else 0)
    if not fields:
        return {"ok": True, "panel_id": panel_id, "updated": []}

    fields.append("updated_at = NOW()")
    values.append(panel_id)
    await db.execute(f"UPDATE panels SET {', '.join(fields)} WHERE panel_id = ?", tuple(values))
    await db.commit()
    logger.info("panel_auth: updated panel %s fields=%s", panel_id, [f.split(' = ')[0] for f in fields])
    return {"ok": True, "panel_id": panel_id}


@router.get("/{panel_id}/bindings")
async def get_panel_bindings(panel_id: str, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Return the current user bindings for a panel (admin view)."""
    row = await (await db.execute(
        "SELECT panel_id, name, allow_guest FROM panels WHERE panel_id = ?", (panel_id,)
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Panel not found")

    cur = await db.execute(
        "SELECT user_id, binding_type, priority FROM panel_user_bindings WHERE panel_id = ? ORDER BY binding_type, priority",
        (panel_id,),
    )
    bindings = [dict(r) for r in await cur.fetchall()]
    default_user_id = next((b["user_id"] for b in bindings if b["binding_type"] == "default"), None)
    allowed_user_ids = [b["user_id"] for b in bindings if b["binding_type"] == "allowed"]
    return {
        "panel_id": row["panel_id"],
        "name": row["name"],
        "allow_guest": bool(row["allow_guest"]),
        "default_user_id": default_user_id,
        "allowed_user_ids": allowed_user_ids,
        "bindings": bindings,
    }


@router.put("/{panel_id}/bindings")
async def set_panel_bindings(panel_id: str, payload: dict, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Replace the user bindings for a panel.

    Payload:
      {
        "default_user_id": "jason" | null,
        "allowed_user_ids": ["jason", "teneeka"],
        "allow_guest": true | false   # optional; also settable via PATCH /api/panels/{id}
      }

    A user listed in both `default_user_id` and `allowed_user_ids` is stored
    once as 'default' (default implies allowed for rendering purposes on the
    kiosk, so the default user always appears in the picker if the grid is
    shown).
    """
    row = await (await db.execute("SELECT panel_id FROM panels WHERE panel_id = ?", (panel_id,))).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Panel not found")

    default_user_id = payload.get("default_user_id") or None
    allowed_user_ids = payload.get("allowed_user_ids") or []
    if not isinstance(allowed_user_ids, list):
        raise HTTPException(status_code=400, detail="allowed_user_ids must be a list")

    # Normalize: default implies allowed; avoid storing the same user twice.
    allowed_set = {str(u).strip() for u in allowed_user_ids if str(u).strip()}
    if default_user_id:
        default_user_id = str(default_user_id).strip()
        allowed_set.discard(default_user_id)

    # Replace bindings atomically.
    await db.execute("DELETE FROM panel_user_bindings WHERE panel_id = ?", (panel_id,))
    if default_user_id:
        await db.execute(
            "INSERT INTO panel_user_bindings (panel_id, user_id, binding_type, priority) VALUES (?, ?, 'default', 0)",
            (panel_id, default_user_id),
        )
    for idx, user_id in enumerate(sorted(allowed_set)):
        await db.execute(
            "INSERT INTO panel_user_bindings (panel_id, user_id, binding_type, priority) VALUES (?, ?, 'allowed', ?)",
            (panel_id, user_id, idx),
        )
    if "allow_guest" in payload:
        await db.execute(
            "UPDATE panels SET allow_guest = ?, updated_at = NOW() WHERE panel_id = ?",
            (1 if payload["allow_guest"] else 0, panel_id),
        )
    await db.commit()
    logger.info(
        "panel_auth: bindings set for %s default=%s allowed=%s",
        panel_id, default_user_id, sorted(allowed_set),
    )
    return {
        "ok": True,
        "panel_id": panel_id,
        "default_user_id": default_user_id,
        "allowed_user_ids": sorted(allowed_set),
    }


@router.post("/{panel_id}/token")
async def issue_token(panel_id: str, payload: dict, admin: dict = Depends(require_admin), db=Depends(get_db)):
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
async def revoke_token(panel_id: str, token_id: str, admin: dict = Depends(require_admin), db=Depends(get_db)):
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
    # get_current_user() falls back to a guest identity (never raises) when no
    # X-Session-ID is present, per ZOE_UNAUTHENTICATED_ROLE. Challenge creation
    # broadcasts a PIN-prompt ui_action to the target panel, so an anonymous
    # caller must never be allowed to trigger it (mirrors the guest rejection
    # in auth.get_a2a_caller).
    if user.get("role") == "guest":
        raise HTTPException(status_code=401, detail="Authentication required to create a PIN challenge")

    panel_id = str(payload.get("panel_id") or "").strip()
    action_context = payload.get("action_context") or None
    if not panel_id:
        raise HTTPException(status_code=400, detail="panel_id required")

    return await create_pin_challenge_internal(
        panel_id=panel_id,
        user_id=user.get("user_id"),
        action_context=action_context,
        db=db,
    )


async def create_pin_challenge_internal(panel_id: str, user_id, action_context, db) -> dict:
    """Internal version — callable from voice_tts without HTTP context."""
    panel_row = await (
        await db.execute(
            "SELECT panel_id FROM panels WHERE panel_id = ? AND is_active = 1",
            (panel_id,),
        )
    ).fetchone()
    if not panel_row:
        raise HTTPException(status_code=404, detail="Panel not found or inactive")

    challenge_id = uuid.uuid4().hex
    expires_at = datetime.fromtimestamp(time.time() + _CHALLENGE_TTL_S, tz=timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO panel_auth_challenges (challenge_id, panel_id, user_id, action_context, status, expires_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        (challenge_id, panel_id, user_id, json.dumps(action_context), expires_at),
    )
    await db.commit()

    # Broadcast modern auth action so the touch login page handles PIN.
    try:
        from push import broadcaster
        await broadcaster.broadcast_to_panel(
            panel_id,
            "ui_action",
            {
                "action": {
                    "id": f"panel_auth_{panel_id}_{challenge_id[:8]}",
                    "action_type": "panel_request_auth",
                    "payload": {
                        "panel_id": panel_id,
                        "challenge_id": challenge_id,
                        "action_context": action_context,
                        "expires_at": expires_at,
                    },
                }
            },
        )
    except Exception as exc:
        logger.warning("panel_auth: broadcast failed: %s", exc)

    return {"ok": True, "challenge_id": challenge_id, "expires_at": expires_at}


@router.post("/auth/pin")
async def submit_pin(payload: dict, request: Request = None, db=Depends(get_db)):
    """
    Submit a PIN for an outstanding challenge (called by the touch panel JS).
    PINs are validated by delegating to zoe-auth's /api/auth/login/passcode
    endpoint (the system of record for passcodes — bcrypt, lockout, passcodes table).
    """
    challenge_id = str(payload.get("challenge_id") or "").strip()
    pin = str(payload.get("pin") or "").strip()
    # SECURITY (P2): this endpoint is UNAUTHENTICATED. A caller-supplied user_id
    # must NEVER influence which identity the PIN is validated against — otherwise
    # any valid user's PIN could approve a challenge for a panel/user they aren't
    # bound to. The acting identity is resolved ONLY from the challenge or the
    # panel's binding below; any "user_id" in the payload is deliberately ignored.
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

    # Lockout check before attempting PIN validation
    locked, remaining_s = _pin_check_locked(challenge_id)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed PIN attempts. Locked out for {remaining_s}s.",
            headers={"Retry-After": str(remaining_s)},
        )

    # Delegate PIN validation to zoe-auth (bcrypt + lockout + passcodes table).
    # Pass 0 proved the old SHA-256-on-users.pin_hash path always 500s: wrong column
    # name ("user_id" vs "id") and every users.pin_hash is NULL.
    zoe_auth_base = os.environ.get("ZOE_AUTH_URL", "http://localhost:8002")
    # Resolve the acting identity ONLY from trusted sources — the challenge's
    # stored user, else the panel's configured default-user binding. Caller input
    # is never consulted (see SECURITY note above).
    challenge_user_id = str(row["user_id"]) if row["user_id"] else None
    resolved_user_id = challenge_user_id
    resolved_from_binding = False
    if not resolved_user_id:
        # Voice-originated challenges are often created with user_id=None.
        # Fall back to the panel's configured default user so PIN verification
        # has a concrete identity target.
        try:
            _default_row = await (await db.execute(
                "SELECT user_id FROM panel_user_bindings WHERE panel_id = ? AND binding_type = 'default' LIMIT 1",
                (row["panel_id"],),
            )).fetchone()
            if _default_row and _default_row["user_id"]:
                resolved_user_id = str(_default_row["user_id"])
                resolved_from_binding = True
        except Exception as _bind_exc:
            # This lookup is part of deciding the acting identity, so it is
            # security-load-bearing and must FAIL CLOSED. A transient DB error
            # here must not fall through to the "no resolvable user" 400 (which
            # tells the panel the challenge itself is malformed and to give up);
            # return the same retryable 503 the authorization check below uses.
            logger.warning("panel_auth: default binding lookup failed: %s", _bind_exc)
            raise HTTPException(
                status_code=503,
                detail="Authorization check temporarily unavailable. Please try again.",
            )

    if not resolved_user_id:
        raise HTTPException(status_code=400, detail="Challenge has no resolvable user")

    # Verify panel authorization for a challenge-carried user. (Users resolved
    # from the panel's default binding above are authorized by definition and
    # skip this block.)
    if not resolved_from_binding:
        # This check is security-load-bearing, so it must FAIL CLOSED: a transient
        # DB error here must never let the request skip authorization and proceed to
        # PIN validation. Convert unexpected errors into a 503 (deny) instead of
        # swallowing them.
        try:
            _has_binding = await (await db.execute(
                "SELECT 1 FROM panel_user_bindings WHERE panel_id = ? LIMIT 1",
                (row["panel_id"],),
            )).fetchone()
            if _has_binding:
                # Bindings exist → the challenge user MUST be among them.
                _allowed = await (await db.execute(
                    "SELECT 1 FROM panel_user_bindings WHERE panel_id = ? AND user_id = ? LIMIT 1",
                    (row["panel_id"], resolved_user_id),
                )).fetchone()
                if not _allowed:
                    logger.warning(
                        "panel_auth: challenge user %s not bound to panel %s — rejecting PIN",
                        resolved_user_id, row["panel_id"],
                    )
                    raise HTTPException(status_code=403, detail="User not authorized for this panel")
            elif not _request_has_panel_device_token(request, row["panel_id"]):
                # Zero bindings for this panel. The challenge ALONE is NOT proof of
                # authority: create_pin_challenge accepts any authenticated user plus
                # an arbitrary active panel_id and stores that caller as the challenge
                # user, so trusting it here would let a normal session approve a PIN
                # for a panel it has no relationship to (BLOCKING cross-review finding).
                # Require the panel's own device token on the request (panel-daemon /
                # first-boot authority). Legitimate provisioning always creates a
                # 'default' binding atomically (panel_provision.provision_confirm), so a
                # correctly-provisioned panel is never in this zero-binding branch.
                logger.warning(
                    "panel_auth: PIN approval denied for unbound panel %s without device-token "
                    "authority (challenge user %s)",
                    row["panel_id"], resolved_user_id,
                )
                raise HTTPException(status_code=403, detail="Panel not authorized for PIN approval")
        except HTTPException:
            raise
        except Exception as _authz_exc:
            logger.warning("panel_auth: panel authorization check errored: %s", _authz_exc)
            raise HTTPException(
                status_code=503,
                detail="Authorization check temporarily unavailable. Please try again.",
            )

    pin_valid = False
    auth_service_error: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as _hc:
            r = await _hc.post(
                f"{zoe_auth_base}/api/auth/login/passcode",
                json={"user_id": resolved_user_id, "passcode": pin},
            )
            # zoe-auth returns HTTP 200 for both success/failure with a body:
            # {"success": bool, ...}. Treat any non-success as invalid PIN.
            _body = {}
            try:
                _body = r.json()
            except Exception:
                _body = {}
            if r.status_code >= 500:
                auth_service_error = f"zoe-auth HTTP {r.status_code}"
            else:
                pin_valid = (r.status_code == 200) and bool(_body.get("success"))
    except Exception as _auth_exc:
        logger.warning("panel_auth: zoe-auth delegation failed: %s", _auth_exc)
        auth_service_error = str(_auth_exc)

    if auth_service_error:
        try:
            from voice_metrics import voice_failure_reason_count
            voice_failure_reason_count.labels(path="auth", reason="auth_service_down").inc()
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail="Authentication service is temporarily unavailable. Please try again.",
        )

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

    # Pass 3 B6 resume hook: if this challenge was for a pending voice turn, replay it.
    try:
        action_ctx = json.loads(row["action_context"] or "{}") if row["action_context"] else {}
        if action_ctx.get("kind") == "voice_turn" and resolved_user_id:
            from routers.voice_tts import _PENDING_VOICE_IDENT, _VOICE_SESSIONS, voice_command
            p_id = action_ctx.get("panel_id") or row["panel_id"]
            pending = _PENDING_VOICE_IDENT.pop(p_id, None)
            pending_id = action_ctx.get("pending_id")
            if pending and pending_id and pending.get("pending_id") != pending_id:
                pending = None
            pending_transcript = (pending or {}).get("transcript") or str(action_ctx.get("pending_transcript") or "").strip()
            pending_session = (pending or {}).get("session_id") or str(action_ctx.get("pending_session_id") or "").strip()
            pending_fresh = bool((pending or {}).get("expire_at", 0) > time.monotonic())
            pending_has_durable = bool(str(action_ctx.get("pending_transcript") or "").strip())
            if (pending and pending_fresh) or (not pending and pending_has_durable):
                # Bind the resolved user to the session for the rest of the conversation.
                ses = _VOICE_SESSIONS.get(p_id)
                if ses:
                    ses["bound_user_id"] = resolved_user_id
                # Persist to DB so _resolve_recent_panel_session_user trusts this panel
                # for the next 15 min (ZOE_PANEL_SESSION_TRUST_WINDOW_S default 900s),
                # preventing repeated auth challenges within the same session.
                try:
                    await db.execute(
                        """INSERT INTO ui_panel_sessions (panel_id, user_id, last_seen_at, updated_at)
                           VALUES (?, ?, NOW(), NOW())
                           ON CONFLICT(panel_id) DO UPDATE SET
                             user_id=excluded.user_id,
                             last_seen_at=NOW(),
                             updated_at=NOW()""",
                        (p_id, resolved_user_id),
                    )
                    await db.commit()
                except Exception as _db_exc:
                    logger.warning(
                        "panel_auth: ui_panel_sessions update failed for panel=%s "
                        "user=%s — panel session identity NOT persisted: %s",
                        p_id, resolved_user_id, _db_exc)
                if pending_transcript:
                    # Replay the held voice command under the verified identity.
                    async def _run_replay():
                        return await voice_command(
                            {
                                "text": pending_transcript,
                                "panel_id": p_id,
                                "session_id": pending_session,
                                "identified_user_id": resolved_user_id,
                            },
                            caller={"source": "pin_resume", "panel_id": p_id, "user_id": resolved_user_id},
                            stream=False,
                            db=db,
                        )

                    _replay_task = asyncio.create_task(_run_replay())

                    def _log_replay_done(task):
                        try:
                            result = task.result()
                            logger.info(
                                "panel_auth: voice replay completed user=%s panel=%s ok=%s",
                                resolved_user_id,
                                p_id,
                                bool((result or {}).get("ok")),
                            )
                        except Exception as replay_exc:
                            logger.warning(
                                "panel_auth: voice replay failed user=%s panel=%s err=%s",
                                resolved_user_id,
                                p_id,
                                replay_exc,
                            )

                    _replay_task.add_done_callback(_log_replay_done)
                    logger.info("panel_auth: replaying held voice turn for %s on panel %s", resolved_user_id, p_id)
    except Exception as _resume_exc:
        logger.warning("panel_auth: voice resume hook failed (non-fatal): %s", _resume_exc)

    return {
        "ok": True,
        "challenge_id": challenge_id,
        "status": status,
        "panel_id": row["panel_id"],
    }
