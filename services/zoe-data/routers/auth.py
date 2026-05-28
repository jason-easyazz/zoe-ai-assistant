"""
Auth-related endpoints for zoe-data.

Currently exposes:
  POST /api/auth/logout  — invalidate the caller's session in zoe-auth and
                           immediately evict it from the local _session_cache so
                           the session cannot be replayed for up to CACHE_TTL_SECONDS.
"""
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request

import auth as _auth_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

ZOE_AUTH_URL = os.environ.get("ZOE_AUTH_URL", "http://localhost:8002")


@router.post("/logout")
async def logout(
    request: Request,
    current_user: dict = Depends(_auth_module.get_current_user),
):
    """Log out the current user.

    1. Forwards the session-invalidation request to zoe-auth.
    2. Immediately removes the session from the local _session_cache so the
       session ID cannot be replayed during the CACHE_TTL_SECONDS window
       (ZOE-ea67ad3a).
    """
    session_id = request.headers.get("X-Session-ID", "")

    # ── Forward logout to the upstream auth service ───────────────────────────
    upstream_ok = False
    if session_id:
        try:
            timeout = httpx.Timeout(5.0, connect=3.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{ZOE_AUTH_URL}/api/auth/logout",
                    headers={"X-Session-ID": session_id},
                )
                upstream_ok = resp.status_code in (200, 204)
                if not upstream_ok:
                    logger.warning(
                        "auth/logout: upstream returned %s for session %s...",
                        resp.status_code,
                        session_id[:20],
                    )
        except Exception as exc:
            logger.warning("auth/logout: upstream call failed (%s) — evicting cache anyway", exc)

    # ── Evict immediately from local cache (fix ZOE-ea67ad3a) ─────────────────
    if session_id and session_id in _auth_module._session_cache:
        del _auth_module._session_cache[session_id]
        logger.info(
            "auth/logout: evicted session %s... from local cache (user=%s)",
            session_id[:20],
            current_user.get("user_id"),
        )

    return {"ok": True, "upstream_invalidated": upstream_ok}
