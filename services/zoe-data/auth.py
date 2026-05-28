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
# Default role for requests without X-Session-ID. Fail-closed default is "guest"
# (read-only). Set ZOE_UNAUTHENTICATED_ROLE="family-admin" to restore legacy
# behaviour on trusted LAN deployments — a warning is logged on every such request
# so the relaxation is visible in the logs.
_UNAUTH_ROLE = os.environ.get("ZOE_UNAUTHENTICATED_ROLE", "guest").strip().lower() or "guest"
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
    def _normalize_auth_user(data: Any) -> dict:
        # zoe-auth may return either a flat user object or {"user": {...}}.
        user = data.get("user") if isinstance(data, dict) and isinstance(data.get("user"), dict) else data
        if not isinstance(user, dict):
            user = {}
        return {
            "user_id": user.get("user_id") or user.get("id", DEFAULT_USER_ID),
            "role": user.get("role", "user"),
            "username": user.get("username") or user.get("name", ""),
            "permissions": user.get("permissions", []),
        }

    try:
        timeout = httpx.Timeout(5.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{ZOE_AUTH_URL}/api/auth/user",
                headers={"X-Session-ID": session_id},
            )
            if resp.status_code == 200:
                return _normalize_auth_user(resp.json())
            if resp.status_code == 404:
                # Some deployments expose /api/auth/profile instead of /api/auth/user.
                # Fallback avoids noisy degraded-user auth warnings in panel polling.
                prof = await client.get(
                    f"{ZOE_AUTH_URL}/api/auth/profile",
                    headers={"X-Session-ID": session_id},
                )
                if prof.status_code == 200:
                    return _normalize_auth_user(prof.json())
                if prof.status_code in (401, 403):
                    return None
                if prof.status_code >= 500:
                    logger.warning(
                        "zoe-auth profile %s for session validation — using degraded user",
                        prof.status_code,
                    )
                    return _degraded_user()
                logger.warning(
                    "zoe-auth profile returned %s for session validation — degraded user",
                    prof.status_code,
                )
                return _degraded_user()
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
    """Extract and validate user from session header against zoe-auth.

    Also accepts X-Device-Token for voice/panel daemon requests — resolves to
    the panel's registered user so voice commands run in the right user context.
    """
    session_id = request.headers.get("X-Session-ID", "")
    device_token = request.headers.get("X-Device-Token", "")

    # Device token path: resolve to panel user without going through zoe-auth session.
    # Inline token check to avoid circular import with panel_auth (which imports auth).
    if not session_id and device_token:
        import hashlib as _hashlib
        _tok_hash = _hashlib.sha256(device_token.encode()).hexdigest()
        # Late import inside function body avoids module-level circular dependency
        try:
            from routers.panel_auth import _token_cache as _ptc
            _tok_info = _ptc.get(_tok_hash)
            if _tok_info and not _tok_info.get("revoked"):
                _exp = _tok_info.get("expires_at")
                _ok = True
                if _exp:
                    from datetime import datetime, timezone
                    _ok = datetime.fromisoformat(_exp) >= datetime.now(tz=timezone.utc)
                if _ok:
                    _pid = _tok_info.get("panel_id", "unknown")
                    return {
                        "user_id": DEFAULT_USER_ID,
                        "role": "member",
                        "username": f"panel:{_pid}",
                        "permissions": ["chat", "voice"],
                        "panel_id": _pid,
                    }
        except Exception:
            pass
        # Invalid/unknown token → fall through to unauthenticated behaviour

    if not session_id:
        if _UNAUTH_ROLE == "family-admin":
            logger.warning(
                "unauthenticated request promoted to family-admin via ZOE_UNAUTHENTICATED_ROLE override "
                "(path=%s method=%s) — flip back to 'guest' for fail-closed behaviour",
                request.url.path, request.method,
            )
            return {"user_id": DEFAULT_USER_ID, "role": "admin"}
        return {
            "user_id": "guest",
            "role": "guest",
            "username": "guest",
            "permissions": [],
        }

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


_ZOE_A2A_TOKEN = os.environ.get("ZOE_A2A_TOKEN", "")


async def get_a2a_caller(request: Request) -> dict:
    """A2A-aware auth dependency.

    Accepts either:
    1. ``Authorization: Bearer <token>`` header (inbound A2A agents).
       Requires ``ZOE_A2A_TOKEN`` to be configured; if that env-var is empty the
       Bearer path is disabled and any Bearer request is rejected immediately so
       that a misconfigured deployment can never silently accept unauthenticated
       agent tasks.
    2. ``X-Session-ID`` / ``X-Device-Token`` (UI and voice daemon paths).

    Returns a user dict. External A2A agents get ``user_id="a2a-agent"``,
    ``role="agent"`` so they can submit tasks but not access personal data.

    Raises 401 for unauthenticated (guest) callers — A2A task submission is
    always a privileged operation and must never be available anonymously.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        if not _ZOE_A2A_TOKEN:
            # Token auth path is disabled (ZOE_A2A_TOKEN not configured).
            # Reject rather than silently falling through to guest access.
            raise HTTPException(
                status_code=401,
                detail="A2A bearer token auth is not configured on this server",
            )
        token = auth_header[len("Bearer "):]
        if token == _ZOE_A2A_TOKEN:
            return {
                "user_id": "a2a-agent",
                "role": "agent",
                "username": "a2a-agent",
                "permissions": ["chat", "tasks"],
            }
        raise HTTPException(status_code=401, detail="Invalid A2A bearer token")

    # Fall through to standard session/device-token auth
    user = await get_current_user(request)

    # Reject unauthenticated (guest) callers — A2A task submission is a
    # privileged operation; anonymous access is never permitted.
    if user.get("role") == "guest":
        raise HTTPException(
            status_code=401,
            detail="Authentication required to submit agent tasks",
        )

    return user


_ZOE_INTERNAL_TOKEN = os.environ.get("ZOE_INTERNAL_TOKEN", "")


async def require_internal_token(request: Request) -> None:
    """Dependency for internal-only endpoints (e.g. MCP → main.py bridge calls).

    Accepts requests that satisfy *either* condition:
    1. The caller is on the loopback interface (127.0.0.1 / ::1).
    2. The ``X-Internal-Token`` request header matches ``ZOE_INTERNAL_TOKEN``.

    If ``ZOE_INTERNAL_TOKEN`` is unset the token path is disabled and only
    loopback callers are admitted.  This keeps the endpoint locked down by
    default while still allowing token-based auth for containerised deployments
    where the MCP server and the data service run in separate containers on a
    shared network.
    """
    client_host = request.client.host if request.client else ""
    is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
    if is_loopback:
        return

    # Token path — only enabled when ZOE_INTERNAL_TOKEN is configured.
    if _ZOE_INTERNAL_TOKEN:
        provided = request.headers.get("X-Internal-Token", "")
        if provided and provided == _ZOE_INTERNAL_TOKEN:
            return

    raise HTTPException(
        status_code=403,
        detail="Internal endpoint: loopback or valid X-Internal-Token required",
    )
