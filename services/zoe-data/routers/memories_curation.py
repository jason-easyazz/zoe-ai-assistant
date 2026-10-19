"""Caregiver curation routes (memories-space P7; ``memory_curation.py`` holds every rule). Flag ``ZOE_MEMORIES_CURATORS`` (default
OFF, read per request): off = every route answers 404.

Identity is the server: a member SESSION (``memory_space.require_member``: no ``X-Session-ID``, a degraded auth check, a guest
role or a guest-shaped id are refused), never a ``user_id`` parameter for the caller. A PANEL never reaches these routes (a device
token or the kiosk credential cookie = 403): curation is typed on a phone or a computer, and a curated sensitive row must never
reach a wall. Every response is ``no-store``.

  GET  /api/memories/curation/me                                  the caller own opt-ins (an adult may change them; a child sees them)
  PUT  /api/memories/curation/me/{category}             body on   an ADULT opts a category in / out (out releases its curated rows)
  GET  /api/memories/curation/members                             (curator) who I may curate, and in which categories
  PUT  /api/memories/curation/members/{m}/sensitive/{category}    (curator) health / medication / money on / off for a CHILD
  GET  /api/memories/curation/members/{m}/rows                    (curator) the curated rows of m + how many notes are held
  POST /api/memories/curation/members/{m}/rows          text, category
  POST /api/memories/curation/members/{m}/rows/{id}/edit   text
  POST /api/memories/curation/members/{m}/rows/{id}/forget        the audited forget
  GET  /api/memories/curation/members/{m}/held                    notes held against curated rows
  POST /api/memories/curation/members/{m}/held/{id}     action    keep | accept
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories/curation", tags=["memories"])


def _out(content: Any) -> JSONResponse:
    return JSONResponse(content=content, headers=dict([("Cache-Control", "no-store")]))


async def _guard(request: Request, user: dict) -> str:
    """The flag, the panel wall and the member session. Returns the caller id."""
    import memory_curation as mc
    import memory_space

    if not mc.enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        from panel_kiosk_credential import COOKIE_NAME
    except Exception:  # noqa: BLE001
        COOKIE_NAME = "zoe_panel_kiosk"
    if request.headers.get("x-device-token") or (request.cookies or dict()).get(COOKIE_NAME):
        raise HTTPException(status_code=403, detail="Use your phone or computer for this.")
    try:
        return memory_space.require_member(user, has_session_header=bool(request.headers.get(memory_space.SESSION_HEADER)))
    except memory_space.SpaceRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


async def _run(fn, *args, **kwargs):
    """Run ``fn(db, *args)`` on one database handle; a refusal becomes its status, any other failure a 503 (never a half answer)."""
    import memory_curation as mc
    from db_compat import get_compat_db

    try:
        async with get_compat_db() as db:
            return await fn(db, *args, **kwargs)
    except mc.CurationRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories/curation: %s failed (%s)", getattr(fn, "__name__", "call"), type(exc).__name__)
        raise HTTPException(status_code=503, detail="Memories are unavailable right now.")


async def _as_curator(db, user: dict, fn, *args, **kwargs):
    import memory_curation as mc

    curator = await mc.require_curator(db, user)
    return await fn(db, curator, *args, **kwargs)


def _on(body: Any) -> bool:
    on = (body or dict()).get("on")
    if not isinstance(on, bool):
        raise HTTPException(status_code=422, detail="Say on or off.")
    return on


@router.get("/me")
async def my_curation(request: Request, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    uid = await _guard(request, user)
    return _out(await _run(mc.my_settings, uid))


@router.put("/me/{category}")
async def set_my_curation(category: str, request: Request, body: dict, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    uid = await _guard(request, user)
    on = _on(body)
    return _out(await _run(mc.set_my_optin, uid, category, on))


@router.get("/members")
async def curation_members(request: Request, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    return _out(dict(members=await _run(_as_curator, user, mc.members_for)))


@router.put("/members/{member_id}/sensitive/{category}")
async def set_child_sensitive(member_id: str, category: str, request: Request, body: dict,
                              user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    on = _on(body)
    return _out(await _run(_as_curator, user, mc.set_child_sensitive, member_id, category, on))


async def _rows_view(db, curator, member_id: str) -> dict:
    import memory_curation as mc

    pol = await mc.policy(db, curator, member_id)
    rows = [mc._view(r, pol.member) for r in await mc.curated_rows(pol)]
    held = await mc.held(db, curator, member_id)
    return dict(member=dict(user_id=pol.member.user_id, name=pol.member.name, kind=pol.member.kind),
                categories=mc._category_list(pol.allowed), rows=rows, held=len(held))


@router.get("/members/{member_id}/rows")
async def curated_rows(member_id: str, request: Request, user: dict = Depends(get_current_user)):
    await _guard(request, user)
    return _out(await _run(_as_curator, user, _rows_view, member_id))


def _text(body: Any) -> str:
    text = " ".join(str((body or dict()).get("text") or "").split())
    if not text or len(text) > 280:
        raise HTTPException(status_code=422, detail="Write the memory in one short sentence.")
    return text


@router.post("/members/{member_id}/rows")
async def add_curated(member_id: str, request: Request, body: dict, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    text = _text(body)
    category = str((body or dict()).get("category") or "")
    return _out(await _run(_as_curator, user, mc.add, member_id, text, category, via="desktop"))


@router.post("/members/{member_id}/rows/{row_id}/edit")
async def edit_curated(member_id: str, row_id: str, request: Request, body: dict, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    text = _text(body)
    return _out(await _run(_as_curator, user, mc.edit, member_id, row_id, text, via="desktop"))


@router.post("/members/{member_id}/rows/{row_id}/forget")
async def forget_curated(member_id: str, row_id: str, request: Request, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    await _run(_as_curator, user, mc.forget, member_id, row_id)
    return _out(dict(forgotten=True))


@router.get("/members/{member_id}/held")
async def held_notes(member_id: str, request: Request, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    return _out(dict(held=await _run(_as_curator, user, mc.held, member_id)))


@router.post("/members/{member_id}/held/{held_id}")
async def decide_held(member_id: str, held_id: str, request: Request, body: dict, user: dict = Depends(get_current_user)):
    import memory_curation as mc

    await _guard(request, user)
    action = str((body or dict()).get("action") or "")
    return _out(await _run(_as_curator, user, mc.decide_held, member_id, held_id, action))
