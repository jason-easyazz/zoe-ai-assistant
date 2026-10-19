"""Settings > Bluetooth devices (flag-dark ``ZOE_BLUETOOTH_SETTINGS``; mounted via ``register(app)``, off = 404).

Household-facing (HUMAN ADMIN session only - never a device token, never a member, never a guest):
  GET    /api/bluetooth/config                                  {"enabled": true}  (404 while the flag is off: the UI hides the section)
  GET    /api/bluetooth/panels                                  panels whose Bluetooth agent has reported, with name + online state
  GET    /api/bluetooth/panels/{panel}                          one panel: devices (scan merged with remembered), active job
  POST   /api/bluetooth/panels/{panel}/scan                     -> job
  POST   /api/bluetooth/panels/{panel}/devices/{id}/{action}    connect | disconnect | forget | info | update_check | update_start |
                                                                update_cancel | omi_bond   -> job   (update_start needs
                                                                {"confirm":true,"ack_no_undo":true}; omi_bond needs {"confirm":true,"ack_wipe":true},
                                                                optional "replace"/"force":true - the owner-run secure pairing of a pendant
                                                                on Zoe phase-2 firmware, panel flag PANEL_BT_BOND_ENABLED)
  GET    /api/bluetooth/panels/{panel}/jobs/{job}               progress / outcome (the UI polls; there is no push)
Panel-facing (a panel's DEVICE TOKEN only):
  POST   /api/bluetooth/agent/poll                              long poll: {"snapshot", "wait_s"} -> {"commands": [...]}
  POST   /api/bluetooth/agent/report                            {"job_id", "state", "progress"?, "result"?, "error"?}

A panel can only report on jobs created for ITS OWN panel id (taken from its token), and the device id in every path is the agent's
opaque hash - a BLE address never reaches this service. See ``bluetooth_broker.py`` for the queue, the audit log and why this is
pull-based. Not under ``/api/voice/*``: this never touches the voice path.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

import bluetooth_broker as bt
from auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bluetooth", tags=["bluetooth"])


async def _require_human_admin(user: dict = Depends(require_admin)) -> dict:
    """An admin PERSON. ``get_current_user`` also resolves a device token to a user (possibly with an admin-ish role); a device token
    authenticates a panel, not a person, so it never reaches the household's Bluetooth controls."""
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
        raise HTTPException(status_code=403, detail="Sign in as the household admin to manage Bluetooth devices.")
    return user


async def _require_device(request: Request) -> dict:
    from routers.panel_auth import lookup_device_token

    raw = (request.headers.get("X-Device-Token") or "").strip()
    info = lookup_device_token(raw) if raw else None
    pid = str((info or {}).get("panel_id") or "")
    if not info or not bt.PANEL_ID_RX.match(pid):
        raise HTTPException(status_code=401, detail="a device token is required")
    return {"panel_id": pid}


def _raise(exc: bt.BrokerError):
    raise HTTPException(status_code=exc.status, detail=exc.detail)


async def _panel_names() -> dict:
    """panel_id -> {name, location} from the panels table (best effort: a DB hiccup must not hide the Bluetooth list)."""
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            rows = await (await db.execute("SELECT panel_id, name, location FROM panels")).fetchall()
        return {r["panel_id"]: {"name": r["name"], "location": r["location"]} for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.debug("bluetooth: panel names unavailable: %s", exc)
        return {}


# --- household-facing -----------------------------------------------------------------------------------------------
@router.get("/config")
async def get_config(user: dict = Depends(_require_human_admin)):
    return {"enabled": True}


@router.get("/panels")
async def list_panels(user: dict = Depends(_require_human_admin)):
    names = await _panel_names()
    rows = bt.BROKER.list_panels()
    for r in rows:
        n = names.get(r["panel_id"]) or {}
        r["name"] = n.get("name") or r["panel_id"]
        r["location"] = n.get("location") or ""
    rows.sort(key=lambda r: (not r["online"], r["name"]))
    return {"panels": rows}


@router.get("/panels/{panel_id}")
async def get_panel(panel_id: str, user: dict = Depends(_require_human_admin)):
    if not bt.PANEL_ID_RX.match(panel_id):
        raise HTTPException(status_code=400, detail="bad panel id")
    try:
        return bt.BROKER.view(panel_id)
    except bt.BrokerError as exc:
        _raise(exc)


async def _enqueue(panel_id: str, op: str, device_id: Optional[str], params: Optional[dict], user: dict) -> dict:
    if not bt.PANEL_ID_RX.match(panel_id):
        raise HTTPException(status_code=400, detail="bad panel id")
    try:
        job = bt.BROKER.enqueue(panel_id, op, device_id, params, user)
    except bt.BrokerError as exc:
        _raise(exc)
    return job.public()


async def _body(request: Request) -> Optional[dict]:
    try:
        doc = await request.json()
    except Exception:  # noqa: BLE001 - no body is fine
        return None
    return doc if isinstance(doc, dict) else None


@router.post("/panels/{panel_id}/scan")
async def scan(panel_id: str, user: dict = Depends(_require_human_admin)):
    return await _enqueue(panel_id, "scan", None, None, user)


@router.post("/panels/{panel_id}/devices/{device_id}/{action}")
async def device_action(panel_id: str, device_id: str, action: str, request: Request, user: dict = Depends(_require_human_admin)):
    if action not in bt.DEVICE_OPS:
        raise HTTPException(status_code=404, detail="unknown action")
    return await _enqueue(panel_id, action, device_id, await _body(request), user)


@router.get("/panels/{panel_id}/jobs/{job_id}")
async def get_job(panel_id: str, job_id: str, user: dict = Depends(_require_human_admin)):
    try:
        return bt.BROKER.get_job(panel_id, job_id).public()
    except bt.BrokerError as exc:
        _raise(exc)


# --- panel-facing ---------------------------------------------------------------------------------------------------
@router.post("/agent/poll")
async def agent_poll(request: Request, device: dict = Depends(_require_device)):
    doc: Any = await _body(request) or {}
    pid = device["panel_id"]
    bt.BROKER.heartbeat(pid, doc.get("snapshot"))
    try:
        wait_s = float(doc.get("wait_s", 20))
    except (TypeError, ValueError):
        wait_s = 20.0
    return {"commands": await bt.BROKER.take_commands(pid, wait_s)}


@router.post("/agent/report")
async def agent_report(request: Request, device: dict = Depends(_require_device)):
    try:
        bt.BROKER.report(device["panel_id"], await _body(request))
    except bt.BrokerError as exc:
        _raise(exc)
    return {"ok": True}


def register(app: FastAPI) -> bool:
    """Mount the router iff ``ZOE_BLUETOOTH_SETTINGS`` is on. Flag off = the routes are ABSENT (404), not merely refusing."""
    if not bt.enabled():
        return False
    app.include_router(router)
    logger.info("Bluetooth settings router registered (ZOE_BLUETOOTH_SETTINGS on)")
    return True
