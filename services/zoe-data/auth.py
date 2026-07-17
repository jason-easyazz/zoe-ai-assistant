"""
Auth integration for zoe-data.
Validates X-Session-ID against zoe-auth and caches results.
"""
import os
import time
import logging
import httpx
import hmac
from fastapi import Request, HTTPException, Depends
from typing import Any, Optional, Dict, Tuple

logger = logging.getLogger(__name__)

ZOE_AUTH_URL = os.environ.get("ZOE_AUTH_URL", "http://localhost:8002")
# Default role for requests without X-Session-ID. Fail-closed default is "guest"
# (read-only). Set ZOE_UNAUTHENTICATED_ROLE="family-admin" to restore legacy
# behaviour on trusted LAN deployments — a warning is logged on every such request
# so the relaxation is visible in the logs.
_UNAUTH_ROLE = os.environ.get("ZOE_UNAUTHENTICATED_ROLE", "guest").strip().lower() or "guest"
_AUTH_FAIL_CLOSED = os.environ.get("ZOE_AUTH_FAIL_CLOSED", "true").lower() in (
    "1", "true", "yes",
)
CACHE_TTL_SECONDS = 30
# The household-admin identity. Used ONLY for the explicit, opt-in
# ZOE_UNAUTHENTICATED_ROLE=family-admin override below — never as a silent
# fallback. Every other "we can't determine the user" path resolves to GUEST
# (least privilege), so a malformed auth response or a downed auth service can
# never accidentally grant admin.
DEFAULT_USER_ID = "family-admin"
GUEST_USER_ID = "guest"
_DEGRADED_MARK = "__zoe_degraded__"

_session_cache: Dict[str, Tuple[dict, float]] = {}


def _degraded_user() -> Optional[Dict[str, Any]]:
    """When zoe-auth is down: fail closed (None), or — only if ZOE_AUTH_FAIL_CLOSED
    is explicitly disabled — a degraded GUEST (never admin) so an outage cannot
    silently elevate an anonymous caller (ZOE-4319)."""
    if _AUTH_FAIL_CLOSED:
        return None
    return {
        _DEGRADED_MARK: True,
        "user_id": GUEST_USER_ID,
        "role": "guest",
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


def _normalize_auth_user(data: Any) -> dict:
    """Normalise a zoe-auth response (flat user object OR {"user": {...}}) to the
    internal user dict. A validated session that returns no user_id is a malformed
    auth response — fall back to GUEST (least privilege), never the admin id."""
    user = data.get("user") if isinstance(data, dict) and isinstance(data.get("user"), dict) else data
    if not isinstance(user, dict):
        user = {}
    uid = user.get("user_id") or user.get("id")
    if not uid:
        # Malformed response (200 with no identity): DROP the payload's role/perms
        # too — a `{"role": "admin"}` with no user_id must NOT yield an admin. Fail
        # closed to a fully guest principal.
        return {"user_id": GUEST_USER_ID, "role": "guest", "username": "guest", "permissions": []}
    return {
        "user_id": uid,
        "role": user.get("role", "user"),
        "username": user.get("username") or user.get("name", ""),
        "permissions": user.get("permissions", []),
    }


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
                if prof.status_code == 404:
                    return None
                if prof.status_code >= 500:
                    logger.warning("zoe-auth profile %s for session validation", prof.status_code)
                    if _AUTH_FAIL_CLOSED:
                        raise HTTPException(
                            status_code=503,
                            detail="Authentication service unavailable",
                        )
                    return _degraded_user()
                logger.warning("zoe-auth profile returned %s for session validation", prof.status_code)
                if _AUTH_FAIL_CLOSED:
                    raise HTTPException(
                        status_code=503,
                        detail="Authentication service unavailable",
                    )
                return _degraded_user()
            if resp.status_code in (401, 403):
                return None
            if resp.status_code >= 500:
                logger.warning("zoe-auth %s for session validation", resp.status_code)
                if _AUTH_FAIL_CLOSED:
                    raise HTTPException(
                        status_code=503,
                        detail="Authentication service unavailable",
                    )
                return _degraded_user()
            logger.warning("zoe-auth returned %s for session validation", resp.status_code)
            if _AUTH_FAIL_CLOSED:
                raise HTTPException(
                    status_code=503,
                    detail="Authentication service unavailable",
                )
            return _degraded_user()
    except HTTPException:
        raise
    except httpx.ConnectError:
        logger.warning("zoe-auth unreachable during session validation")
        if _AUTH_FAIL_CLOSED:
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            )
        return _degraded_user()
    except httpx.TimeoutException:
        logger.warning("zoe-auth timeout during session validation")
        if _AUTH_FAIL_CLOSED:
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            )
        return _degraded_user()
    except Exception as e:
        logger.warning("Session validation error: %s", e)
        if _AUTH_FAIL_CLOSED:
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            )
        return _degraded_user()


