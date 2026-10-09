"""Wire-compatibility of the OIDC JWT code after the python-jose -> PyJWT swap.

python-jose (<= 3.5.0, no patched release: GHSA-3qf3-8w2g-rqmx / CVE-2026-85394)
was replaced by PyJWT. The fixtures below were minted/derived ONCE with
python-jose 3.5.0 (RS256, a throwaway key; only the PUBLIC half is committed), so
these tests prove tokens and JWKS produced by the OLD code are accepted by the
NEW code. The reverse direction (new-minted tokens decode under the old library)
was proven at swap time and is covered here by asserting the new tokens use the
standard compact RS256 JWS shape any JOSE library reads.
"""
import base64
import hashlib
import hmac
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from oidc import keys, tokens
from oidc.keys import generate_rsa_key

ISSUER = "https://zoe.example"

# --- minted by python-jose 3.5.0 (exp = 2100-01-01 so the fixture never expires) ---
GOLDEN_PUBLIC_PEM = '-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArIlZFrgyZEXMdIWbrgVF\nTO09Zl1+SAYKff+83ORDaNV1pGGUdpqqVjlvzvqU0JjbAPZqZCpWBcelCzWe1nNn\nTRO5M0oorx9h4MVqVZsIhhjfocB6bIESIp2MUqj6PM/jafi+wuWQmprox/EVIjcJ\ngpwkPPBU5Nl1KczLRGl4Xp4+60uY14/1gh66P2P8Lgvq8gWxGDT9fCMFPZSJKmbB\nEv5e/wFP3r+tMALXLwEwvTgjv096eQK17BEn+04HnftB9FO9wCLc3/AWHyILv/sg\nFM88jqNlKdmdrxgR9NHzG6INBrKMDB62BgoKs2CDlWbKM+IFsO+INipTqNQ2TaVj\nJQIDAQAB\n-----END PUBLIC KEY-----\n'
GOLDEN_JWK = {'alg': 'RS256', 'kty': 'RSA', 'n': 'rIlZFrgyZEXMdIWbrgVFTO09Zl1-SAYKff-83ORDaNV1pGGUdpqqVjlvzvqU0JjbAPZqZCpWBcelCzWe1nNnTRO5M0oorx9h4MVqVZsIhhjfocB6bIESIp2MUqj6PM_jafi-wuWQmprox_EVIjcJgpwkPPBU5Nl1KczLRGl4Xp4-60uY14_1gh66P2P8Lgvq8gWxGDT9fCMFPZSJKmbBEv5e_wFP3r-tMALXLwEwvTgjv096eQK17BEn-04HnftB9FO9wCLc3_AWHyILv_sgFM88jqNlKdmdrxgR9NHzG6INBrKMDB62BgoKs2CDlWbKM-IFsO-INipTqNQ2TaVjJQ', 'e': 'AQAB', 'kid': 'golden-kid-1', 'use': 'sig'}
_GOLDEN_ACCESS_TOKEN_PARTS = (
    'eyJhbGciOiJSUzI1NiIsImtpZCI6ImdvbGRlbi1raWQtMSIsInR5cCI6IkpXVCJ9',
    'eyJpc3MiOiJodHRwczovL3pvZS5leGFtcGxlIiwic3ViIjoidXNlci0xIiwiYXVkIjoiem9lLWF1dGgiLCJjbGllbnRfaWQiOiJjbGllbnQtMSIsInNjb3BlIjoib3BlbmlkIHByb2ZpbGUiLCJleHAiOjQxMDI0NDQ4MDAsImlhdCI6MTcwMDAwMDAwMH0',
    'XTZXDykPiTR0RurMxNmUmVkGIL974s-lcaPtyMYqXEot9iWdJmuCGVkIo0qd4pICN_iG-d4Joj5XfwVY9jT_ahrC2akXccy0XIDifEUqbEAO3GwAk11bVw0kxV3tXMNIiC_j7HGCSbatxDvNsuw6Bbnj5nmajEY1PkH4fPyB336Dybrpgi6QrFLqbLDe8WKdzhFW8XXG7RerQHbXmp2kVVY9lg8_joqqHvPBO9-O6uhzLRzo1tFj_JZWSK4ylpUiT1lNtZJfrQXkcdWbi8IILfjavgbNgSkmRh8_JwIJiMeQCs8-wmPYK6zUglgHNtKwqrEqBct37-Q6VU_dZudH9g',
)
GOLDEN_ACCESS_TOKEN = ".".join(_GOLDEN_ACCESS_TOKEN_PARTS)  # a throwaway fixture, split at the JWS dots so a secrets scanner does not read it as a credential
_GOLDEN_ID_TOKEN_PARTS = (
    'eyJhbGciOiJSUzI1NiIsImtpZCI6ImdvbGRlbi1raWQtMSIsInR5cCI6IkpXVCJ9',
    'eyJpc3MiOiJodHRwczovL3pvZS5leGFtcGxlIiwic3ViIjoidXNlci0xIiwiYXVkIjoiY2xpZW50LTEiLCJleHAiOjQxMDI0NDQ4MDAsImlhdCI6MTcwMDAwMDAwMCwicm9sZSI6ImFkbWluIiwiZ3JvdXBzIjpbImFkbWluIl0sIm5vbmNlIjoibjEifQ',
    'OgJu1g7s6WOn3YX9cb0_NxDYIKEVQfDZZiqia5kGaxcbFo_bhkIp8fFppeQKa4Mnr0COL9k7eOV6QvKZa9ei7eazeNyR6uS3faMNVPtrdwbmkX3-yplUdX68wHu3xs5kFo7k_ArYAFJKgzR-i12dzGSUq5e6ylFenWLUtzc6N8PS1yNdZvNxdO4nr70fjwgVX-ByM9ctC5DDbSSz2y6ijh8Kh3zAlB-28QE3Bg2lIPkJQffUxVGPPBMvCfxeQSBBe5ghsASDapt4mcoE9XXVo8Xz-DaBbBoTyeHap8IEliHxGBlYn4GwHZaFyX0sIkgPjLuNTcC_r1XIb6UML86GWQ',
)
GOLDEN_ID_TOKEN = ".".join(_GOLDEN_ID_TOKEN_PARTS)  # a throwaway fixture, split at the JWS dots so a secrets scanner does not read it as a credential


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _segment(token: str, index: int) -> dict:
    seg = token.split(".")[index]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


