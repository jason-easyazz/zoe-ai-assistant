"""OIDC client registration and secret verification."""
import json
from datetime import datetime, timezone
import bcrypt
from models.database import get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_secret(secret: str) -> str:
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()


def verify_secret(secret: str, secret_hash: str) -> bool:
    try:
        return bcrypt.checkpw(secret.encode(), secret_hash.encode())
    except Exception:
        return False


def get_client(client_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT client_id, client_secret_hash, client_name, redirect_uris, scopes, is_active"
            " FROM oidc_clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "client_id": row[0],
        "client_secret_hash": row[1],
        "client_name": row[2],
        "redirect_uris": json.loads(row[3]),
        "scopes": row[4].split(),
        "is_active": row[5],
    }


def validate_redirect_uri(client: dict, redirect_uri: str) -> bool:
    """Exact match only — no wildcards."""
    return redirect_uri in client["redirect_uris"]


def upsert_client(
    client_id: str,
    client_name: str,
    secret: str,
    redirect_uris: list[str],
    scopes: str = "openid profile email",
) -> None:
    """Insert or update a client. Safe to call on every startup."""
    secret_hash = hash_secret(secret)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO oidc_clients (client_id, client_secret_hash, client_name, redirect_uris, scopes, is_active, created_at)"
            " VALUES (?, ?, ?, ?, ?, TRUE, ?)"
            " ON CONFLICT (client_id) DO UPDATE SET client_secret_hash = EXCLUDED.client_secret_hash,"
            " redirect_uris = EXCLUDED.redirect_uris, scopes = EXCLUDED.scopes, is_active = TRUE",
            (client_id, secret_hash, client_name, json.dumps(redirect_uris), scopes, _now_iso()),
        )
