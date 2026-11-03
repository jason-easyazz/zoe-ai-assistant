"""Per-member biometric setup state - the READ API for the panel and the self-model, and the daemon's result report.

    GET  /api/enrollment/state              the signed-in member's OWN state (voice / face enrolled, samples, what was offered and answered)
    GET  /api/enrollment/state?user_id=ID   any member's state - ADMIN only (who is enrolled is itself biometric metadata)
    GET  /api/enrollment/household          every real member's state - ADMIN only
    POST /api/enrollment/result             DEVICE TOKEN only: the panel daemon reports how an enrolment run ended
    POST /api/enrollment/screen             DEVICE TOKEN only: the panel daemon puts the phrase to say / progress on ITS OWN talk screen

Same scoping rules as the other biometric routes (``biometric_scope``): a device token owns nothing and cannot read a member's state; a
signed-in member sees only their own row; an admin sees the household, explicitly. The state itself is ``enrollment_state.get_state``
(enrolment is READ from ``speaker_profiles`` / ``face_profiles``, never copied). Counts and timestamps only - never an embedding.

``POST /result`` changes exactly one thing: a run that ENDED BY THE PERSON SAYING OR TAPPING STOP (``status=cancelled`` AND
``reason`` = ``said_stop`` | ``tapped_stop``) starts the same quiet period as a "no", so Zoe does not offer again tomorrow. Every other
outcome - done / partial / failed / timeout, and a ``cancelled`` run caused by a daemon ``shutdown`` (or an older daemon that sends no
reason) - is logged and changes nothing. Every result closes the panel's enrolment grant (``biometric_scope``), and a result for a member
the server did not grant at that panel records nothing.

``POST /screen`` is the on-screen half of "Zoe, learn my voice": the person only HEARS each phrase, which is hard to remember, so the
daemon also has the panel show it (``voice_enroll`` push frame -> ``js/touch-voice-enroll.js``). It is a TRANSIENT push, never queued or
stored: the frame goes to the panel the DEVICE TOKEN belongs to (any ``panel_id`` in the body is ignored) and to no other, and not to the
``all`` channel ``broadcast_to_panel`` falls back to. It carries the phrase text, the step counter and one short line - no member id, no name.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException

import biometric_scope
import enrollment_state as es
from biometric_scope import require_person_scope
from routers.voice_tts import _require_voice_auth
from voice_session import send_to_panel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrollment", tags=["enrollment"])

_RESULTS = frozenset({"done", "partial", "cancelled", "failed", "timeout"})
#: the ``reason`` values of a ``cancelled`` run that mean the PERSON said / tapped stop (zoe_voice_enroll_mode)
_PERSON_STOPPED = frozenset({"said_stop", "tapped_stop"})


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
    # Off the LAN a copied token could close the panel's live grant or record a "no" for the granted member (2026-10-19).
    biometric_scope.require_local_panel(caller, "an enrolment result")
    body = payload or {}
    status = str(body.get("status") or "").strip().lower()
    uid = str(body.get("user_id") or "").strip()
    if status not in _RESULTS or not uid or len(uid) > 128:
        raise HTTPException(status_code=400, detail="status and user_id are required")
    try:
        samples = max(0, min(10, int(body.get("samples") or 0)))
    except (TypeError, ValueError):
        samples = 0
    reason = str(body.get("reason") or "").strip().lower()[:32]
    logger.info("ENROLL_RESULT status=%s reason=%s samples=%d", status, reason or "-", samples)
    # The run is over: close THIS panel's enrolment grant (the token's own panel, never the body's). Only the member the
    # server granted can be recorded as having said "no" - a copied panel token cannot put a member into the quiet period.
    granted = biometric_scope.close_enrolment_grant(str(caller.get("panel_id") or ""), reason=status)
    if granted != uid:
        logger.warning("ENROLL_RESULT ignored: no live enrolment grant for that member at this panel")
        return {"ok": True}
    # ONLY the person stopping it is a "no". A daemon shutdown / restart also ends the run as ``cancelled`` but says ``shutdown``
    # (or says nothing: an older daemon) - that must not start a 60-day quiet period for someone who never declined.
    if status == "cancelled" and reason in _PERSON_STOPPED:
        await es.record_answer(uid, False)
    return {"ok": True}


#: the states the talk screen knows (``scripts/setup/zoe_voice_enroll_mode.py`` emits exactly these)
_SCREEN_STATES = frozenset({"phrase", "recording", "accepted", "reask", "saving", "done", "partial", "failed", "clear"})
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def _screen_text(value, limit: int) -> str:
    return _CTRL.sub(" ", str(value or "")).strip()[:limit]


def _screen_int(value, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return 0


@router.post("/screen")
async def enrollment_screen(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Show (or clear) the enrolment prompt on the calling panel's talk screen. Device token only; best effort (``delivered`` 0 = the
    panel had no live socket, which the daemon ignores - the spoken prompt is the contract)."""
    if caller.get("source") != "device":
        raise HTTPException(status_code=403, detail="the enrolment screen is driven by the panel daemon (device token)")
    panel_id = str(caller.get("panel_id") or "").strip()
    if not panel_id:
        raise HTTPException(status_code=403, detail="this device token is not bound to a panel")
    body = payload or {}
    state = str(body.get("state") or "").strip().lower()
    if state not in _SCREEN_STATES:
        raise HTTPException(status_code=400, detail="unknown state")
    frame: dict = {"panel_id": panel_id, "state": state}
    if state != "clear":
        frame["line"] = _screen_text(body.get("line"), 160)
        if state in ("phrase", "recording", "accepted", "reask"):
            frame["phrase"] = _screen_text(body.get("phrase"), 240)
            frame["index"] = _screen_int(body.get("index"), 0, 12)
            frame["total"] = _screen_int(body.get("total"), 0, 12)
    delivered = await send_to_panel(panel_id, "voice_enroll", frame)
    return {"ok": True, "delivered": delivered}
