"""Guarded speaker-profile adaptation endpoints (flag-dark: ``ZOE_SPEAKER_ADAPT=off|shadow|on``, default off).

``POST /api/voice/adapt`` - DEVICE-TOKEN-ONLY. The panel daemon offers a qualifying turn's embedding (never audio) with
its claimed score, speech seconds and any face match; ``speaker_adaptation.process_adaptation`` applies every guard
and, only in mode ``on``, updates the adapted profile. With the flag off the route answers ``off`` without a DB read.

``DELETE /api/voice/adaptation`` - a signed-in member resets THEIR OWN adapted profile (an admin may name a user). The
enrolled anchor is untouched; "forget my voice" proper is the existing ``DELETE /api/voice/profiles/{id}``, which
removes the whole row (anchor + adapted).

Kept out of ``voice_tts.py`` on purpose: that file's enrol route is changing in parallel work.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import speaker_adaptation
from routers.voice_tts import _require_voice_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/adapt")
async def voice_adapt(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Offer one high-certainty turn for guarded profile adaptation (policy is server-side)."""
    if caller.get("source") != "device":
        raise HTTPException(status_code=403, detail="device token required")
    if caller.get("via_relay"):   # the claim + embedding are the panel's word; over the relay it may be anywhere
        raise HTTPException(status_code=403, detail="voice adaptation is not available through the relay")
    if speaker_adaptation.adapt_mode() == "off":
        return {"ok": True, "mode": "off", "applied": False, "reason": "off"}
    from db_compat import get_compat_db as _get_compat_db

    # The panel is the device token's panel, never a payload field: a panel cannot claim another panel's binding.
    panel_id = str(caller.get("panel_id") or "")
    try:
        async with _get_compat_db() as db:
            return await speaker_adaptation.process_adaptation(db, panel_id=panel_id, payload=payload)
    except Exception as exc:
        logger.error("voice/adapt error: %s", exc)
        raise HTTPException(status_code=500, detail="adaptation error") from exc


@router.delete("/adaptation")
async def voice_adaptation_reset(user_id: Optional[str] = None, caller: dict = Depends(_require_voice_auth)):
    """Drop the caller's adapted profile (anchor stays). Admins may pass ``user_id``."""
    from biometric_scope import require_person_scope
    from db_compat import get_compat_db as _get_compat_db

    caller_id, is_admin = require_person_scope(caller)
    target = (user_id or "").strip() or caller_id
    if target != caller_id and not is_admin:
        raise HTTPException(status_code=403, detail="you may only reset your own adapted voice profile")
    try:
        async with _get_compat_db() as db:
            changed = await speaker_adaptation.reset_adaptation(db, target)
    except Exception as exc:
        logger.error("voice/adaptation reset error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc
    return {"ok": True, "user_id": target, "reset": changed}
