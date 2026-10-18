"""Omi pendant recordings (flag-dark ``ZOE_OMI_SYNC_ENABLED``; mounted via ``register(app)``, off = 404).

  POST   /api/omi/recordings        the Pi sync service uploads ONE recording (raw 16 kHz mono WAV body, metadata in
                                    ``X-Omi-*`` headers). DEVICE-TOKEN auth only. Idempotent by ``X-Omi-Key``. The 200 body
                                    (``ok`` + the echoed ``sha256``) is the custody confirmation: only after it does the Pi free
                                    the audio on the pendant.
  POST   /api/omi/sync-notice       ``{"count": N, "device": <id>}`` from the Pi after a session: one short bell notice to THAT pendant's
                                    owner. Device token.
  POST   /api/omi/device-status     the Pi's NUMBERS-ONLY health heartbeat (reachable, last-sync age, battery, ring fill, the pendant's overwrite
                                    counter, firmware) after each visit and every ~15 min while the pendant is away. Device token. Stores the
                                    latest row per pendant (``omi_health.py``, migration 0056); never audio, text or an address.
  GET    /api/omi/health            the caller's pendants' health + live warnings (no_sync / ring_fill / overwrite / battery_low /
                                    bridge_silent), owner-scoped exactly like the recordings. A device token never reads it.
  GET    /api/omi/recordings        the caller's recordings, newest first (no transcript text in the list) + ``transcription``: counts
                                    (``backlog`` waiting, ``transcribing``) and whether the transcriber is ``deferred`` and why
                                    (``reason`` code, ``since``, MemAvailable vs the floor) - never content
  GET    /api/omi/recordings/{id}   one recording with its transcript
  PATCH  /api/omi/recordings/{id}   ``{"keep_audio"?, "reviewed"?}``
  DELETE /api/omi/recordings/{id}   delete (audio, transcript, times); a content-free tombstone stays
  POST   /api/omi/recordings/retranscribe       requeue the caller's held recordings (``empty_transcript`` / ``failed``, WAV still kept)
  POST   /api/omi/recordings/{id}/retranscribe  requeue one of them (404 if it is not held with audio)

WHO MAY DO WHAT - two modes, chosen by ``ZOE_OMI_DEVICE_OWNERS`` (default OFF; see ``omi_devices.py``).

  OFF (legacy single owner). A device token authenticates the DEVICE (the panel Pi), never a person, so uploads are attributed to
  ``ZOE_OMI_OWNER_USER_ID`` - named by configuration - and are refused (503) while it is unset. The viewing/editing routes are OWNER-ONLY:
  a signed-in, non-guest session whose user id IS that owner.

  ON (device owners). Uploads are attributed ONLY to the owner the registry (``omi_devices``) names for ``X-Omi-Device`` - never to the
  panel's bound or signed-in user, never to a configured default, never to an admin. A pendant nobody owns gets 409
  ``device_not_registered`` (the Pi keeps the audio and retries slowly). Viewing/editing is for any signed-in, non-guest HUMAN member
  and only their OWN rows (a device token, even one bound to a member, is refused); there is NO admin override - a recording is the
  wearer's, not the household admin's. Admins manage ownership, never content:
    GET    /api/omi/members              household members who can own a pendant (not guests)
    GET    /api/omi/devices              the registry: device id, owner, label (metadata only - no recordings, no counts)
    PUT    /api/omi/devices/{device_id}  ``{"user_id", "label"?}`` assign / reassign (moves NO existing recording)
    DELETE /api/omi/devices/{device_id}  unassign
  Those three are HUMAN-ADMIN only (a signed-in admin person; a device token is refused, like Settings > Bluetooth) and every change is
  audited fail-closed (no audit line, no change).

In both modes every recording query also filters on ``user_id``.

Not under ``/api/voice/*`` on purpose: those broadcast to every panel and capture into the panel STT corpus.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import omi_devices as devices
import omi_health as health
import omi_recordings as omi
from auth import require_admin, require_signed_in

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/omi", tags=["omi"])

_NOTICE_COOLDOWN_S = 60.0
_last_notice: dict[str, float] = {}       # per OWNER: two members' pendants syncing together each get their own notice


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
    """Who may view/edit recordings. Legacy mode: the one configured owner. Device-owners mode: any signed-in non-guest HUMAN member
    (their own rows only - every query filters on ``user_id``); a device token never reads recordings, even one bound to a member."""
    if devices.enabled():
        if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
            raise HTTPException(status_code=403, detail="sign in as a household member to see your recordings")
        return user
    owner = omi.owner_user_id()
    if not owner or user.get("user_id") != owner:
        raise HTTPException(status_code=403, detail="owner only")
    return user


async def _require_human_admin(user: dict = Depends(require_admin)) -> dict:
    """An admin PERSON (same rule as Settings > Bluetooth): a device token authenticates a panel, not a person."""
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
        raise HTTPException(status_code=403, detail="Sign in as the household admin to assign a pendant.")
    return user


async def _upload_owner(device_id: str) -> tuple[str, Optional[JSONResponse]]:
    """(owner, None) or ('', refusal). Device-owners mode: the registry, nothing else. Legacy mode: the configured owner."""
    if not devices.enabled():
        owner = omi.owner_user_id()
        return (owner, None) if owner else ("", _error(503, "owner not configured"))
    if not devices.DEVICE_ID_RX.match(device_id):
        return "", _error(400, "bad device id")
    await devices.ensure_seeded()
    owner = await devices.owner_of(device_id)
    if not owner:
        logger.warning("OMI_UPLOAD_REFUSED device=%s reason=not_registered (an admin assigns it in Settings > Bluetooth)", device_id)
        return "", _error(409, "device_not_registered")
    return owner, None


@router.post("/recordings")
async def upload_recording(request: Request, device: dict = Depends(_require_device)):
    h = request.headers
    device_id = (h.get("X-Omi-Device") or "").strip()
    owner, refusal = await _upload_owner(device_id)          # the registry (or the legacy config) - never the panel's session
    if refusal is not None and refusal.status_code != 409:
        return refusal
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > omi.MAX_UPLOAD_BYTES:
        return _error(413, "audio too large", max_bytes=omi.MAX_UPLOAD_BYTES)
    body = await request.body()          # read it even when refusing 409, so the Pi sees the answer rather than a reset connection
    if refusal is not None:
        return refusal
    if not body:
        return _error(400, "empty body")
    try:
        duration = float(h.get("X-Omi-Duration-S", ""))
        gap = int(h.get("X-Omi-Gap-Before", "0") or 0)
    except ValueError:
        return _error(400, "bad numeric header")
    try:
        res = await omi.store_recording(
            owner, device_id=device_id, key=(h.get("X-Omi-Key") or "").strip(),
            sha256=(h.get("X-Omi-Sha256") or "").strip().lower(), started_at=(h.get("X-Omi-Started-At") or "").strip(),
            duration_s=duration, time_quality=(h.get("X-Omi-Time-Quality") or "device").strip(), gap_before=gap, wav=body,
            registry=devices.enabled())
    except omi.StoreError as exc:
        return _error(exc.status, exc.code)
    return res


class _Notice(BaseModel):
    count: int
    device: str = ""


@router.post("/sync-notice")
async def sync_notice(body: _Notice, device: dict = Depends(_require_device)):
    # The notice goes to THE PENDANT'S OWNER and nobody else: found from the device id, never from the panel's session.
    if devices.enabled():
        owner = await devices.owner_of((body.device or "").strip())
        if not owner:
            return {"ok": True, "sent": False}
    else:
        owner = omi.owner_user_id()
        if not owner:
            return _error(503, "owner not configured")
    now = time.monotonic()
    if body.count <= 0 or now - _last_notice.get(owner, -_NOTICE_COOLDOWN_S) < _NOTICE_COOLDOWN_S:
        return {"ok": True, "sent": False}
    _last_notice[owner] = now
    return {"ok": True, "sent": await omi.notify_synced(owner, body.count)}


class _HealthReport(BaseModel):
    """Numbers, bools and one short token. ``extra=forbid``: a field this model does not name (a transcript, a path, an address) is a 422,
    so the endpoint cannot be used to smuggle content in."""
    model_config = ConfigDict(extra="forbid")
    device: str = Field(max_length=64)
    reachable: bool
    consecutive_misses: int = Field(0, ge=0, le=10_000_000)
    last_sync_age_s: Optional[int] = Field(None, ge=0, le=10 * 365 * 86400)
    battery_pct: Optional[int] = Field(None, ge=0, le=100)
    firmware: Optional[str] = Field(None, max_length=32, pattern=r"^[0-9A-Za-z._+-]*$")
    ring_fill_pct: Optional[float] = Field(None, ge=0, le=100)
    ring_fill_start_pct: Optional[float] = Field(None, ge=0, le=100)
    ring_stored_bytes: Optional[int] = Field(None, ge=0, le=2**40)
    ring_capacity_bytes: Optional[int] = Field(None, ge=0, le=2**40)
    dropped_total: Optional[int] = Field(None, ge=0, le=2**62)


@router.post("/device-status")
async def device_status(body: _HealthReport, device: dict = Depends(_require_device)):
    # Whose pendant is it? The registry (or the legacy configured owner) - never the panel's session. Nobody's: nothing is stored, so
    # there is no row an owner could be missing and no row another member could read.
    if not health.DEVICE_ID_RX.match((body.device or "").strip()):
        return _error(400, "bad device id")
    import user_erase_gate as erase_gate
    scheduled = erase_gate.snapshot()          # every member's erase generation NOW, before the owner is resolved: a later erase stales this write
    owner, refusal = await _upload_owner((body.device or "").strip())
    if refusal is not None:
        return refusal
    rep = health.Report(
        reachable=body.reachable, consecutive_misses=body.consecutive_misses, last_sync_age_s=body.last_sync_age_s,
        battery_pct=body.battery_pct, firmware=body.firmware or None, ring_fill_pct=body.ring_fill_pct,
        ring_fill_start_pct=body.ring_fill_start_pct, ring_stored_bytes=body.ring_stored_bytes,
        ring_capacity_bytes=body.ring_capacity_bytes, dropped_total=body.dropped_total)
    try:
        active = await health.record_report(owner, body.device.strip(), rep, scheduled_at=scheduled.get(owner, 0))
    except health.NotTheOwner:
        return _error(409, "device_not_registered")          # erased or reassigned while this request was in flight: store nothing
    except Exception as exc:  # noqa: BLE001 - the Pi treats anything but 200 as 'not delivered' and tries again
        logger.warning("OMI_HEALTH_STORE_FAILED device=%s (%s)", body.device.strip(), type(exc).__name__)
        return _error(503, "health_unavailable")
    return {"ok": True, "warnings": [w["kind"] for w in active]}


@router.get("/health")
async def my_pendant_health(user: dict = Depends(_require_owner)):
    try:
        pendants = await health.list_for(user["user_id"])
    except Exception as exc:  # noqa: BLE001 - an unmigrated table reads as 'no data yet', not a 500
        logger.warning("OMI_HEALTH_READ_FAILED (%s)", type(exc).__name__)
        pendants = []
    return {"pendants": pendants, "warnings_enabled": health.enabled()}


# --- the device registry (device-owners mode; admin manages WHO OWNS a pendant, never what it recorded) -----------------------------
def _registry_on() -> None:
    if not devices.enabled():
        raise HTTPException(status_code=404, detail="not found")


def _device_error(exc: devices.DeviceError):
    raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/members")
async def list_members(admin: dict = Depends(_require_human_admin)):
    _registry_on()
    return {"members": await devices.members()}


@router.get("/devices")
async def list_devices(admin: dict = Depends(_require_human_admin)):
    _registry_on()
    await devices.ensure_seeded()
    return {"devices": await devices.list_devices()}


class _Assign(BaseModel):
    user_id: str
    label: Optional[str] = None


@router.put("/devices/{device_id}")
async def assign_device(device_id: str, body: _Assign, admin: dict = Depends(_require_human_admin)):
    _registry_on()
    try:
        out = await devices.assign(device_id, body.user_id.strip(), label=body.label, actor=admin)
    except devices.DeviceError as exc:
        _device_error(exc)
    out["note"] = ("Recordings already stored stay with their owner. Audio still on the pendant or waiting on the panel Pi goes to the "
                   "new owner when it syncs.")
    return out


@router.delete("/devices/{device_id}")
async def unassign_device(device_id: str, admin: dict = Depends(_require_human_admin)):
    _registry_on()
    try:
        found = await devices.unassign(device_id, actor=admin)
    except devices.DeviceError as exc:
        _device_error(exc)
    if not found:
        raise HTTPException(status_code=404, detail="no such pendant")
    return {"ok": True}


@router.get("/recordings")
async def list_my_recordings(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: dict = Depends(_require_owner)):
    return {"recordings": await omi.list_recordings(user["user_id"], limit=limit, offset=offset),
            "empty_transcript_held": await omi.count_empty_transcripts(user["user_id"]),
            "transcription": await omi.transcription_status(user["user_id"])}


@router.post("/recordings/retranscribe")
async def retranscribe_my_held(user: dict = Depends(_require_owner)):
    """Requeue every one of the caller's held recordings (empty transcript or failed, WAV still kept). Their retention is unchanged."""
    return {"ok": True, "queued": await omi.requeue_held(user["user_id"])}


@router.post("/recordings/{rid}/retranscribe")
async def retranscribe_my_recording(rid: str, user: dict = Depends(_require_owner)):
    if not await omi.requeue_held(user["user_id"], rid=rid):
        raise HTTPException(status_code=404, detail="no held recording with audio to transcribe again")
    return {"ok": True, "queued": 1}


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
    from routers import omi_voices         # the member's voices in their recordings (same flag, same owner rule)
    app.include_router(omi_voices.router)
    logger.info("Omi recordings router registered (ZOE_OMI_SYNC_ENABLED on; owner %s)", "set" if omi.owner_user_id() else "NOT SET")
    return True
