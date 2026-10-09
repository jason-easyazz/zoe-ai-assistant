"""RSA signing key management for the OIDC provider."""
import uuid
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from models.database import get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_rsa_key() -> dict:
    """Generate a new RSA-2048 key pair and return as dict."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return {
        "kid": str(uuid.uuid4()),
        "private_key_pem": private_pem,
        "public_key_pem": public_pem,
    }


def get_active_key() -> dict | None:
    """Return the active signing key row from DB, or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT kid, private_key_pem, public_key_pem FROM oidc_signing_keys"
            " WHERE is_active = TRUE ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {"kid": row[0], "private_key_pem": row[1], "public_key_pem": row[2]}


def ensure_signing_key() -> dict:
    """Return the active key, generating and storing one if needed."""
    key = get_active_key()
    if key:
        return key
    new_key = generate_rsa_key()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO oidc_signing_keys (kid, algorithm, private_key_pem, public_key_pem, created_at, is_active)"
            " VALUES (?, 'RS256', ?, ?, ?, TRUE)",
            (new_key["kid"], new_key["private_key_pem"], new_key["public_key_pem"], _now_iso()),
        )
    return new_key


def get_jwks() -> dict:
    """Return JWKS dict with all active public keys."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT kid, public_key_pem FROM oidc_signing_keys WHERE is_active = TRUE"
        ).fetchall()
    keys = []
    for kid, public_pem in rows:
        public_key = serialization.load_pem_public_key(public_pem.encode())
        key_dict = RSAAlgorithm.to_jwk(public_key, as_dict=True)
        # PyJWT adds key_ops=["verify"]; python-jose never did. Keep the
        # published JWKS member set identical so relying parties see no change.
        key_dict.pop("key_ops", None)
        key_dict["kid"] = kid
        key_dict["use"] = "sig"
        key_dict["alg"] = "RS256"
        keys.append(key_dict)
    return {"keys": keys}
