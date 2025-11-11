"""
Auth Integration for zoe-core
- Validates X-Session-ID against zoe-auth
- Provides FastAPI dependency to require session and extract user_id/permissions
- Allows localhost access in development mode for developer tools
"""
import os
import httpx
from fastapi import Header, HTTPException, Depends, Request
from typing import Optional, Dict, Any

ZOE_AUTH_URL = os.getenv("ZOE_AUTH_INTERNAL_URL", "http://zoe-auth:8002")
DEV_MODE = os.getenv("ZOE_DEV_MODE", "false").lower() in ("true", "1", "yes")  # More flexible parsing

class AuthenticatedSession:
    def __init__(self, session_id: str, user_id: str, permissions: list, role: str, dev_bypass: bool = False):
        self.session_id = session_id
        self.user_id = user_id
        self.permissions = permissions or []
        self.role = role
        self.dev_bypass = dev_bypass

def is_localhost_request(request: Request) -> bool:
    """Check if request is from localhost or Docker bridge network"""
    client_host = request.client.host if request.client else None
    
    # Allow localhost and Docker bridge networks (172.x.x.x for Docker internal routing)
    if client_host in ["127.0.0.1", "localhost", "::1", None]:
        return True
    
    # Allow Docker bridge network (usually 172.x.x.x)
    if client_host and client_host.startswith("172."):
        return True
    
    # Allow private network ranges (for local development)
    if client_host and (client_host.startswith("192.168.") or client_host.startswith("10.")):
        return True
    
    return False

async def validate_session(
    request: Request,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID")
) -> AuthenticatedSession:
    """Validate session with development mode bypass for localhost"""
    import logging
    auth_logger = logging.getLogger(__name__)
    
    # ALWAYS allow dev-localhost session ID if provided (for testing)
    if x_session_id == "dev-localhost":
        auth_logger.info("✅ DEV-LOCALHOST session accepted")
        return AuthenticatedSession(
            session_id="dev-localhost",
            user_id="developer",
            permissions=["*"],
            role="admin",
            dev_bypass=True
        )
    
    # Check DEV_MODE dynamically (in case it's set after module load)
    current_dev_mode = os.getenv("ZOE_DEV_MODE", "false").lower() in ("true", "1", "yes")
    is_dev = DEV_MODE or current_dev_mode
    
    # Check if request is from localhost or Docker network
    client_host = request.client.host if request.client else None
    
    # Development mode: Allow localhost/Docker network access
    if is_dev:
        # Allow localhost/Docker network requests
        if (client_host in ["127.0.0.1", "localhost", "::1", None] or
            (client_host and (client_host.startswith("172.") or 
                              client_host.startswith("192.168.") or 
                              client_host.startswith("10.")))):
            auth_logger.info(f"✅ DEV MODE: Allowing request from {client_host}")
            return AuthenticatedSession(
                session_id="dev-localhost",
                user_id="developer",
                permissions=["*"],
                role="admin",
                dev_bypass=True
            )
    
    # Production mode: Require authentication
    if not x_session_id:
        auth_logger.warning(f"❌ Missing X-Session-ID header")
        raise HTTPException(status_code=401, detail="Missing X-Session-ID")
    
    auth_logger.info(f"🔍 Validating session: {x_session_id[:20]}...")
    
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
                dev_bypass=False
            )
    except HTTPException:
        raise
    except Exception:
        # If auth service is unavailable in dev mode, allow access
        if DEV_MODE:
            return AuthenticatedSession(
                session_id="dev-fallback",
                user_id="developer",
                permissions=["*"],
                role="admin",
                dev_bypass=True
            )
        raise HTTPException(status_code=502, detail="Auth service unavailable")


def require_permission(required: str):
    def _dep(session: AuthenticatedSession = Depends(validate_session)) -> AuthenticatedSession:
        # Dev bypass has all permissions
        if session.dev_bypass:
            return session
            
        from fnmatch import fnmatch
        if any(fnmatch(required, p) or fnmatch(p, required) for p in session.permissions):
            return session
        raise HTTPException(status_code=403, detail="Permission denied")
    return _dep
