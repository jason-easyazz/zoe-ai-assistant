"""
Panel first-boot provisioning API.

Endpoints:
  POST /api/panels/provision/request           — Pi requests a pairing code (no auth)
  GET  /api/panels/provision/{code}            — Pi polls for status (X-Provision-Secret)
  GET  /api/panels/provision/{code}/public     — Phone reads code info (no auth)
  POST /api/panels/provision/{code}/confirm    — a signed-in household member confirms

Trust model (auth audit 2026-09-27):
  * ``/confirm`` requires a REAL member session (``X-Session-ID`` that zoe-auth
    validates to a non-guest user). ``get_current_user`` alone resolves an
    anonymous caller to guest rather than refusing it, which let any LAN device
    holding the 6-char code pair a panel and mint a kiosk device token.
  * ``/request`` returns a per-attempt ``poll_secret`` to the pairing device
    ONLY. The DB keeps its sha256. The status poll must present it, so the raw
    device token is released to the device that started the flow — never to
    whoever happens to poll the code first.
  * Codes come from ``secrets`` (not ``random``), expire after
    ``ZOE_PROVISION_CODE_TTL_S``, and a confirmed token that is not collected
    within ``_PICKUP_GRACE_S`` of expiry is cleared and its device token revoked.
"""

import hashlib
import logging
import hmac
import os
import secrets
import string
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from auth import get_current_user, require_signed_in
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panels/provision", tags=["panel-provision"])

_PROVISION_CODE_TTL_S = int(os.environ.get("ZOE_PROVISION_CODE_TTL_S", "300"))  # 5 min
_BASE_URL = os.environ.get("ZOE_BASE_URL", "https://192.168.1.218")
# How long after the code's expiry a CONFIRMED device token may still be
# collected. The Pi polls every 3s, so a token still uncollected past this is
# from a pairing device that went away — clear it and revoke the device token
# rather than leave a raw credential sitting in the table.
_PICKUP_GRACE_S = int(os.environ.get("ZOE_PROVISION_PICKUP_GRACE_S", "120"))
_POLL_SECRET_HEADER = "X-Provision-Secret"
_SWEEP_INTERVAL_S = 300  # sweep_uncollected_tokens cadence (main.py)

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
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _require_member_session(
    request: Request, user: dict = Depends(get_current_user)
) -> dict:
    """A signed-in household member (admin or member), presenting a SESSION.

    Refuses, in order:
      * no ``X-Session-ID`` at all → 401. ``get_current_user`` would otherwise
        hand back a guest (or, under the ZOE_UNAUTHENTICATED_ROLE override, the
        household admin) and the confirm would run anonymously.
      * a degraded identity (zoe-auth down, fail-open mode) → 503; the header
        was not actually checked.
      * a guest session → 403 (via ``auth.require_signed_in``).
    A device token is not a person, so it cannot confirm a pairing either: with
    no session header it is refused by the first rule.
    """
    if not request.headers.get("X-Session-ID", "").strip():
        raise HTTPException(status_code=401, detail="Sign in to pair a panel")
    if user.get("auth_degraded"):
        raise HTTPException(status_code=503, detail="Authentication service unavailable")
    return await require_signed_in(user)


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_utc(seconds: int) -> str:
    dt = datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_expired(expires_at: str) -> bool:
    return _is_expired_by(expires_at, 0)


def _is_expired_by(expires_at: str, grace_s: int) -> bool:
    """True once ``expires_at`` + ``grace_s`` has passed (unparseable → expired)."""
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc) > exp + timedelta(seconds=grace_s)
    except Exception:
        return True


