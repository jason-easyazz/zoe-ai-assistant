"""
Auth integration for zoe-data.
Validates X-Session-ID against zoe-auth and caches results.
"""
import os
import time
import logging
import httpx
from fastapi import Request, HTTPException, Depends
from typing import Any, Optional, Dict, Tuple

logger = logging.getLogger(__name__)

ZOE_AUTH_URL = os.environ.get("ZOE_AUTH_URL", "http://localhost:8002")
CACHE_TTL_SECONDS = 30
DEFAULT_USER_ID = "family-admin"
_DEGRADED_MARK = "__zoe_degraded__"

_session_cache: Dict[str, Tuple[dict, float]] = {}


def _degraded_user() -> Dict[str, Any]:
    """When zoe-auth is down or erroring: serve family-admin without caching to session header."""
    return {
        _DEGRADED_MARK: True,
        "user_id": DEFAULT_USER_ID,
        "role": "member",
        "username": "guest",
        "permissions": [],
    }


def _cache_get(session_id: str) -> Optional[dict]:
    entry = _session_cache.get(session_id)
    if entry and (time.monotonic() - entry[1]) < CACHE_TTL_SECONDS:
        return entry[0]
    if entry:
        del _session_cache[session_id]
    return None


def _cache_set(session_id: str, user: dict):
    if len(_session_cache) > 500:
        cutoff = time.monotonic() - CACHE_TTL_SECONDS
        expired = [k for k, (_, ts) in _session_cache.items() if ts < cutoff]
        for k in expired:
            del _session_cache[k]
    _session_cache[session_id] = (user, time.monotonic())


async def _validate_with_auth_service(session_id: str) -> Optional[dict]:
    """Call zoe-auth to validate session. Returns user dict, degraded-user dict, or None if invalid."""
    try:
        timeout = httpx.Timeout(5.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{ZOE_AUTH_URL}/api/auth/user",
                headers={"X-Session-ID": session_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "user_id": data.get("user_id", DEFAULT_USER_ID),
                    "role": data.get("role", "user"),
                    "username": data.get("username", ""),
                    "permissions": data.get("permissions", []),
                }
            if resp.status_code in (401, 403):
                return None
            if resp.status_code >= 500:
                logger.warning(
                    "zoe-auth %s for session validation — using degraded user", resp.status_code
                )
                return _degraded_user()
            logger.warning("zoe-auth returned %s for session validation — degraded user", resp.status_code)
            return _degraded_user()
    except httpx.ConnectError:
        logger.warning("zoe-auth unreachable, falling back to default user with member role")
        return _degraded_user()
    except httpx.TimeoutException:
        logger.warning("zoe-auth timeout during session validation — degraded user")
        return _degraded_user()
    except Exception as e:
        logger.warning("Session validation error: %s — degraded user", e)
        return _degraded_user()


async def get_current_user(request: Request) -> dict:
    """Extract and validate user from session header against zoe-auth."""
    session_id = request.headers.get("X-Session-ID", "")

    if not session_id:
        return {"user_id": DEFAULT_USER_ID, "role": "admin"}

    cached = _cache_get(session_id)
    if cached:
        return cached

    validated = await _validate_with_auth_service(session_id)
    if validated is None:
        logger.warning("Invalid session: %s...", session_id[:20])
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if validated.get(_DEGRADED_MARK):
        return {k: v for k, v in validated.items() if k != _DEGRADED_MARK}
    _cache_set(session_id, validated)
    return validated


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
