"""
Auth Integration for zoe-core
- Validates X-Session-ID against zoe-auth
- Provides FastAPI dependency to require session and extract user_id/permissions
"""
import os
import httpx
from fastapi import Header, HTTPException, Depends
from typing import Optional, Dict, Any

ZOE_AUTH_URL = os.getenv("ZOE_AUTH_INTERNAL_URL", "http://zoe-auth:8002")

class AuthenticatedSession:
    def __init__(self, session_id: str, user_id: str, permissions: list, role: str):
        self.session_id = session_id
        self.user_id = user_id
        self.permissions = permissions or []
        self.role = role

async def validate_session(x_session_id: Optional[str] = Header(None, alias="X-Session-ID")) -> AuthenticatedSession:
    if not x_session_id:
        raise HTTPException(status_code=401, detail="Missing X-Session-ID")
    validate_url = f"{ZOE_AUTH_URL}/api/auth/user"
    headers = {"X-Session-ID": x_session_id}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(validate_url, headers=headers)
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid or expired session")
            resp.raise_for_status()
            data = resp.json()
            return AuthenticatedSession(
                session_id=x_session_id,
                user_id=data.get("user_id"),
                permissions=data.get("permissions", []),
                role=data.get("role", "user"),
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Auth service unavailable")


def require_permission(required: str):
    def _dep(session: AuthenticatedSession = Depends(validate_session)) -> AuthenticatedSession:
        from fnmatch import fnmatch
        if any(fnmatch(required, p) or fnmatch(p, required) for p in session.permissions):
            return session
        raise HTTPException(status_code=403, detail="Permission denied")
    return _dep

