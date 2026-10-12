"""Per-member biometric setup state - the READ API for the panel and the self-model, and the daemon's result report.

    GET  /api/enrollment/state              the signed-in member's OWN state (voice / face enrolled, samples, what was offered and answered)
    GET  /api/enrollment/state?user_id=ID   any member's state - ADMIN only (who is enrolled is itself biometric metadata)
    GET  /api/enrollment/household          every real member's state - ADMIN only
    POST /api/enrollment/result             DEVICE TOKEN only: the panel daemon reports how an enrolment run ended

Same scoping rules as the other biometric routes (``biometric_scope``): a device token owns nothing and cannot read a member's state; a
signed-in member sees only their own row; an admin sees the household, explicitly. The state itself is ``enrollment_state.get_state``
(enrolment is READ from ``speaker_profiles`` / ``face_profiles``, never copied). Counts and timestamps only - never an embedding.

``POST /result`` changes exactly one thing: a run that ENDED BY THE PERSON SAYING STOP (``cancelled``) starts the same quiet period as a
"no", so Zoe does not offer again tomorrow. Every other outcome (done / partial / failed / timeout) is logged and changes nothing.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

import enrollment_state as es
from biometric_scope import require_person_scope
from routers.voice_tts import _require_voice_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrollment", tags=["enrollment"])

_RESULTS = frozenset({"done", "partial", "cancelled", "failed", "timeout"})


@router.get("/state")
async def enrollment_state_for(user_id: str = "", caller: dict = Depends(_require_voice_auth)):
    caller_id, is_admin = require_person_scope(caller)
    target = (user_id or "").strip() or caller_id
    if target != caller_id and not is_admin:
        raise HTTPException(status_code=403, detail="you can only see your own setup state")
    state = await es.get_state(target)
    audience = await es.audience_of(target)
    return {"ok": True, **state.to_public(), "audience": audience, "offer_mode": es.mode()}


@router.get("/household")
async def enrollment_household(caller: dict = Depends(_require_voice_auth)):
    _, is_admin = require_person_scope(caller)
    if not is_admin:
        raise HTTPException(status_code=403, detail="household setup state is admin only")
    from db_compat import get_compat_db
    from user_filters import is_synthetic_user

    try:
        async with get_compat_db() as db:
            async with db.execute("SELECT user_id FROM auth_users ORDER BY user_id") as cur:
                ids = [str(r[0]) for r in await cur.fetchall() if r[0]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment household read failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="DB error") from exc
    members = []
    for uid in ids:
        if is_synthetic_user(uid):
            continue
        state = await es.get_state(uid)
        members.append({**state.to_public(), "audience": await es.audience_of(uid)})
    return {"ok": True, "offer_mode": es.mode(), "members": members}


@router.post("/result")
async def enrollment_result(payload: dict, caller: dict = Depends(_require_voice_auth)):
    if caller.get("source") != "device":
        raise HTTPException(status_code=403, detail="enrolment results come from the panel daemon (device token)")
    body = payload or {}
    status = str(body.get("status") or "").strip().lower()
    uid = str(body.get("user_id") or "").strip()
    if status not in _RESULTS or not uid or len(uid) > 128:
        raise HTTPException(status_code=400, detail="status and user_id are required")
    try:
        samples = max(0, min(10, int(body.get("samples") or 0)))
    except (TypeError, ValueError):
        samples = 0
    logger.info("ENROLL_RESULT status=%s samples=%d", status, samples)
    if status == "cancelled":
        await es.record_answer(uid, False)
    return {"ok": True}
