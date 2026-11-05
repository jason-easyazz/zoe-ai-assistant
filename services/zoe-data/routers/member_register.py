"""Member register routes (flag-dark ``ZOE_MEMBER_REGISTER``; mounted via ``register(app)``; ``member_register.py``).

  GET    /api/member-register/{user}          a member's stored register + the exact line the model would read (that member or an admin)
  PUT    /api/member-register/{user}/{key}    set one setting {"value"}; an edit here is always ``stated``
  DELETE /api/member-register/{user}/{key}    forget one setting (leaves a tombstone); ``?value=`` names a topic
  POST   /api/member-register/{user}/confirm  {"key", "value"}: the member says yes to an inferred proposal

Rules: a member edits only their OWN register; an admin may edit anyone's (an owner edit is ``stated``). Nothing here lets a model
write. Guests and synthetic ids have no register (404). A child's stored entries are never rendered (the child gets the fixed kid
register), but the owner may still read and clear them.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query

import member_register as mr
from auth import is_admin_role, require_signed_in
from user_filters import GUEST_USERS, is_synthetic_user

logger = logging.getLogger(__name__)


def _flag_on() -> None:
    """Per-call flag: a process started with the flag on answers 404 the moment it is turned off."""
    if not mr.enabled():
        raise HTTPException(status_code=404, detail="not found")


router = APIRouter(prefix="/api/member-register", tags=["member-register"], dependencies=[Depends(_flag_on)])


def _target(user_id: str, user: dict) -> str:
    uid = (user_id or "").strip()
    if not uid or uid in GUEST_USERS or is_synthetic_user(uid):
        raise HTTPException(status_code=404, detail="guests and test users have no register")
    if uid != (user.get("user_id") or "").strip() and not is_admin_role(user.get("role")):
        raise HTTPException(status_code=403, detail="you can change only your own register")
    return uid


def _422(exc: mr.RegisterValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail={"errors": [str(exc)]})


@router.get("/{user_id}")
async def get_register(user_id: str, user: dict = Depends(require_signed_in)):
    uid = _target(user_id, user)
    entries = await mr.list_entries(uid)
    return {
        "user_id": uid,
        "enabled": mr.enabled(),
        "audience": await mr.audience(uid),
        "entries": [e.to_dict() for e in entries],
        "line": await mr.line_for(uid),
        "vocabulary": {k: list(v) for k, v in mr.ENUM_KEYS.items()},
        "max_topics": mr.MAX_TOPICS,
    }


@router.put("/{user_id}/{key}")
async def put_entry(user_id: str, key: str, body: dict, user: dict = Depends(require_signed_in)):
    uid = _target(user_id, user)
    try:
        result = await mr.set_entry(uid, key, str(body.get("value", "")), "stated", updated_by=str(user.get("user_id") or ""))
    except mr.RegisterValidationError as exc:
        raise _422(exc) from exc
    logger.info("MEMBER_REGISTER action=set by=%s key=%s result=%s", user.get("user_id"), key, result)
    return {"result": result}


@router.delete("/{user_id}/{key}")
async def delete_entry(user_id: str, key: str, value: str = Query(default=""), user: dict = Depends(require_signed_in)):
    uid = _target(user_id, user)
    try:
        removed = await mr.forget(uid, key, value)
    except mr.RegisterValidationError as exc:
        raise _422(exc) from exc
    logger.info("MEMBER_REGISTER action=forget by=%s key=%s removed=%s", user.get("user_id"), key, removed)
    return {"removed": removed}


@router.post("/{user_id}/confirm")
async def confirm_entry(user_id: str, body: dict, user: dict = Depends(require_signed_in)):
    uid = _target(user_id, user)
    try:
        result = await mr.confirm(uid, str(body.get("key", "")), str(body.get("value", "")), updated_by=str(user.get("user_id") or ""))
    except mr.RegisterValidationError as exc:
        raise _422(exc) from exc
    return {"result": result}


def register(app: FastAPI) -> bool:
    """Mount the router iff ``ZOE_MEMBER_REGISTER`` is on. Flag off = the routes are ABSENT (404)."""
    if not mr.enabled():
        return False
    app.include_router(router)
    logger.info("Member register router registered (ZOE_MEMBER_REGISTER on)")
    return True
