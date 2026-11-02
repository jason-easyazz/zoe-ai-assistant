"""/api/alarms - wake-up alarms (flag-dark: ``ZOE_ALARMS``). Logic, lifecycle and visibility rules: ``alarms.py``; design:
``docs/architecture/alarms.md``. With the flag off every route answers 404 (the router-level gate runs before auth and the DB).

  GET    /api/alarms              the caller's alarms (member: own + household; panel: household in full, personal masked)
  POST   /api/alarms              create (a member: personal; the panel: household)
  PATCH  /api/alarms/{id}         edit / enable / disable (reschedules, stops a ring in progress)
  DELETE /api/alarms/{id}
  GET    /api/alarms/ringing      what is ringing now, for the panel's ring screen (a personal alarm's label and music source are blanked for the panel)
  POST   /api/alarms/{id}/snooze  ringing -> snoozed (tap anywhere on the ring screen)
  POST   /api/alarms/{id}/dismiss ringing|snoozed -> off for this occurrence (the deliberate Turn off control)

Auth: ``require_member_or_panel`` for all; ownership is then decided by ``alarms.can_*`` (a member never touches another member's
personal alarm; the panel may silence any ringing alarm but edits only household ones).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request

import alarms
from database import get_db
from panel_principal import is_member, require_member_or_panel


def _on() -> None:
    if not alarms.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/api/alarms", tags=["alarms"], dependencies=[Depends(_on)])


def viewer_of(user: dict) -> alarms.Viewer:
    """A signed-in member acting as themselves; anything else that got past ``require_member_or_panel`` IS the panel."""
    u = user or {}
    if is_member(u) and "device_token_role" not in u and not u.get("panel_id"):
        return alarms.Viewer(user_id=str(u.get("user_id") or ""))
    return alarms.Viewer(user_id="", panel=True)


def _err(e: alarms.AlarmError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.reason)


async def _load(db, alarm_id: str, v: alarms.Viewer, check) -> dict:
    a = await alarms.get_alarm(db, alarm_id)
    if a is None or not alarms.can_see(a, v):
        raise HTTPException(status_code=404, detail="No such alarm")
    if not check(a, v):
        raise HTTPException(status_code=403, detail="That alarm is not yours to change")
    return a


@router.get("")
async def list_alarms(request: Request, user: dict = Depends(require_member_or_panel), db=Depends(get_db)):
    v, now = viewer_of(user), time.time()
    rows = [a for a in await alarms.list_alarms(db, v) if alarms.can_see(a, v)]
    return {"alarms": [alarms.present(a, v, now) for a in rows], "now": now, "tones": alarms.TONES}


@router.get("/ringing")
async def ringing_now(request: Request, user: dict = Depends(require_member_or_panel), db=Depends(get_db)):
    v, now = viewer_of(user), time.time()
    live = [a for a in await alarms.ringing(db) if alarms.can_silence(a, v)]

    def shown(a: dict) -> dict:
        """The ring screen needs the time, snooze and which tone to play - not what a PERSONAL alarm is called or which music it wakes
        to. The panel gets those blanked for a personal alarm (a music alarm names no source; the server plays it and reports its state)."""
        if v.panel and a["user_id"] != alarms.HOUSEHOLD:
            return {**a, "label": "", "sound_name": "", "sound_id": a["sound_id"] if a["sound_kind"] == "tone" else ""}
        return a

    live = [shown(a) for a in live]
    return {"now": now, "ringing": [{
        "id": a["id"], "label": a["label"], "time_hhmm": a["time_hhmm"], "sound_kind": a["sound_kind"], "sound_id": a["sound_id"],
        "sound_name": a["sound_name"], "ramp_seconds": a["ramp_seconds"], "snooze_minutes": a["snooze_minutes"],
        "snooze_count": a["snooze_count"], "can_snooze": a["snooze_count"] < alarms.MAX_SNOOZES,
        "ring_started_at": a["ring_started_at"], "stops_at": (a["ring_started_at"] or now) + alarms.RING_MAX_S} for a in live]}


@router.post("")
async def create_alarm(payload: dict, request: Request, user: dict = Depends(require_member_or_panel), db=Depends(get_db)):
    v, p = viewer_of(user), payload or {}
    try:
        a = await alarms.create_alarm(
            db, v.owner_id, time_hhmm=p.get("time_hhmm") or p.get("time"), repeat=p.get("repeat", "once"), once_date=p.get("once_date"),
            label=p.get("label", ""), sound_kind=p.get("sound_kind", "tone"), sound_id=p.get("sound_id", ""),
            sound_name=p.get("sound_name", ""), snooze_minutes=p.get("snooze_minutes"), ramp_seconds=p.get("ramp_seconds"))
    except alarms.AlarmError as e:
        raise _err(e) from None
    return {"alarm": alarms.present(a, v)}


@router.patch("/{alarm_id}")
async def update_alarm(alarm_id: str, payload: dict, request: Request, user: dict = Depends(require_member_or_panel),
                       db=Depends(get_db)):
    v = viewer_of(user)
    a = await _load(db, alarm_id, v, alarms.can_edit)
    try:
        a = await alarms.update_alarm(db, a, payload or {})
    except alarms.AlarmError as e:
        raise _err(e) from None
    return {"alarm": alarms.present(a, v)}


@router.delete("/{alarm_id}")
async def delete_alarm(alarm_id: str, request: Request, user: dict = Depends(require_member_or_panel), db=Depends(get_db)):
    a = await _load(db, alarm_id, viewer_of(user), alarms.can_edit)
    return {"ok": await alarms.delete_alarm(db, a["id"])}


@router.post("/{alarm_id}/snooze")
async def snooze_alarm(alarm_id: str, request: Request, user: dict = Depends(require_member_or_panel), db=Depends(get_db)):
    v = viewer_of(user)
    a = await _load(db, alarm_id, v, alarms.can_silence)
    try:
        a = await alarms.snooze(db, a)
    except alarms.AlarmError as e:
        raise _err(e) from None
    return {"alarm": alarms.present(a, v), "snoozed_until": a["snooze_until"]}


@router.post("/{alarm_id}/dismiss")
async def dismiss_alarm(alarm_id: str, request: Request, user: dict = Depends(require_member_or_panel), db=Depends(get_db)):
    v = viewer_of(user)
    a = await _load(db, alarm_id, v, alarms.can_silence)
    return {"alarm": alarms.present(await alarms.dismiss(db, a), v)}