async def get_current_user(request: Request) -> dict:
    """Extract and validate user from session header against zoe-auth.

    Also accepts X-Device-Token for voice/panel daemon requests — resolves to
    the panel's registered user so voice commands run in the right user context.
    """
    session_id = request.headers.get("X-Session-ID", "")
    device_token = request.headers.get("X-Device-Token", "")

    # Device token path: resolve to panel user without going through zoe-auth session.
    # Late import inside function body avoids module-level circular dependency
    # with panel_auth (which imports auth). Delegate to _resolve_device_token_user
    # so this path honours panel_user_bindings instead of hardcoding the
    # household-admin identity for every device token (ZOE security review).
    if not session_id and device_token:
        try:
            from routers.panel_auth import _resolve_device_token_user
            _resolved = await _resolve_device_token_user(device_token)
            if _resolved is not None:
                return _resolved
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
        if _AUTH_FAIL_CLOSED:
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            )
        return {k: v for k, v in validated.items() if k != _DEGRADED_MARK}
    _cache_set(session_id, validated)
    return validated


_ADMIN_ROLES = {"admin", "family-admin"}  # ZOE-22dcd46d: honour family-admin alias


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_signed_in(user: dict = Depends(get_current_user)) -> dict:
    """A real, signed-in user — not a guest.

    ``get_current_user`` RESOLVES an identity but never enforces one: an
    unauthenticated caller comes back as GUEST (least privilege) rather than a
    401/403. So `Depends(get_current_user)` alone lets any LAN client through —
    fine for reads, wrong for household-wide writes (e.g. changing the voice Zoe
    speaks with for everyone). Depend on THIS instead for those.

    Not the same as require_admin: any signed-in household member qualifies —
    this only excludes guest/unauthenticated.
    """
    if user.get("role") in (None, "guest") or user.get("user_id") in (None, GUEST_USER_ID):
        raise HTTPException(status_code=403, detail="Sign in to change this setting")
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
        if hmac.compare_digest(token, _ZOE_A2A_TOKEN):
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


def _is_internal_request(request: Request) -> bool:
    """True when the request is a TRUSTED internal caller.

    Trust basis (identical to require_internal_token): the request is loopback,
    OR it carries a valid X-Internal-Token. This is the ONLY gate under which an
    internal service (e.g. the Telegram bridge) may assert an acting identity —
    a public/browser request must never satisfy it.
    """
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return True
    if _ZOE_INTERNAL_TOKEN:
        provided = request.headers.get("X-Internal-Token", "")
        if provided and hmac.compare_digest(provided, _ZOE_INTERNAL_TOKEN):
            return True
    return False


async def require_internal_token(request: Request) -> None:
    """Dependency for internal-only endpoints (e.g. MCP → main.py bridge calls)."""
    if _is_internal_request(request):
        return
    raise HTTPException(
        status_code=403,
        detail="Internal endpoint: loopback or valid X-Internal-Token required",
    )


def _intent_dispatch_token_required() -> bool:
    """Dark flag: require a PROVEN X-Internal-Token (loopback insufficient) on
    /api/system/intent-dispatch. Default OFF — read lazily so tests and a
    .env+restart can flip it without re-importing."""
    return os.environ.get(
        "ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", ""
    ).strip().lower() in ("1", "true", "yes", "on")


