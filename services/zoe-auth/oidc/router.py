"""Native OIDC provider endpoints for zoe-auth."""
import base64
import hashlib
import html
import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.auth import auth_manager
from core.sessions import (
    AuthenticationRequest,
    AuthMethod,
    SessionType,
    session_manager,
)
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
            "SELECT user_id, username, email, role, is_verified FROM auth_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    # email_verified must be carried into the OIDC id_token — clients (e.g. Omnigent)
    # hard-reject the email claim unless email_verified is true.
    return {
        "user_id": row[0],
        "username": row[1],
        "email": row[2],
        "role": row[3],
        "email_verified": bool(row[4]),
    }


def _get_user_from_session(session_id: str) -> str | None:
    """Return user_id for a valid, non-expired session, or None."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM auth_sessions WHERE session_id = ? AND is_active = 1 AND expires_at > ?",
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
        "scopes_supported": ["openid", "email", "profile", "groups"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "none",
        ],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": [
            "sub", "iss", "aud", "exp", "iat", "auth_time", "nonce",
            "email", "email_verified", "name", "preferred_username", "role", "groups",
            "zoe_user_id",
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
    return RedirectResponse(
        f"/application/o/login?oidc_state_id={oidc_state_id}", status_code=302
    )


# ---------------------------------------------------------------------------
# Login page (server-rendered) — bridges password auth into a zoe_session cookie
# so the OIDC flow completes without depending on the SPA. Used by every OIDC
# client (Omnigent, Home Assistant, Multica). The main app uses header-based
# (X-Session-ID) sessions, which a browser redirect to /authorize can't carry,
# so the consent flow needs its own cookie-setting login.
#
# Test coverage: tests/test_oidc_login.py exercises _login_page_html escaping, the
# GET page (expired-state->400, valid form, error allow-list) and the POST submit
# (expired state, bad credentials, unknown user, SETUP_REQUIRED hash, success ->
# zoe_session cookie + resume to authorize/complete).
# ---------------------------------------------------------------------------

def _login_page_html(oidc_state_id: str, error: str = "") -> str:
    # Defense-in-depth: oidc_state_id is only ever a server-generated UUID and
    # error is mapped through a fixed allow-list, but escape both reflected
    # values so the form can never become an HTML-injection sink.
    oidc_state_id = html.escape(oidc_state_id, quote=True)
    err_html = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in to Zoe</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e8e8e8;
         display:flex; min-height:100vh; align-items:center; justify-content:center; margin:0; }}
  form {{ background:#1a1d24; padding:2rem; border-radius:12px; width:320px;
          box-shadow:0 8px 30px rgba(0,0,0,.4); }}
  h1 {{ font-size:1.25rem; margin:0 0 1rem; }}
  label {{ display:block; font-size:.8rem; margin:.75rem 0 .25rem; color:#9aa0aa; }}
  input {{ width:100%; box-sizing:border-box; padding:.6rem; border-radius:8px;
           border:1px solid #2c313c; background:#0f1115; color:#e8e8e8; }}
  button {{ width:100%; margin-top:1.25rem; padding:.65rem; border:0; border-radius:8px;
            background:#4f8cff; color:#fff; font-weight:600; cursor:pointer; }}
  .err {{ color:#ff6b6b; font-size:.85rem; margin:.5rem 0 0; }}
  .sub {{ color:#6b7280; font-size:.75rem; margin-top:1rem; text-align:center; }}
</style></head>
<body>
  <form method="post" action="/application/o/login">
    <h1>Sign in to Zoe</h1>
    <input type="hidden" name="oidc_state_id" value="{oidc_state_id}">
    <label for="u">Username</label>
    <input id="u" name="username" autocomplete="username" autofocus required>
    <label for="p">Password</label>
    <input id="p" name="password" type="password" autocomplete="current-password" required>
    {err_html}
    <button type="submit">Sign in</button>
    <p class="sub">Authorizing an application to use your Zoe account.</p>
  </form>
</body></html>"""


@router.get("/application/o/login", response_class=HTMLResponse)
async def oidc_login_page(oidc_state_id: str, error: str = ""):
    if get_pending_auth(oidc_state_id) is None:
        return HTMLResponse(
            _login_page_html("", "This login request expired — please retry from the app."),
            status_code=400,
        )
    msg = {
        "invalid": "Invalid username or password.",
        "session_required": "Please sign in to continue.",
        "session_expired": "Your session expired — please sign in again.",
    }.get(error, "")
    return HTMLResponse(_login_page_html(oidc_state_id, msg))


