"""GET /api/voice/livekit-token — the join grant for the house voice room is
never anonymous.

The gap this pins (found 2026-08-04 while rotating the LiveKit key pair): the
endpoint depended on `get_current_user`, which RESOLVES an identity but never
ENFORCES one — a credential-less request came back as guest and was handed a
valid `roomJoin` token for `zoe-voice`. Any LAN device could join the house
voice channel, publish a mic track and subscribe to Zoe's replies. Rotating the
key pair does not touch that: it is authz, not a leaked credential.

Falsifiable pins — break the thing, these go red:
  * swap the gate back to `Depends(get_current_user)` and
    `test_anonymous_caller_is_refused` returns 200 with a token;
  * drop the device-token branch and `test_device_token_is_accepted` 401s;
  * drop the validated-session branch and the PANEL breaks
    (`test_validated_guest_session_is_accepted`) — that branch is the
    acceptance constraint, not an oversight;
  * take the identity from a request argument and
    `test_identity_comes_from_the_principal_not_the_request` goes red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import inspect

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.voice_tts as voice_tts
from auth import get_current_user

_SECRET = "test-livekit-secret-0123456789abcdef"  # ≥32 bytes: HS256 min key length
_KEY = "APItestkey"


@pytest.fixture(autouse=True)
def _livekit_env(monkeypatch):
    """Credentials present (else the endpoint 503s before the gate is reached),
    and on-demand OFF so no test ever shells out to docker."""
    monkeypatch.setenv("LIVEKIT_API_KEY", _KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", _SECRET)
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "false")


def _app(*, device=None, user=None) -> FastAPI:
    """Router under test with the two upstream resolvers stubbed.

    `_validate_device_token` and `get_current_user` are the inputs to the gate;
    everything the gate then decides is the code under test.
    """
    app = FastAPI()
    app.include_router(voice_tts.router)
    app.dependency_overrides[voice_tts._validate_device_token] = lambda: device
    app.dependency_overrides[get_current_user] = lambda: (
        user if user is not None else {"user_id": "guest", "role": "guest"}
    )
    return app


def _claims(body: dict) -> dict:
    return jwt.decode(body["token"], _SECRET, algorithms=["HS256"])


# ── the gap itself ──────────────────────────────────────────────────────────

def test_anonymous_caller_is_refused():
    """No session header, no device token — the exact `curl` that used to get a
    working room-join token."""
    resp = TestClient(_app()).get("/api/voice/livekit-token")
    assert resp.status_code == 401
    assert "token" not in resp.json()


def test_invalid_device_token_alone_is_refused():
    """A bogus X-Device-Token resolves to None, and header PRESENCE must not be
    mistaken for a credential — only a header the server validated counts."""
    app = _app(device=None)
    resp = TestClient(app).get(
        "/api/voice/livekit-token", headers={"X-Device-Token": "not-a-real-token"}
    )
    assert resp.status_code == 401


# ── who IS allowed ──────────────────────────────────────────────────────────

def test_device_token_is_accepted():
    """The Pi voice daemon / provisioned kiosk lane."""
    app = _app(device={"panel_id": "zoe-touch-pi", "user_id": "jason", "role": "kiosk"})
    resp = TestClient(app).get(
        "/api/voice/livekit-token", headers={"X-Device-Token": "raw"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert _claims(body)["sub"] == "jason"


def test_signed_in_user_is_accepted():
    app = _app(user={"user_id": "jason", "role": "admin"})
    resp = TestClient(app).get(
        "/api/voice/livekit-token", headers={"X-Session-ID": "sess-real"}
    )
    assert resp.status_code == 200
    assert _claims(resp.json())["sub"] == "jason"


def test_validated_guest_session_is_accepted():
    """THE PANEL. The estate kiosk boots as a guest (`/api/auth/guest`) and has
    no device token, so its only credential is a validated guest session — an
    expired or unknown session id never reaches this gate, `get_current_user`
    401s it first. Tightening this to reject guests breaks the Talk button."""
    app = _app(user={"user_id": "guest", "role": "guest"})
    resp = TestClient(app).get(
        "/api/voice/livekit-token", headers={"X-Session-ID": "kiosk-guest-sid"}
    )
    assert resp.status_code == 200
    assert resp.json()["token"]


def test_blank_session_header_is_not_a_credential():
    """An empty X-Session-ID is what a kiosk with no stored session sends — it
    must not satisfy the gate by mere header presence."""
    app = _app()
    resp = TestClient(app).get(
        "/api/voice/livekit-token", headers={"X-Session-ID": "   "}
    )
    assert resp.status_code == 401


# ── identity binding ────────────────────────────────────────────────────────

def test_identity_comes_from_the_principal_not_the_request():
    """The participant identity and the room are SERVER-chosen. A caller must
    not be able to steer either from query args — that would let a guest mint a
    token that joins as somebody else, or joins a room it was never granted."""
    app = _app(user={"user_id": "jason", "role": "admin"})
    resp = TestClient(app).get(
        "/api/voice/livekit-token",
        params={"user_id": "family-admin", "identity": "zoe-agent", "room": "other"},
        headers={"X-Session-ID": "sess-real"},
    )
    assert resp.status_code == 200
    claims = _claims(resp.json())
    assert claims["sub"] == "jason", "identity must track the authenticated principal"
    assert claims["video"]["room"] == "zoe-voice", "room is fixed server-side"


def test_device_token_identity_is_the_bound_user():
    """A device token authenticates the DEVICE; the identity is the user the
    panel is bound to, resolved server-side — not anything the caller sent."""
    app = _app(device={"panel_id": "p1", "user_id": "voice-daemon", "role": "kiosk"})
    resp = TestClient(app).get(
        "/api/voice/livekit-token",
        params={"user_id": "jason"},
        headers={"X-Device-Token": "raw"},
    )
    assert _claims(resp.json())["sub"] == "voice-daemon"


# ── wiring pin ──────────────────────────────────────────────────────────────

def test_endpoint_is_wired_through_require_livekit_auth():
    """Signature pin: a future edit that swaps the gate back to a bare
    `Depends(get_current_user)` reopens the hole silently — the response shape
    does not change at all. Pin the dependency itself."""
    sig = inspect.signature(voice_tts.get_livekit_token)
    dep = sig.parameters["caller"].default
    assert getattr(dep, "dependency", None) is voice_tts._require_livekit_auth
