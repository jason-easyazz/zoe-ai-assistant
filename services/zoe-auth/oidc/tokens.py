"""JWT token issuance for the OIDC provider."""
import logging
import time
from jose import jwt
from oidc.keys import ensure_signing_key

logger = logging.getLogger(__name__)


def issue_id_token(
    issuer: str,
    subject: str,
    audience: str,
    user_info: dict,
    nonce: str | None,
    auth_time: int | None = None,
    ttl_seconds: int = 3600,
) -> str:
    key = ensure_signing_key()
    now = int(time.time())
    role = user_info.get("role", "user")
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "exp": now + ttl_seconds,
        "iat": now,
        "auth_time": auth_time or now,
        "name": user_info.get("username", ""),
        "email": user_info.get("email", ""),
        "email_verified": bool(user_info.get("email_verified", False)),
        "preferred_username": user_info.get("username", ""),
        "role": role,
        # Array form of `role`. Relying parties map roles from a list claim and
        # discard a bare string (Home Assistant's auth_oidc does so silently),
        # so `role` alone cannot drive an RP's admin mapping.
        "groups": [role],
        "zoe_user_id": subject,
    }
    if nonce:
        claims["nonce"] = nonce
    return jwt.encode(
        claims,
        key["private_key_pem"],
        algorithm="RS256",
        headers={"kid": key["kid"]},
    )


def issue_access_token(
    issuer: str,
    subject: str,
    client_id: str,
    scope: str,
    ttl_seconds: int = 3600,
) -> str:
    key = ensure_signing_key()
    now = int(time.time())
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": "zoe-auth",
        "client_id": client_id,
        "scope": scope,
        "exp": now + ttl_seconds,
        "iat": now,
    }
    return jwt.encode(
        claims,
        key["private_key_pem"],
        algorithm="RS256",
        headers={"kid": key["kid"]},
    )


def verify_access_token(token: str, issuer: str, jwks: dict) -> dict | None:
    """Verify and decode an access token. Returns claims or None."""
    try:
        from jose import jwk as jose_jwk
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        key_data = next((k for k in jwks["keys"] if k.get("kid") == kid), None)
        if key_data is None:
            return None
        public_key = jose_jwk.construct(key_data, algorithm="RS256")
        claims = jwt.decode(
            token,
            public_key.to_pem().decode(),
            algorithms=["RS256"],
            audience="zoe-auth",
            issuer=issuer,
        )
        return claims
    except Exception:  # noqa: BLE001 - callers treat None as an unverified token.
        logger.debug("verify_access_token failed", exc_info=True)
        return None
