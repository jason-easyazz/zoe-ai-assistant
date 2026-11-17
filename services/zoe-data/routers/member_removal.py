"""Remove a member: the ONE action behind Settings > People > Delete (``member_removal.py`` runs the job).

``POST /api/members/{user_id}/remove`` starts the removal, or resumes it from the step it stands at (idempotent: Settings calls it
again for a removal that is not finished). ``GET /api/members/{user_id}/removal`` and ``GET /api/members/removals`` report the true
state: ``in_progress``, ``retrying`` (not finished; Zoe keeps trying) or ``done`` - "done" only after the full erase confirmed and the
sign-in was deleted. Admin persons only (``require_admin``). The administrator's own session (``X-Session-ID``) is passed through to
zoe-auth for the two sign-in steps and never stored. Not under ``/api/admin/``: nginx sends that prefix to zoe-auth.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

import member_removal
from auth import require_admin

router = APIRouter(prefix="/api/members", tags=["members"])

#: the running jobs' tasks (kept so they are not garbage-collected mid-run)
_TASKS: set = set()


def _session(request: Request) -> str:
    return str(request.headers.get("X-Session-ID") or request.headers.get("x-session-id") or "")


def _signed_in_admin(admin: dict, request: Request) -> None:
    """Removing a member is destructive and lasting: it needs an administrator who actually SIGNED IN. ``require_admin`` also passes
    the header-less ZOE_UNAUTHENTICATED_ROLE=family-admin promotion and a degraded identity; neither is a person, and an anonymous
    LAN caller must never be able to bar another member (the job's first step, before the missing session stops it)."""
    if admin.get("unauthenticated_override") or admin.get("auth_degraded") or not _session(request):
        raise HTTPException(status_code=403, detail="Sign in as an administrator to remove a member")


@router.post("/{user_id}/remove", status_code=202)
async def remove_member(user_id: str, request: Request, admin: dict = Depends(require_admin)):
    _signed_in_admin(admin, request)
    target = str(user_id or "").strip()
    if not member_removal.valid_member_id(target):
        raise HTTPException(status_code=400, detail="Not a member id")
    if target == str(admin.get("user_id") or ""):
        raise HTTPException(status_code=400, detail="You can't delete your own account")
    task = asyncio.ensure_future(member_removal.advance(target, actor=str(admin.get("user_id") or ""), session_id=_session(request)))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    await asyncio.sleep(0)                      # the job has started (it is marked running) before the state is read
    return await member_removal.status(target)


@router.get("/removals")
async def list_removals(_admin: dict = Depends(require_admin)):
    return {"removals": await member_removal.pending()}


@router.get("/{user_id}/removal")
async def removal_status(user_id: str, _admin: dict = Depends(require_admin)):
    return await member_removal.status(user_id)
