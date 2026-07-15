"""GET /api/auth/user must return the guest profile, not 404.

Regression: guest is a synthetic identity — guest_login mints a session with
user_id="guest" but there is no auth_users row, so auth_manager.get_user_info
returns None and /api/auth/user (and /profile) 404'd. zoe-data treats that as
an invalid session → 401 on every guest call → the kiosk loops on the sign-in
card. The endpoint now returns the guest profile for a guest session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

import api.auth as auth_api
import api.dependencies as deps
from models.database import AuthSession, SessionType, AuthMethod
from main import app


def _guest_session(session_id="guest-sid"):
    now = datetime.now(timezone.utc)
    return AuthSession(
        session_id=session_id,
        user_id="guest",
        session_type=SessionType.GUEST,
        auth_method=AuthMethod.API_KEY,
        device_info={},
        created_at=now,
        last_activity=now,
        expires_at=now + timedelta(minutes=30),
        is_active=True,
    )


@pytest.fixture
def guest_session(monkeypatch):
    sess = _guest_session()
    # get_current_session resolves via session_manager.get_session
    monkeypatch.setattr(deps.session_manager, "get_session",
                        lambda sid: sess if sid == sess.session_id else None)
    # synthetic guest → no auth_users row
    monkeypatch.setattr(auth_api.auth_manager, "get_user_info", lambda uid: None)
    # control the module-level rbac singleton (else the guest fast-path hits a
    # live DB round-trip + mutates its permission_cache across tests)
    monkeypatch.setattr(auth_api.rbac_manager, "list_user_permissions", lambda uid: [])
    return sess


def test_user_endpoint_returns_guest_profile(guest_session):
    client = TestClient(app)
    r = client.get("/api/auth/user", headers={"X-Session-ID": guest_session.session_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "guest"
    assert body["role"] == "guest"
    assert body["username"] == "Guest"


def test_profile_endpoint_returns_guest_profile(guest_session):
    client = TestClient(app)
    r = client.get("/api/auth/profile", headers={"X-Session-ID": guest_session.session_id})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "guest"


def test_non_guest_missing_user_still_404s(monkeypatch):
    # A real (non-guest) session whose user vanished must still 404, not be
    # silently treated as a guest.
    now = datetime.now(timezone.utc)
    sess = AuthSession(
        session_id="ghost-sid", user_id="ghost", session_type=SessionType.STANDARD,
        auth_method=AuthMethod.PASSWORD, device_info={}, created_at=now,
        last_activity=now, expires_at=now + timedelta(minutes=30), is_active=True,
    )
    monkeypatch.setattr(deps.session_manager, "get_session",
                        lambda sid: sess if sid == sess.session_id else None)
    monkeypatch.setattr(auth_api.auth_manager, "get_user_info", lambda uid: None)
    client = TestClient(app)
    r = client.get("/api/auth/user", headers={"X-Session-ID": sess.session_id})
    assert r.status_code == 404
