"""Native OIDC provider endpoints for zoe-auth."""
import base64
import hashlib
import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from oidc.clients import get_client, validate_redirect_uri, verify_secret
from oidc.codes import (
    consume_auth_code,
    delete_pending_auth,
    get_pending_auth,
    issue_auth_code,
    store_pending_auth,
)
from oidc.keys import get_jwks
from oidc.tokens import issue_access_token, issue_id_token, verify_access_token
from models.database import get_db

router = APIRouter()


def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{scheme}://{host}"


def _get_user_info(user_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id, username, email, role FROM auth_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {"user_id": row[0], "username": row[1], "email": row[2], "role": row[3]}


def _get_user_from_session(session_id: str) -> str | None:
    """Return user_id for a valid, non-expired session, or None."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM auth_sessions WHERE session_id = ? AND is_active = TRUE AND expires_at > ?",
            (session_id, now),
        ).fetchone()
    return row[0] if row else None


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return computed == code_challenge


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@router.get("/.well-known/openid-configuration")
async def discovery(request: Request):
    base = _base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/application/o/authorize/",
        "token_endpoint": f"{base}/application/o/token/",
        "userinfo_endpoint": f"{base}/application/o/userinfo/",
        "jwks_uri": f"{base}/jwks.json",
        "end_session_endpoint": f"{base}/application/o/end-session/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "scopes_supported": ["openid", "email", "profile"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "none",
        ],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": [
            "sub", "iss", "aud", "exp", "iat", "auth_time", "nonce",
            "email", "email_verified", "name", "preferred_username", "role", "zoe_user_id",
        ],
    })


# ---------------------------------------------------------------------------
# JWKS
# ---------------------------------------------------------------------------

@router.get("/jwks.json")
async def jwks():
    return JSONResponse(get_jwks())


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------

@router.get("/application/o/authorize/")
async def authorize(
    request: Request,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    scope: str = "openid",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    nonce: Optional[str] = None,
    zoe_session: Optional[str] = Cookie(None),
):
    if response_type != "code":
        raise HTTPException(400, "Only response_type=code is supported")

    if not code_challenge:
        raise HTTPException(400, "code_challenge is required (PKCE S256)")
    if code_challenge_method != "S256":
        raise HTTPException(400, "Only code_challenge_method=S256 is supported")

    client = get_client(client_id)
    if client is None or not client["is_active"]:
        raise HTTPException(400, "Unknown client_id")

    if not validate_redirect_uri(client, redirect_uri):
        raise HTTPException(400, "redirect_uri does not match registered URIs")

    user_id = None
    if zoe_session:
        user_id = _get_user_from_session(zoe_session)

    if user_id:
        code = issue_auth_code(
            client_id=client_id,
            redirect_uri=redirect_uri,
            user_id=user_id,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            nonce=nonce,
        )
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)

    oidc_state_id = str(uuid.uuid4())
    store_pending_auth(oidc_state_id, {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "nonce": nonce,
    })
    return RedirectResponse(f"/?oidc_state_id={oidc_state_id}", status_code=302)


# ---------------------------------------------------------------------------
# Post-login completion
# ---------------------------------------------------------------------------

@router.get("/application/o/authorize/complete")
async def authorize_complete(
    request: Request,
    oidc_state_id: str,
    zoe_session: Optional[str] = Cookie(None),
):
    if not zoe_session:
        return RedirectResponse(
            f"/?oidc_state_id={oidc_state_id}&error=session_required", status_code=302
        )

    user_id = _get_user_from_session(zoe_session)
    if not user_id:
        return RedirectResponse(
            f"/?oidc_state_id={oidc_state_id}&error=session_expired", status_code=302
        )

    params = get_pending_auth(oidc_state_id)
    if params is None:
        return RedirectResponse("/?error=oidc_expired", status_code=302)

    delete_pending_auth(oidc_state_id)

    code = issue_auth_code(
        client_id=params["client_id"],
        redirect_uri=params["redirect_uri"],
        user_id=user_id,
        scope=params["scope"],
        code_challenge=params["code_challenge"],
        code_challenge_method=params["code_challenge_method"],
        nonce=params.get("nonce"),
    )
    redirect_uri = params["redirect_uri"]
    state = params.get("state", "")
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------

@router.post("/application/o/token/")
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    code_verifier: str = Form(...),
    client_secret: Optional[str] = Form(None),
):
    if grant_type != "authorization_code":
        raise HTTPException(400, "Only grant_type=authorization_code is supported")

    client = get_client(client_id)
    if client is None or not client["is_active"]:
        raise HTTPException(401, detail={"error": "invalid_client"})

    if client["client_secret_hash"] and client_secret:
        if not verify_secret(client_secret, client["client_secret_hash"]):
            raise HTTPException(401, detail={"error": "invalid_client"})

    payload = consume_auth_code(code)
    if payload is None:
        raise HTTPException(
            400, detail={"error": "invalid_grant", "error_description": "Code expired or already used"}
        )

    if payload["redirect_uri"] != redirect_uri:
        raise HTTPException(400, detail={"error": "invalid_grant"})

    if payload["client_id"] != client_id:
        raise HTTPException(400, detail={"error": "invalid_grant"})

    if not _verify_pkce(code_verifier, payload["code_challenge"]):
        raise HTTPException(
            400, detail={"error": "invalid_grant", "error_description": "PKCE verification failed"}
        )

    user_id = payload["user_id"]
    user_info = _get_user_info(user_id)
    if user_info is None:
        raise HTTPException(
            400, detail={"error": "invalid_grant", "error_description": "User not found"}
        )

    issuer = _base_url(request)
    scope = payload.get("scope", "openid")
    nonce = payload.get("nonce")

    id_token = issue_id_token(
        issuer=issuer,
        subject=user_id,
        audience=client_id,
        user_info=user_info,
        nonce=nonce,
    )
    access_token = issue_access_token(
        issuer=issuer,
        subject=user_id,
        client_id=client_id,
        scope=scope,
    )

    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "id_token": id_token,
        "scope": scope,
    })


# ---------------------------------------------------------------------------
# UserInfo endpoint
# ---------------------------------------------------------------------------

@router.get("/application/o/userinfo/")
@router.post("/application/o/userinfo/")
async def userinfo(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")

    token_str = authorization.removeprefix("Bearer ").strip()
    issuer = _base_url(request)
    jwks_data = get_jwks()
    claims = verify_access_token(token_str, issuer, jwks_data)
    if claims is None:
        raise HTTPException(401, "Invalid or expired token")

    user_id = claims.get("sub")
    user_info = _get_user_info(user_id)
    if user_info is None:
        raise HTTPException(404, "User not found")

    return JSONResponse({
        "sub": user_id,
        "name": user_info.get("username", ""),
        "email": user_info.get("email", ""),
        "email_verified": True,
        "preferred_username": user_info.get("username", ""),
        "role": user_info.get("role", "user"),
        "zoe_user_id": user_id,
    })


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------

@router.get("/application/o/end-session/")
@router.post("/application/o/end-session/")
async def end_session(
    request: Request,
    post_logout_redirect_uri: Optional[str] = None,
    id_token_hint: Optional[str] = None,
    zoe_session: Optional[str] = Cookie(None),
):
    if zoe_session:
        with get_db() as conn:
            conn.execute(
                "UPDATE auth_sessions SET is_active = FALSE WHERE session_id = ?",
                (zoe_session,),
            )

    if post_logout_redirect_uri:
        response = RedirectResponse(post_logout_redirect_uri, status_code=302)
    else:
        response = RedirectResponse("/", status_code=302)

    response.delete_cookie("zoe_session")
    return response