async def require_intent_dispatch_auth(request: Request) -> None:
    """Gate for the internal ACTOR-ASSERTING endpoints — the brain's funnels:
    ``/api/system/intent-dispatch`` (writes as body ``user_id``) and
    ``/api/system/delegate-sync`` (delegates as body ``user_id``).

    A body-asserted ``user_id`` under bare-loopback trust is the same
    impersonation class #1054 closed for the ``X-Zoe-User-Id`` header: any
    SSRF'd or compromised local process could act as any user. Strict
    enforcement can't be unconditional yet — the flue brain sidecar (the main
    caller of both endpoints) does not send ``X-Internal-Token`` until its env
    is provisioned, and hard-requiring it would break live brain writes.

    Two-stage rollout, flag-keyed:
      * ``ZOE_INTENT_DISPATCH_REQUIRE_TOKEN`` unset (default): today's
        loopback-or-token trust, PLUS a readiness WARNING for every internal
        caller that did NOT prove the token — when the journal goes quiet the
        sidecar is provisioned and the flag is safe to flip.
      * flag set: only ``_has_valid_internal_token`` passes (#1054 semantics:
        token configured AND presented; loopback alone is insufficient).
    Rollback is unsetting the flag + restart.
    """
    if _intent_dispatch_token_required():
        if _has_valid_internal_token(request):
            return
        raise HTTPException(
            status_code=403,
            detail="intent-dispatch requires a valid X-Internal-Token "
                   "(ZOE_INTENT_DISPATCH_REQUIRE_TOKEN is enabled)",
        )
    if _is_internal_request(request):
        if not _has_valid_internal_token(request):
            client_host = request.client.host if request.client else "?"
            logger.warning(
                "intent-dispatch caller %s trusted via loopback WITHOUT a "
                "proven X-Internal-Token — provision ZOE_INTERNAL_TOKEN for "
                "this caller, then set ZOE_INTENT_DISPATCH_REQUIRE_TOKEN=1",
                client_host,
            )
        return
    raise HTTPException(
        status_code=403,
        detail="Internal endpoint: loopback or valid X-Internal-Token required",
    )


def _has_valid_internal_token(request: Request) -> bool:
    """True only when ZOE_INTERNAL_TOKEN is configured AND the request carries it.

    Deliberately STRICTER than _is_internal_request: loopback alone does not
    qualify. Acting-as-another-user is the highest-privilege internal capability,
    and loopback is reachable by any SSRF'd or compromised local process — the
    residual the 2026-07-04 review flagged. Unset token ⇒ always False (the
    override is disabled until the operator provisions the secret).
    """
    if not _ZOE_INTERNAL_TOKEN:
        return False
    provided = request.headers.get("X-Internal-Token", "")
    return bool(provided) and hmac.compare_digest(provided, _ZOE_INTERNAL_TOKEN)


async def resolve_acting_user(request: Request) -> dict:
    """get_current_user, plus a TOKEN-PROVEN internal-caller identity override.

    An internal caller that presents a valid ``X-Internal-Token`` MAY specify
    the acting user via the ``X-Zoe-User-Id`` header. This lets the Telegram
    bridge forward a verified, already-resolved Zoe user_id so the turn runs as
    that real user (with their memory) instead of guest.

    SECURITY: the override requires the shared secret — being loopback is NOT
    enough (unlike require_internal_token's boundary). A loopback SSRF or
    compromised local process must not inherit impersonation. If
    ZOE_INTERNAL_TOKEN is unset, the override is disabled entirely and we log
    why, so a missing provisioning step surfaces in the journal instead of as
    silently-degraded Telegram identity. A public/browser request that sets
    X-Zoe-User-Id is ignored and falls through to normal session/guest auth.
    Session auth still runs for internal callers that do NOT send the header,
    so existing loopback session flows are unchanged.
    """
    override = request.headers.get("X-Zoe-User-Id", "").strip()
    if override:
        if _has_valid_internal_token(request):
            return {
                "user_id": override,
                # A forwarded, pre-resolved real user acts as a normal member;
                # the bridge is not an admin console. Downstream role checks
                # still apply.
                "role": "user",
                "username": override,
                "permissions": [],
            }
        if _is_internal_request(request):
            logger.warning(
                "X-Zoe-User-Id override DENIED for internal caller: %s — "
                "loopback alone no longer grants impersonation; the caller must "
                "send X-Internal-Token matching ZOE_INTERNAL_TOKEN%s",
                override,
                "" if _ZOE_INTERNAL_TOKEN else " (which is NOT provisioned on this host)",
            )
    # No trusted override → standard session / device-token / guest resolution.
    return await get_current_user(request)
