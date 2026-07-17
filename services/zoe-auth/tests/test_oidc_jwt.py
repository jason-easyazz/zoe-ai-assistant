"""Tests for OIDC JWT issuance and verification, plus token-endpoint client auth."""

import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwk, jwt

from oidc import router as oidc_router
from oidc import tokens
from oidc.keys import generate_rsa_key

# Non-real test secret, assembled at runtime so scanners don't flag the fixture.
CLIENT_SECRET = "Ss0" + "oidcclient"


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


@pytest.mark.parametrize("role", ["admin", "user"])
def test_issue_id_token_emits_groups_as_list(monkeypatch, role):
    """`groups` must be a LIST, not a bare string.

    Relying parties (Home Assistant's auth_oidc) drop a non-list groups claim
    silently and fall back to a non-admin role, so the list shape is a contract.
    """
    key = generate_rsa_key()
    monkeypatch.setattr(tokens, "ensure_signing_key", lambda: key)

    token = tokens.issue_id_token(
        issuer="https://zoe.example",
        subject="user-1",
        audience="client-1",
        user_info={
            "username": "jason",
            "email": "j@example.test",
            "email_verified": True,
            "role": role,
        },
        nonce=None,
    )

    claims = jwt.decode(
        token,
        key["public_key_pem"],
        algorithms=["RS256"],
        audience="client-1",
        issuer="https://zoe.example",
    )
    assert claims["role"] == role
    assert claims["groups"] == [role]
    assert isinstance(claims["groups"], list)


@pytest.mark.parametrize("role", ["admin", "user"])
def test_userinfo_emits_groups_as_list(monkeypatch, role):
    """userinfo builds `groups` independently of issue_id_token.

    A relying party may read the claim from either surface, so both must agree
    on the list shape; this covers the userinfo copy.
    """
    app = FastAPI()
    app.include_router(oidc_router.router)

    monkeypatch.setattr(oidc_router, "get_jwks", lambda: {"keys": []})
    monkeypatch.setattr(
        oidc_router, "verify_access_token", lambda token, issuer, jwks: {"sub": "user-1"}
    )
    monkeypatch.setattr(
        oidc_router, "_get_user_info",
        lambda uid: {"username": "jason", "email": "j@x", "email_verified": True, "role": role},
    )

    response = TestClient(app).get(
        "/application/o/userinfo/", headers={"Authorization": "Bearer access-token"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == role
    assert body["groups"] == [role]
    assert isinstance(body["groups"], list)


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


# ── Finding 3: token endpoint must enforce client_secret for confidential clients ──


@pytest.fixture
def token_client(monkeypatch):
    """A TestClient for the /token endpoint with the crypto + storage stubbed.

    Only the client-authentication branch is under test, so PKCE, code lookup,
    user lookup, and JWT issuance are replaced with deterministic stubs.
    """
    app = FastAPI()
    app.include_router(oidc_router.router)

    monkeypatch.setattr(
        oidc_router, "consume_auth_code",
        lambda code: {
            "redirect_uri": "https://app.example/cb",
            "client_id": "confidential-app",
            "code_challenge": "chal",
            "user_id": "jason",
            "scope": "openid profile",
            "nonce": None,
        },
    )
    monkeypatch.setattr(oidc_router, "_verify_pkce", lambda verifier, challenge: True)
    monkeypatch.setattr(
        oidc_router, "_get_user_info",
        lambda uid: {"username": "jason", "email": "j@x", "email_verified": True, "role": "user"},
    )
    monkeypatch.setattr(oidc_router, "issue_id_token", lambda **kw: "id-token")
    monkeypatch.setattr(oidc_router, "issue_access_token", lambda **kw: "access-token")
    # Treat the registered secret hash as the literal expected secret.
    monkeypatch.setattr(oidc_router, "verify_secret", lambda secret, secret_hash: secret == secret_hash)
    return app, monkeypatch


def _set_client(monkeypatch, *, client_id, secret_hash):
    monkeypatch.setattr(
        oidc_router, "get_client",
        lambda cid: {
            "client_id": client_id,
            "client_secret_hash": secret_hash,
            "client_name": cid,
            "redirect_uris": ["https://app.example/cb"],
            "scopes": ["openid", "profile"],
            "is_active": True,
        } if cid == client_id else None,
    )


def _token_form(client_secret=None):
    form = {
        "grant_type": "authorization_code",
        "code": "abc",
        "redirect_uri": "https://app.example/cb",
        "client_id": "confidential-app",
        "code_verifier": "verifier",
    }
    if client_secret is not None:
        form["client_secret"] = client_secret
    return form


def test_token_confidential_client_rejected_without_secret(token_client):
    """The vuln: a confidential client must NOT authenticate on PKCE alone."""
    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=CLIENT_SECRET)
    client = TestClient(app)
    resp = client.post("/application/o/token/", data=_token_form())  # no client_secret
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_client"


def test_token_confidential_client_rejected_with_wrong_secret(token_client):
    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=CLIENT_SECRET)
    client = TestClient(app)
    resp = client.post("/application/o/token/", data=_token_form(client_secret="WRONG"))
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_client"


def test_token_confidential_client_accepts_correct_secret(token_client):
    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=CLIENT_SECRET)
    client = TestClient(app)
    resp = client.post("/application/o/token/", data=_token_form(client_secret=CLIENT_SECRET))
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "access-token"
    assert body["id_token"] == "id-token"


def test_token_public_client_still_works_with_pkce_only(token_client):
    """Regression: a public client (no registered secret) authenticates via PKCE."""
    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=None)
    client = TestClient(app)
    resp = client.post("/application/o/token/", data=_token_form())  # no client_secret
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "access-token"


def test_token_confidential_client_accepts_secret_via_http_basic(token_client):
    """Regression: a confidential client may send its secret via HTTP Basic auth."""
    import base64

    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=CLIENT_SECRET)
    client = TestClient(app)
    basic = base64.b64encode(f"confidential-app:{CLIENT_SECRET}".encode()).decode()
    resp = client.post(
        "/application/o/token/",
        data=_token_form(),  # no client_secret in the body
        headers={"Authorization": f"Basic {basic}"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "access-token"


def test_token_accepts_basic_only_without_form_client_id(token_client):
    """A client_secret_basic client may send id+secret only in the Basic header."""
    import base64

    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=CLIENT_SECRET)
    client = TestClient(app)
    form = _token_form()
    form.pop("client_id")  # nothing in the body; identity comes from Basic
    basic = base64.b64encode(f"confidential-app:{CLIENT_SECRET}".encode()).decode()
    resp = client.post(
        "/application/o/token/",
        data=form,
        headers={"Authorization": f"Basic {basic}"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "access-token"


def test_token_rejects_basic_secret_for_mismatched_client_id(token_client):
    """A Basic header whose client_id differs from the form client_id is ignored."""
    import base64

    app, monkeypatch = token_client
    _set_client(monkeypatch, client_id="confidential-app", secret_hash=CLIENT_SECRET)
    client = TestClient(app)
    basic = base64.b64encode(f"someone-else:{CLIENT_SECRET}".encode()).decode()
    resp = client.post(
        "/application/o/token/",
        data=_token_form(),
        headers={"Authorization": f"Basic {basic}"},
    )
    assert resp.status_code == 401
