"""Auth code issuance and consumption using oauth_states table."""
import json
import secrets
from datetime import datetime, timezone, timedelta
from models.database import get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_auth_code(
    client_id: str,
    redirect_uri: str,
    user_id: str,
    scope: str,
    code_challenge: str,
    code_challenge_method: str,
    nonce: str | None,
) -> str:
    """Issue a single-use auth code valid for 60 seconds."""
    code = secrets.token_urlsafe(32)
    data = json.dumps({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "user_id": user_id,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "nonce": nonce,
        "used": False,
    })
    with get_db() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, data, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (code, data, _now_iso(), _future_iso(60)),
        )
    return code


def consume_auth_code(code: str) -> dict | None:
    """
    Atomically read and delete an auth code.
    Returns the payload dict, or None if not found / expired / already used.
    """
    now = _now_iso()
    with get_db() as conn:
        row = conn.execute(
            "SELECT data, expires_at FROM oauth_states WHERE state = ?",
            (code,),
        ).fetchone()
        if row is None:
            return None
        data_str, expires_at = row
        if expires_at < now:
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (code,))
            return None
        payload = json.loads(data_str)
        if payload.get("used"):
            return None
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (code,))
    return payload


def store_pending_auth(oidc_state_id: str, params: dict, ttl_seconds: int = 600) -> None:
    """Store pending OIDC params while user is redirected to login page."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, data, created_at, expires_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (state) DO UPDATE SET data = EXCLUDED.data, expires_at = EXCLUDED.expires_at",
            (oidc_state_id, json.dumps(params), _now_iso(), _future_iso(ttl_seconds)),
        )


def get_pending_auth(oidc_state_id: str) -> dict | None:
    """Retrieve (but do NOT delete) pending OIDC params."""
    now = _now_iso()
    with get_db() as conn:
        row = conn.execute(
            "SELECT data, expires_at FROM oauth_states WHERE state = ?",
            (oidc_state_id,),
        ).fetchone()
    if row is None:
        return None
    data_str, expires_at = row
    if expires_at < now:
        return None
    return json.loads(data_str)


def delete_pending_auth(oidc_state_id: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (oidc_state_id,))
