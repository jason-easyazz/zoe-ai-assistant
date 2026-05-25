"""Tests for OIDC JWT issuance and verification."""

from cryptography.hazmat.primitives import serialization
from jose import jwk, jwt

from oidc import tokens
from oidc.keys import generate_rsa_key


def _jwks_for(key: dict) -> dict:
    public_key = serialization.load_pem_public_key(key["public_key_pem"].encode())
    key_dict = jwk.construct(public_key, algorithm="RS256").to_dict()
    key_dict["kid"] = key["kid"]
    key_dict["use"] = "sig"
    key_dict["alg"] = "RS256"
    return {"keys": [key_dict]}


def test_issue_id_token_uses_user_email_verified_claim(monkeypatch):
    key = generate_rsa_key()
    monkeypatch.setattr(tokens, "ensure_signing_key", lambda: key)

    token = tokens.issue_id_token(
        issuer="https://zoe.example",
        subject="user-1",
        audience="client-1",
        user_info={
            "username": "zoe",
            "email": "zoe@example.test",
            "email_verified": False,
            "role": "user",
        },
        nonce="nonce-1",
    )

    claims = jwt.decode(
        token,
        key["public_key_pem"],
        algorithms=["RS256"],
        audience="client-1",
        issuer="https://zoe.example",
    )
    assert claims["email"] == "zoe@example.test"
    assert claims["email_verified"] is False
    assert claims["nonce"] == "nonce-1"


def test_issue_and_verify_access_token(monkeypatch):
    key = generate_rsa_key()
    monkeypatch.setattr(tokens, "ensure_signing_key", lambda: key)

    token = tokens.issue_access_token(
        issuer="https://zoe.example",
        subject="user-1",
        client_id="client-1",
        scope="openid profile",
    )

    claims = tokens.verify_access_token(token, "https://zoe.example", _jwks_for(key))
    assert claims is not None
    assert claims["sub"] == "user-1"
    assert claims["client_id"] == "client-1"
    assert claims["scope"] == "openid profile"


def test_verify_access_token_rejects_unknown_key(monkeypatch):
    key = generate_rsa_key()
    monkeypatch.setattr(tokens, "ensure_signing_key", lambda: key)

    token = tokens.issue_access_token(
        issuer="https://zoe.example",
        subject="user-1",
        client_id="client-1",
        scope="openid",
    )

    assert tokens.verify_access_token(token, "https://zoe.example", {"keys": []}) is None