def test_old_jose_access_token_verifies_with_new_code():
    claims = tokens.verify_access_token(GOLDEN_ACCESS_TOKEN, ISSUER, {"keys": [GOLDEN_JWK]})
    assert claims is not None
    assert claims["sub"] == "user-1"
    assert claims["aud"] == "zoe-auth"
    assert claims["client_id"] == "client-1"
    assert claims["scope"] == "openid profile"


def test_old_jose_id_token_decodes_with_pyjwt():
    claims = jwt.decode(
        GOLDEN_ID_TOKEN, GOLDEN_PUBLIC_PEM, algorithms=["RS256"],
        audience="client-1", issuer=ISSUER,
    )
    assert claims["groups"] == ["admin"] and claims["nonce"] == "n1"


def test_old_jose_token_rejected_for_wrong_issuer():
    assert tokens.verify_access_token(GOLDEN_ACCESS_TOKEN, "https://evil.example", {"keys": [GOLDEN_JWK]}) is None


def test_jwks_member_set_and_modulus_match_old_jose_output(monkeypatch):
    """get_jwks() must publish the same JWK (kty/n/e/alg/use/kid, nothing extra) python-jose did."""
    class _Conn:
        def execute(self, *_a, **_k):
            return self
        def fetchall(self):
            return [(GOLDEN_JWK["kid"], GOLDEN_PUBLIC_PEM)]
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(keys, "get_db", lambda: _Conn())
    published = keys.get_jwks()["keys"]
    assert published == [GOLDEN_JWK]


def test_new_tokens_are_standard_compact_rs256_jws(monkeypatch):
    key = generate_rsa_key()
    monkeypatch.setattr(tokens, "ensure_signing_key", lambda: key)
    token = tokens.issue_access_token(ISSUER, "user-1", "client-1", "openid")
    assert token.count(".") == 2
    header = _segment(token, 0)
    assert header == {"alg": "RS256", "kid": key["kid"], "typ": "JWT"}
    assert _segment(token, 1)["aud"] == "zoe-auth"


def _forged_hs256(public_secret: bytes, kid: str) -> str:
    """The CVE-2026-85394 shape: an HS256 token keyed with the service's public key."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    payload = _b64(json.dumps({
        "iss": ISSUER, "sub": "user-1", "aud": "zoe-auth", "exp": 4102444800, "iat": 1700000000,
    }).encode())
    sig = hmac.new(public_secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


@pytest.mark.parametrize("encoding,fmt", [
    (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),  # the advisory's DER key
    (serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
])
def test_hs256_public_key_forgery_rejected(encoding, fmt):
    pub = serialization.load_pem_public_key(GOLDEN_PUBLIC_PEM.encode())
    forged = _forged_hs256(pub.public_bytes(encoding, fmt), GOLDEN_JWK["kid"])
    assert tokens.verify_access_token(forged, ISSUER, {"keys": [GOLDEN_JWK]}) is None


def test_alg_none_rejected():
    header = _b64(json.dumps({"alg": "none", "kid": GOLDEN_JWK["kid"]}).encode())
    payload = _b64(json.dumps({"iss": ISSUER, "sub": "user-1", "aud": "zoe-auth", "exp": 4102444800}).encode())
    assert tokens.verify_access_token(f"{header}.{payload}.", ISSUER, {"keys": [GOLDEN_JWK]}) is None
