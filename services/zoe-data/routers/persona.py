"""Persona layer routes (phase 0, flag-dark ``ZOE_PERSONA_LAYER``; mounted via ``register(app)``).

  GET    /api/persona                  the household record + defaults + vocabulary + the exact
                                       block the model reads (any signed-in member)
  PUT    /api/persona                  replace the household record (ADMIN; validated)
  POST   /api/persona/reset            back to the default persona (ADMIN)
  GET    /api/persona/modes/{user}     a member's mode (that member, or an admin)
  PUT    /api/persona/modes/{user}     set a member's mode ({"mode", "minor"?}); see rules below
  DELETE /api/persona/modes/{user}     reset a member to companion / non-minor
  GET    /api/persona/block?user_id=   the rendered block for the sidecar's future consumer
                                       (X-Internal-Token only: 401 missing, 403 wrong)

Mode rules (``docs/governance/emotional-safety-note.md`` §4):
  * a member may set their OWN mode among companion / mentor / helper, never ``kid`` and never
    ``minor``;
  * a minor's row (``kid`` / ``minor``) can be changed ONLY by an admin, and leaving minor
    status needs an explicit ``"minor": false`` — picking a mode never does it implicitly;
  * guests and synthetic ids have no mode (404).
No voice or panel editing UI ships with this router; the PUT is the only editor in phase 0.
Nothing here is reachable by a model: the sidecar gets ``GET /block`` (read-only) and no write.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request

import persona_layer as pl
from auth import _has_valid_internal_token, require_admin, require_signed_in
from user_filters import GUEST_USERS, is_synthetic_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/persona", tags=["persona"])


def _is_admin(user: dict) -> bool:
    from auth import is_admin_role

    return is_admin_role(user.get("role"))


def _422(exc: pl.PersonaValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail={"errors": exc.errors})


def _member_or_404(user_id: str) -> str:
    uid = (user_id or "").strip()
    if uid in GUEST_USERS or is_synthetic_user(uid):
        raise HTTPException(status_code=404, detail="guests and test users have no persona mode")
    return uid


def _payload(record: pl.PersonaRecord, member: pl.MemberMode | None, uid: str = "") -> dict:
    # `block` is what the model reads for this caller — "" for a held minor (governance note §7).
    block = pl.block_for(uid, record=record, member=member)
    return {
        "enabled": pl.enabled(),
        "persona": record.to_dict(),
        "version": record.version,
        "is_default": record.version == pl.default_persona().version,
        "block": block,
        "held_for_minor": bool(member and member.minor and not pl.MINORS_GET_PERSONA),
        "block_tokens": pl.estimate_tokens(block),
        "budget": {"max_tokens": pl.MAX_BLOCK_TOKENS, "max_chars": pl.MAX_BLOCK_CHARS},
        "vocabulary": {
            "traits": list(pl.TRAIT_VOCAB), "strengths": list(pl.STRENGTHS),
            "voice_style": {k: list(v) for k, v in pl.VOICE_STYLE_KEYS.items()},
            "modes": list(pl.MODES), "max_traits": pl.MAX_TRAITS,
            "max_boundaries": pl.MAX_BOUNDARIES, "max_boundary_chars": pl.MAX_BOUNDARY_CHARS,
            "max_backstory_chars": pl.MAX_BACKSTORY_CHARS,
        },
        "defaults": pl.default_persona().to_dict(),
    }


@router.get("")
async def get_persona(user: dict = Depends(require_signed_in)):
    """Anyone signed in can read how Zoe is shaped for the household — transparency is the point."""
    record = await pl.load_household() or pl.default_persona()
    uid = (user.get("user_id") or "").strip()
    member = None if (uid in GUEST_USERS or is_synthetic_user(uid)) else await pl.load_member_mode(uid)
    return _payload(record, member, uid)


@router.put("")
async def put_persona(body: dict, user: dict = Depends(require_admin)):
    try:
        record = pl.validate_persona(body)
    except pl.PersonaValidationError as exc:
        raise _422(exc) from exc
    version = await pl.save_household(record, updated_by=str(user.get("user_id") or ""))
    logger.info("PERSONA_CHANGE action=put by=%s version=%s", user.get("user_id"), version)
    return _payload(record, None)


@router.post("/reset")
async def reset_persona(user: dict = Depends(require_admin)):
    await pl.reset_household()
    logger.info("PERSONA_CHANGE action=reset by=%s", user.get("user_id"))
    return _payload(pl.default_persona(), None)


@router.get("/modes/{user_id}")
async def get_mode(user_id: str, user: dict = Depends(require_signed_in)):
    uid = _member_or_404(user_id)
    if uid != user.get("user_id") and not _is_admin(user):
        raise HTTPException(status_code=403, detail="you can read only your own mode")
    return {"user_id": uid, **(await pl.load_member_mode(uid)).to_dict(), "modes": list(pl.MODES)}


@router.put("/modes/{user_id}")
async def put_mode(user_id: str, body: dict, user: dict = Depends(require_signed_in)):
    uid = _member_or_404(user_id)
    admin = _is_admin(user)
    own = uid == user.get("user_id")
    if not (own or admin):
        raise HTTPException(status_code=403, detail="you can change only your own mode")
    unknown = sorted(set(body) - {"mode", "minor"})
    if unknown:
        raise HTTPException(status_code=422, detail={"errors": [f"unknown field(s): {', '.join(unknown)}"]})
    current = await pl.load_member_mode(uid)
    mode = body.get("mode", current.mode)
    minor_given = "minor" in body
    minor = body["minor"] if minor_given else current.minor

    if not admin:
        # A member's own edit: ordinary modes only, and never into or out of minor status.
        if current.minor or current.mode == "kid":
            raise HTTPException(status_code=403, detail="only an admin can change this mode")
        if mode == "kid" or (minor_given and minor):
            raise HTTPException(status_code=403, detail="only an admin can set kid mode or minor status")
    if mode == "kid" and minor_given and minor is False:
        raise HTTPException(status_code=422, detail={"errors": ["kid mode implies minor"]})
    # The validator is the rule (a minor holds only kid/helper): an admin moving a child to
    # companion must say so with an explicit "minor": false, never as a side effect of the mode.
    try:
        saved = await pl.save_member_mode(uid, pl.MemberMode(mode=mode, minor=minor),
                                          updated_by=str(user.get("user_id") or ""))
    except pl.PersonaValidationError as exc:
        raise _422(exc) from exc
    logger.info("PERSONA_CHANGE action=mode by=%s target=%s mode=%s minor=%s",
                user.get("user_id"), uid, saved.mode, saved.minor)
    return {"user_id": uid, **saved.to_dict()}


@router.delete("/modes/{user_id}")
async def delete_mode(user_id: str, user: dict = Depends(require_signed_in)):
    uid = _member_or_404(user_id)
    admin = _is_admin(user)
    if not (admin or uid == user.get("user_id")):
        raise HTTPException(status_code=403, detail="you can reset only your own mode")
    current = await pl.load_member_mode(uid)
    if not admin and (current.minor or current.mode == "kid"):
        raise HTTPException(status_code=403, detail="only an admin can reset this mode")
    await pl.reset_member_mode(uid)
    logger.info("PERSONA_CHANGE action=mode_reset by=%s target=%s", user.get("user_id"), uid)
    return {"user_id": uid, **pl.MemberMode().to_dict()}


@router.get("/block")
async def get_block(request: Request, user_id: str = Query(..., min_length=1)):
    """The rendered block for ``user_id`` — internal token only (the Flue sidecar's future
    consumer, mirroring ``/api/memories/user-model``). Guests / synthetic ids get the household
    tone with no relationship line. ``text`` is ``""`` when the flag is off."""
    if not request.headers.get("X-Internal-Token"):
        raise HTTPException(status_code=401, detail="persona block requires X-Internal-Token")
    if not _has_valid_internal_token(request):
        raise HTTPException(status_code=403, detail="persona block: invalid X-Internal-Token")
    if not pl.enabled():
        return {"version": "", "text": ""}
    uid = (user_id or "").strip()
    record = await pl.load_household() or pl.default_persona()
    member = None if (uid in GUEST_USERS or is_synthetic_user(uid)) else await pl.load_member_mode(uid)
    text = pl.block_for(uid, record=record, member=member)
    return {"version": record.version, "text": text}


def register(app: FastAPI) -> bool:
    """Mount the router iff ``ZOE_PERSONA_LAYER`` is on. Flag off = the routes are ABSENT (404)."""
    if not pl.enabled():
        return False
    app.include_router(router)
    logger.info("Persona router registered (ZOE_PERSONA_LAYER on)")
    return True