@router.post("/application/o/login")
async def oidc_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    oidc_state_id: str = Form(...),
):
    if get_pending_auth(oidc_state_id) is None:
        return RedirectResponse("/?error=oidc_expired", status_code=302)

    ip_address = request.client.host if request.client else None

    # Match either username or user_id, case-insensitively — the username column is
    # capitalized (e.g. "Jason") while user_id is lowercase ("jason"), and people type
    # either in any case.
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id, password_hash FROM auth_users"
            " WHERE LOWER(username) = LOWER(?) OR LOWER(user_id) = LOWER(?)",
            (username, username),
        ).fetchone()

    bad = RedirectResponse(
        f"/application/o/login?oidc_state_id={oidc_state_id}&error=invalid",
        status_code=302,
    )
    if not row or row[1] in (None, "SETUP_REQUIRED"):
        return bad

    user_id = row[0]
    auth_result = auth_manager.verify_password(user_id, password, ip_address)
    if not auth_result.success:
        return bad

    session_result = session_manager.authenticate(
        AuthenticationRequest(
            user_id=auth_result.user_id,
            auth_method=AuthMethod.PASSWORD,
            # session_manager.authenticate re-verifies via _verify_password_auth ->
            # verify_password(user_id, credentials["password"]); an empty credentials
            # dict would verify an empty password and fail, so the password is
            # required here (mirrors the SPA login path).
            credentials={"password": password},
            device_info={},
            ip_address=ip_address,
            user_agent=request.headers.get("user-agent"),
            requested_session_type=SessionType.STANDARD,
        )
    )
    if not session_result.success or not session_result.session:
        return bad

    # Set the zoe_session cookie the OIDC endpoints read, then resume the flow.
    resp = RedirectResponse(
        f"/application/o/authorize/complete?oidc_state_id={oidc_state_id}",
        status_code=302,
    )
    resp.set_cookie(
        "zoe_session",
        session_result.session.session_id,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
        path="/",
    )
    return resp


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
            f"/application/o/login?oidc_state_id={oidc_state_id}&error=session_required",
            status_code=302,
        )

    user_id = _get_user_from_session(zoe_session)
    if not user_id:
        return RedirectResponse(
            f"/application/o/login?oidc_state_id={oidc_state_id}&error=session_expired",
            status_code=302,
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

def _parse_basic_auth(authorization: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse an HTTP Basic ``Authorization`` header into (client_id, secret).

    Supports the ``client_secret_basic`` token-endpoint auth method (RFC 6749
    §2.3.1): ``Basic base64(urlencode(client_id):urlencode(client_secret))``.
    Returns (None, None) when the header is absent or malformed.
    """
    if not authorization or not authorization.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8")
    except Exception:
        return None, None
    basic_id, sep, basic_secret = decoded.partition(":")
    if not sep:
        return None, None
    # RFC 6749 §2.3.1: id/secret are application/x-www-form-urlencoded, so '+'
    # decodes to a space (unquote_plus), not left literal.
    from urllib.parse import unquote_plus
    return unquote_plus(basic_id), unquote_plus(basic_secret)


@router.post("/application/o/token/")
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    code_verifier: str = Form(...),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    if grant_type != "authorization_code":
        raise HTTPException(400, "Only grant_type=authorization_code is supported")

    # A client_secret_basic client may send its id + secret ONLY in the
    # Authorization header, so client_id is optional in the form and resolved
    # from whichever source provided it (rejecting a conflicting pair).
    basic_id, basic_secret = _parse_basic_auth(authorization)
    if client_id and basic_id and client_id != basic_id:
        raise HTTPException(401, detail={"error": "invalid_client"})
    client_id = client_id or basic_id
    if not client_id:
        raise HTTPException(401, detail={"error": "invalid_client"})
    client_secret = client_secret or basic_secret

    client = get_client(client_id)
    if client is None or not client["is_active"]:
        raise HTTPException(401, detail={"error": "invalid_client"})

    # Confidential clients (a client_secret_hash is registered) MUST present a
    # valid client_secret. Previously the secret was only checked when one was
    # supplied, so a confidential client could omit it and authenticate on PKCE
    # alone. Public clients (no registered secret) continue to rely on PKCE.
    if client["client_secret_hash"]:
        if not client_secret or not verify_secret(client_secret, client["client_secret_hash"]):
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

    role = user_info.get("role", "user")
    return JSONResponse({
        "sub": user_id,
        "name": user_info.get("username", ""),
        "email": user_info.get("email", ""),
        "email_verified": True,
        "preferred_username": user_info.get("username", ""),
        "role": role,
        "groups": [role],
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
                "UPDATE auth_sessions SET is_active = 0 WHERE session_id = ?",
                (zoe_session,),
            )

    if post_logout_redirect_uri:
        response = RedirectResponse(post_logout_redirect_uri, status_code=302)
    else:
        response = RedirectResponse("/", status_code=302)

    response.delete_cookie("zoe_session")
    return response
