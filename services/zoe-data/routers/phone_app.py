"""/api/phone - the box side of Zoe's own iPhone app, M0 (pairing + push-token registration). Flag-dark: ``ZOE_PHONE_APP``.

Contract: ``docs/architecture/zoe-iphone-app.md``. Logic and the security model: ``phone_app.py``. With the flag off every route
answers 404 (the app sees "this box has no phone app yet").

Auth:
  POST /api/phone/pair/start       a signed-in ADULT member (``require_member`` + ``manner_block.eligible``); never the panel
                                   principal, a guest or a child
  POST /api/phone/pair/complete    ``require_pairing_code``: the single-use pairing code IS the credential (peeked, then
                                   consumed atomically by ``complete_pairing``)
  GET  /api/phone/me               ``require_phone_device`` (``X-Zoe-Phone-Token``)
  PUT  /api/phone/push             the phone's device credential
  DELETE /api/phone/push/{type}    the phone's device credential
  GET  /api/phone/devices          a signed-in member: their own phones
  DELETE /api/phone/devices/{id}   a signed-in member: their own phone (an admin: any)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request

import phone_app
from auth import is_admin_role
from database import get_db
from panel_principal import require_member

logger = logging.getLogger(__name__)

def _on() -> None:
    """The flag gate. A ROUTER-level dependency, so it runs before every route's own dependencies (auth, DB): with the flag off an
    unauthenticated caller gets the same 404 as everyone else and no DB connection is opened (cross-review #2146)."""
    if not phone_app.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/api/phone", tags=["phone-app"], dependencies=[Depends(_on)])


def _err(e: phone_app.PhoneAppError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.reason)


async def require_phone_device(db=Depends(get_db), x_zoe_phone_token: str = Header(default="")) -> phone_app.Device:
    """The PHONE principal: the live (unrevoked) paired device holding the credential in ``X-Zoe-Phone-Token``; 401 otherwise."""
    dev = await phone_app.device_for_token(db, x_zoe_phone_token)
    if dev is None:
        raise HTTPException(status_code=401, detail="This phone is not paired with Zoe")
    return dev


async def require_pairing_code(request: Request) -> str:
    """The PAIRING principal: the member a live pairing code was minted for. It only PEEKS (the code stays unspent, so a bad key
    or platform in the same body does not burn it); ``complete_pairing`` consumes it atomically. 401 for an unknown or expired code
    - the same answer ``complete_pairing`` gives."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - no JSON is no code
        body = {}
    member = phone_app.peek_code(str((body or {}).get("code") or "")) if isinstance(body, dict) else None
    if not member:
        raise HTTPException(status_code=401, detail="that pairing code is not valid any more; show a new one")
    return member


@router.post("/pair/start")
async def pair_start(request: Request, user: dict = Depends(require_member)):
    import manner_block

    uid = str(user.get("user_id") or "")
    if not await manner_block.eligible(uid):  # adults only: an allowlisted adult role and no minor / kid mode; fails closed
        raise HTTPException(status_code=403, detail="The Zoe phone app is for adults in the household")
    try:
        out = phone_app.start_pairing(uid)
    except phone_app.PhoneAppError as e:
        raise _err(e) from None
    logger.info("PHONE_APP pair_start member=yes")
    return out


@router.post("/pair/complete")
async def pair_complete(payload: dict, db=Depends(get_db), _member: str = Depends(require_pairing_code)):
    try:
        out = await phone_app.complete_pairing(
            db, code=str(payload.get("code") or ""), device_name=str(payload.get("device_name") or ""),
            platform=str(payload.get("platform") or ""), public_key=str(payload.get("public_key") or ""),
            app_version=str(payload.get("app_version") or ""))
    except phone_app.PhoneAppError as e:
        logger.info("PHONE_APP pair_complete refused status=%d", e.status)
        raise _err(e) from None
    logger.info("PHONE_APP paired device=%s", out["device_id"])
    return out


@router.get("/me")
async def me(dev: phone_app.Device = Depends(require_phone_device)):
    name = ""
    try:
        import identity_facts

        ident = await identity_facts.resolve_identity(dev.user_id)
        name = ident.name if ident is not None else ""
    except Exception:  # noqa: BLE001 - a name is optional
        name = ""
    return {"device_id": dev.id, "device_name": dev.name, "member_name": name}


@router.put("/push")
async def register_push(payload: dict, dev: phone_app.Device = Depends(require_phone_device), db=Depends(get_db)):
    try:
        await phone_app.register_push(db, dev, push_type=str(payload.get("push_type") or ""), token=str(payload.get("token") or ""),
                                      environment=str(payload.get("environment") or ""), topic=str(payload.get("topic") or ""))
    except phone_app.PhoneAppError as e:
        raise _err(e) from None
    return {"ok": True}


@router.delete("/push/{push_type}")
async def unregister_push(push_type: str, dev: phone_app.Device = Depends(require_phone_device), db=Depends(get_db)):
    return {"ok": True, "removed": await phone_app.unregister_push(db, dev, push_type)}


@router.get("/devices")
async def devices(user: dict = Depends(require_member), db=Depends(get_db)):
    return {"devices": await phone_app.list_devices(db, str(user.get("user_id") or ""))}


@router.delete("/devices/{device_id}")
async def revoke(device_id: str, user: dict = Depends(require_member), db=Depends(get_db)):
    ok = await phone_app.revoke_device(db, device_id, user_id=str(user.get("user_id") or ""),
                                       is_admin=is_admin_role(user.get("role")))
    if not ok:
        raise HTTPException(status_code=404, detail="No such phone")
    logger.info("PHONE_APP revoked device=%s", device_id)
    return {"ok": True}
