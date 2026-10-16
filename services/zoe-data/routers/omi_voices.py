"""The member's voices in their pendant recordings - who is speaking, tagging, and the voices Zoe has learned (``omi_voices.py``).

Mounted with the Omi router (``routers/omi_recordings.register``: absent while ``ZOE_OMI_SYNC_ENABLED`` is off). Every route is
``_require_owner`` - a signed-in, non-guest HUMAN member (a guest, an anonymous caller and a device token are refused) - and every
query filters on THAT member: there is no admin override and no way to name another member's voices.

  GET    /api/omi/voices/unknown                     unknown voices: "Voice 3", segments, recordings, days, sample segment ids
  POST   /api/omi/voices/unknown/{cluster_id}/tag    ``{"person_id"} | {"name"}`` every line of that voice is this person
  POST   /api/omi/voices/unknown/{cluster_id}/not-a-person  ``{"kind": "zoe" | "tv" | "radio" | "media"}`` never offered again,
                                                    its lines (and later matching ones) leave suggestions and the day review
  POST   /api/omi/segments/{segment_id}/tag          ``{"person_id"} | {"name"}`` one line
  PATCH  /api/omi/segments/{segment_id}              ``{"owner_label": "you" | "other" | null}`` "that was me" / "not me";
                                                    ``{"not_person": kind | null}`` "that was Zoe / the TV"
  GET    /api/omi/voices                             the voices Zoe has learned for me + my learning switch
  PATCH  /api/omi/voices/{voice_id}                  ``{"learning_enabled": bool}`` stop / resume learning this voice
  DELETE /api/omi/voices/{voice_id}                  forget this voice (the person and my tags stay)
  PUT    /api/omi/voices/settings                    ``{"learning_enabled": bool}`` stop learning voices at all

A tag is the member's confirmed word; whether it also teaches Zoe the voice is decided in ``omi_voices.tag`` (flag, the member's
switch, children) and returned as ``learning`` so the UI can give the one-time notice ("I'll learn Sam's voice from this ...").
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import omi_voices as voices
from routers.omi_recordings import _require_owner

router = APIRouter(prefix="/api/omi", tags=["omi"])


async def _call(fn, *args, **kw):
    import contacts_store
    from db_compat import get_compat_db
    try:
        async with get_compat_db() as db:
            return await fn(db, *args, **kw)
    except voices.VoiceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except contacts_store.ContactsError as exc:
        raise HTTPException(status_code=getattr(exc, "status", 400), detail=str(exc)) from exc


class _Tag(BaseModel):
    person_id: Optional[str] = None
    name: Optional[str] = None


class _Label(BaseModel):
    owner_label: Optional[str] = None
    not_person: Optional[str] = None


class _Kind(BaseModel):
    kind: str


class _Learning(BaseModel):
    learning_enabled: bool


def _found(ok: bool) -> dict:
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@router.get("/voices/unknown")
async def unknown_voices(user: dict = Depends(_require_owner)):
    return {"voices": await _call(voices.list_unknown, user["user_id"])}


@router.post("/voices/unknown/{cluster_id}/tag")
async def tag_voice(cluster_id: str, body: _Tag, user: dict = Depends(_require_owner)):
    return await _call(voices.tag, user["user_id"], person_id=body.person_id, name=body.name, cluster_id=cluster_id)


@router.post("/voices/unknown/{cluster_id}/not-a-person")
async def not_a_person_voice(cluster_id: str, body: _Kind, user: dict = Depends(_require_owner)):
    return {"ok": True, "segments": await _call(voices.mark_not_a_person, user["user_id"], kind=body.kind, cluster_id=cluster_id)}


@router.post("/segments/{segment_id}/tag")
async def tag_segment(segment_id: str, body: _Tag, user: dict = Depends(_require_owner)):
    return await _call(voices.tag, user["user_id"], person_id=body.person_id, name=body.name, segment_id=segment_id)


@router.patch("/segments/{segment_id}")
async def label_segment(segment_id: str, body: _Label, user: dict = Depends(_require_owner)):
    given = body.model_fields_set
    if not given:
        raise HTTPException(status_code=400, detail="give owner_label and/or not_person")
    if "not_person" in given:
        await _call(voices.mark_not_a_person, user["user_id"], kind=body.not_person, segment_id=segment_id)
    if "owner_label" in given:
        return _found(await _call(voices.set_owner_label, user["user_id"], segment_id, body.owner_label))
    return {"ok": True}


@router.get("/voices")
async def learned_voices(user: dict = Depends(_require_owner)):
    return {"voices": await _call(voices.list_learned, user["user_id"]),
            "learning_enabled": await _call(voices.member_learning, user["user_id"]), "feature_on": voices.learn_enabled()}


@router.put("/voices/settings")
async def voice_settings(body: _Learning, user: dict = Depends(_require_owner)):
    return await _call(voices.set_member_learning, user["user_id"], body.learning_enabled)


@router.patch("/voices/{voice_id}")
async def voice_learning(voice_id: str, body: _Learning, user: dict = Depends(_require_owner)):
    return _found(await _call(voices.set_voice_learning, user["user_id"], voice_id, body.learning_enabled))


@router.delete("/voices/{voice_id}")
async def forget_voice(voice_id: str, user: dict = Depends(_require_owner)):
    return _found(await _call(voices.forget_voice, user["user_id"], voice_id))
