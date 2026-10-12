"""Omi pendant recordings (flag-dark ``ZOE_OMI_SYNC_ENABLED``; mounted via ``register(app)``, off = 404).

  POST   /api/omi/recordings        the Pi sync service uploads ONE recording (raw 16 kHz mono WAV body, metadata in
                                    ``X-Omi-*`` headers). DEVICE-TOKEN auth only. Idempotent by ``X-Omi-Key``. The 200 body
                                    (``ok`` + the echoed ``sha256``) is the custody confirmation: only after it does the Pi free
                                    the audio on the pendant.
  POST   /api/omi/sync-notice       ``{"count": N}`` from the Pi after a session: one short bell notice to the owner. Device token.
  GET    /api/omi/recordings        the owner's recordings, newest first (no transcript text in the list)
  GET    /api/omi/recordings/{id}   one recording with its transcript
  PATCH  /api/omi/recordings/{id}   ``{"keep_audio"?, "reviewed"?}``
  DELETE /api/omi/recordings/{id}   delete (audio, transcript, times); a content-free tombstone stays

WHO MAY DO WHAT. A device token authenticates the DEVICE (the panel Pi), never a person, so uploads are attributed to
``ZOE_OMI_OWNER_USER_ID`` - named by configuration - and are refused (503) while it is unset. The viewing/editing routes are
OWNER-ONLY: a signed-in, non-guest session whose user id IS that owner; every query also filters on ``user_id``. Admin role does
not widen it - a recording is the wearer's, not the household admin's.

Not under ``/api/voice/*`` on purpose: those broadcast to every panel and capture into the panel STT corpus.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import omi_recordings as omi
from auth import require_signed_in

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/omi", tags=["omi"])

_NOTICE_COOLDOWN_S = 60.0
_last_notice = 0.0


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, **extra}, status_code=status)


async def _require_device(request: Request) -> dict:
    """A valid, non-revoked panel device token (``X-Device-Token``). 401 otherwise - never falls back to a session."""
    from routers.panel_auth import lookup_device_token

    raw = (request.headers.get("X-Device-Token") or "").strip()
    info = lookup_device_token(raw) if raw else None
    if not info:
        raise HTTPException(status_code=401, detail="a device token is required")
    return info


async def _require_owner(user: dict = Depends(require_signed_in)) -> dict:
    owner = omi.owner_user_id()
    if not owner or user.get("user_id") != owner:
        raise HTTPException(status_code=403, detail="owner only")
    return user


@router.post("/recordings")
async def upload_recording(request: Request, device: dict = Depends(_require_device)):
    owner = omi.owner_user_id()
    if not owner:
        return _error(503, "owner not configured")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > omi.MAX_UPLOAD_BYTES:
        return _error(413, "audio too large", max_bytes=omi.MAX_UPLOAD_BYTES)
    body = await request.body()
    if not body:
        return _error(400, "empty body")
    h = request.headers
    try:
        duration = float(h.get("X-Omi-Duration-S", ""))
        gap = int(h.get("X-Omi-Gap-Before", "0") or 0)
    except ValueError:
        return _error(400, "bad numeric header")
    try:
        res = await omi.store_recording(
            owner, device_id=(h.get("X-Omi-Device") or "").strip(), key=(h.get("X-Omi-Key") or "").strip(),
            sha256=(h.get("X-Omi-Sha256") or "").strip().lower(), started_at=(h.get("X-Omi-Started-At") or "").strip(),
            duration_s=duration, time_quality=(h.get("X-Omi-Time-Quality") or "device").strip(), gap_before=gap, wav=body)
    except omi.StoreError as exc:
        return _error(exc.status, exc.code)
    return res


class _Notice(BaseModel):
    count: int


@router.post("/sync-notice")
async def sync_notice(body: _Notice, device: dict = Depends(_require_device)):
    global _last_notice
    owner = omi.owner_user_id()
    if not owner:
        return _error(503, "owner not configured")
    now = time.monotonic()
    if body.count <= 0 or now - _last_notice < _NOTICE_COOLDOWN_S:
        return {"ok": True, "sent": False}
    _last_notice = now
    return {"ok": True, "sent": await omi.notify_synced(owner, body.count)}


@router.get("/recordings")
async def list_my_recordings(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: dict = Depends(_require_owner)):
    return {"recordings": await omi.list_recordings(user["user_id"], limit=limit, offset=offset)}


@router.get("/recordings/{rid}")
async def get_my_recording(rid: str, user: dict = Depends(_require_owner)):
    rec = await omi.get_recording(user["user_id"], rid)
    if rec is None:
        raise HTTPException(status_code=404, detail="no such recording")
    return rec


class _Patch(BaseModel):
    keep_audio: Optional[bool] = None
    reviewed: Optional[bool] = None


@router.patch("/recordings/{rid}")
async def patch_my_recording(rid: str, body: _Patch, user: dict = Depends(_require_owner)):
    rec = await omi.update_recording(user["user_id"], rid, keep_audio=body.keep_audio, reviewed=body.reviewed)
    if rec is None:
        raise HTTPException(status_code=404, detail="no such recording")
    return rec


@router.delete("/recordings/{rid}")
async def delete_my_recording(rid: str, user: dict = Depends(_require_owner)):
    if not await omi.delete_recording(user["user_id"], rid):
        raise HTTPException(status_code=404, detail="no such recording")
    return {"ok": True}


def register(app: FastAPI) -> bool:
    """Mount the router iff ``ZOE_OMI_SYNC_ENABLED`` is on. Flag off = the routes are ABSENT (404), not merely refusing."""
    if not omi.enabled():
        return False
    app.include_router(router)
    logger.info("Omi recordings router registered (ZOE_OMI_SYNC_ENABLED on; owner %s)", "set" if omi.owner_user_id() else "NOT SET")
    return True