@router.post("/request")
async def provision_request(payload: dict, request: Request, db=Depends(get_db)):
    """
    Pi calls this during first-boot to get a pairing code.
    No authentication required. Rate-limited to 3 requests per device per 10 minutes.

    Body: { "device_id": "<MAC address>" }
    Returns: { "code": "A3F7K2", "pair_url": "...", "expires_in": 300,
               "poll_secret": "<per-attempt secret>" }

    ``poll_secret`` goes to THIS caller only (the pairing device) and must be
    sent as ``X-Provision-Secret`` on every status poll. Only its sha256 is kept.
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
    poll_secret = secrets.token_urlsafe(32)
    await db.execute(
        """INSERT INTO panel_provision_codes
               (code, device_id, status, created_at, expires_at, poll_secret_hash)
           VALUES (?, ?, 'pending', ?, ?, ?)""",
        (code, device_id, _now_utc(), expires_at, _hash_secret(poll_secret)),
    )
    await db.commit()

    pair_url = f"{_BASE_URL}/touch/pair.html?code={code}"
    logger.info("provision_request: code=%s device=%s", code, device_id)
    return {
        "code": code,
        "pair_url": pair_url,
        "expires_in": _PROVISION_CODE_TTL_S,
        "poll_secret": poll_secret,
    }


async def _expire_uncollected_token(db, code: str, token: str) -> None:
    """A confirmed token nobody collected in time: clear the raw token and revoke
    the device token it maps to, so no live credential outlives the attempt."""
    token_hash = _hash_secret(token)
    await db.execute(
        "UPDATE panel_provision_codes SET token = NULL WHERE code = ? AND token = ?",
        (code, token),
    )
    await db.execute("UPDATE device_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,))
    await db.commit()
    try:
        from routers.panel_auth import _token_cache
        if token_hash in _token_cache:
            _token_cache[token_hash] = {**_token_cache[token_hash], "revoked": 1}
    except Exception:
        pass
    logger.warning("provision_poll: code=%s token not collected in time — revoked", code)


async def sweep_uncollected_tokens(db=None) -> int:
    """Server-side expiry that does NOT depend on a poll: revoke every confirmed
    token still uncollected past its grace (the pairing device went away), and
    mark stale pending codes expired. Scheduled by main.py every
    ``_SWEEP_INTERVAL_S``; returns how many tokens it revoked."""
    if db is None:
        from db_pool import get_db_ctx

        async with get_db_ctx() as conn:
            return await sweep_uncollected_tokens(conn)
    rows = await (await db.execute(
        "SELECT code, token, expires_at FROM panel_provision_codes "
        "WHERE status = 'confirmed' AND token IS NOT NULL"
    )).fetchall()
    revoked = 0
    for row in rows:
        if _is_expired_by(row["expires_at"], _PICKUP_GRACE_S):
            await _expire_uncollected_token(db, row["code"], row["token"])
            revoked += 1
    pending = await (await db.execute(
        "SELECT code, expires_at FROM panel_provision_codes WHERE status = 'pending'"
    )).fetchall()
    for row in pending:
        if _is_expired(row["expires_at"]):
            await db.execute(
                "UPDATE panel_provision_codes SET status = 'expired' WHERE code = ? AND status = 'pending'",
                (row["code"],),
            )
    await db.commit()
    return revoked


@router.get("/{code}")
async def provision_poll(code: str, request: Request, db=Depends(get_db)):
    """
    Pi polls this to check if the user has confirmed pairing.
    Token is returned ONCE when status=confirmed, then cleared from DB.

    The Pi has no session at this point; it proves it is the device that
    started THIS attempt with the ``poll_secret`` from ``/request``, sent as the
    ``X-Provision-Secret`` header (never a query param — nginx logs those).
    """
    row = await (await db.execute(
        "SELECT code, status, token, panel_id, expires_at, poll_secret_hash "
        "FROM panel_provision_codes WHERE code = ?",
        (code,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown provision code")

    # Bind the poll to the pairing device BEFORE anything else — a stranger who
    # read the code off the screen learns nothing here and changes nothing. A
    # row with no stored hash predates this check and is refused outright.
    presented = request.headers.get(_POLL_SECRET_HEADER, "")
    stored_hash = row["poll_secret_hash"]
    if not presented or not stored_hash or not hmac.compare_digest(
        _hash_secret(presented), str(stored_hash)
    ):
        raise HTTPException(status_code=403, detail="This pairing attempt belongs to another device")

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
        # One-time token pickup must be atomic. The previous read-then-clear
        # (SELECT token … then UPDATE … SET token = NULL) let two concurrent polls
        # both read the same raw device token before either cleared it.
        #
        # Use a single conditional UPDATE and decide the winner by affected-row
        # count: the row-level write lock serializes concurrent statements, so
        # exactly one poll flips the row (rowcount == 1) and the rest match zero
        # rows. The condition matches the EXACT token this poll read (S1), so it is
        # also robust against token rotation — we never clear/deliver a token other
        # than the one we observed. The winner returns the token it already read
        # above (we must NOT use `RETURNING token` here — PostgreSQL RETURNING yields
        # the post-update value, which is the NULL we just wrote).
        token = row["token"]
        if token and _is_expired_by(expires_at, _PICKUP_GRACE_S):
            await _expire_uncollected_token(db, code, token)
            return {"status": "expired"}
        if token:
            cleared = await db.execute(
                "UPDATE panel_provision_codes SET token = NULL WHERE code = ? AND token = ?",
                (code, token),
            )
            await db.commit()
            if getattr(cleared, "rowcount", 0) == 1:
                return {"status": "confirmed", "token": token, "panel_id": row["panel_id"]}
        # Token already delivered to an earlier poll (or rotated out from under us).
        return {"status": "confirmed"}

    return {"status": status}


@router.get("/{code}/public")
async def provision_public_info(code: str, db=Depends(get_db)):
    """
    Phone reads this after scanning QR to show what's connecting.
    No authentication required.
    """
    row = await (await db.execute(
        "SELECT code, device_id, status, token, expires_at FROM panel_provision_codes WHERE code = ?",
        (code,),
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown provision code")

    status = row["status"]
    if status == "pending" and _is_expired(row["expires_at"]):
        status = "expired"
    if status == "confirmed" and row["token"] and _is_expired_by(row["expires_at"], _PICKUP_GRACE_S):
        await _expire_uncollected_token(db, code, row["token"])  # any later request revokes

    return {
        "code": code,
        "device_id": row["device_id"],
        "status": status,
    }


@router.post("/{code}/confirm")
async def provision_confirm(
    code: str, payload: dict, user: dict = Depends(_require_member_session), db=Depends(get_db)
):
    """
    A signed-in household member confirms pairing from their phone. Anonymous
    callers get 401, guest sessions 403 (see ``_require_member_session``).
    Creates the panel record, issues a device token, and stores it for the Pi to pick up.

    Body: { "name": "Living Room", "location": "Living Room", "panel_id": "living-room-panel" }
    """
    user_id = user.get("user_id") or user.get("sub")
    if not user_id:
        raise HTTPException(status_code=403, detail="Sign in to pair a panel")
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
