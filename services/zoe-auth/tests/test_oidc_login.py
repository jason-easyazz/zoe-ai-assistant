"""Tests for the server-rendered OIDC login flow (/application/o/login).

The flow bridges password auth into a zoe_session cookie so the OIDC consent
flow completes without the SPA. These tests exercise the GET page (expired vs
valid state, error allow-list), the POST submit (bad creds, expired state,
success -> cookie + redirect), and the login page's HTML escaping.
"""
import contextlib
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oidc import router as oidc_router


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(oidc_router.router)
    return TestClient(app)


def _fake_db(row):
    class _Conn:
        def execute(self, *a, **k):
            return self

        def fetchone(self):
            return row

    @contextlib.contextmanager
    def _get_db():
        yield _Conn()

    return _get_db


# ── _login_page_html (unit) ──────────────────────────────────────────────────


def test_login_page_html_escapes_state_and_error():
    out = oidc_router._login_page_html('"><script>x</script>', '<b>bad</b>')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>bad</b>" not in out
    assert "&lt;b&gt;bad&lt;/b&gt;" in out


# ── GET /application/o/login ─────────────────────────────────────────────────


def test_login_page_expired_state_returns_400(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: None)
    resp = client.get("/application/o/login", params={"oidc_state_id": "missing"})
    assert resp.status_code == 400
    assert "expired" in resp.text.lower()


def test_login_page_valid_state_renders_form(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: {"client_id": "omnigent"})
    resp = client.get("/application/o/login", params={"oidc_state_id": "state-1"})
    assert resp.status_code == 200
    assert 'name="oidc_state_id" value="state-1"' in resp.text
    assert 'name="password"' in resp.text


def test_login_page_error_allowlist(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: {"client_id": "omnigent"})
    resp = client.get(
        "/application/o/login", params={"oidc_state_id": "s", "error": "invalid"}
    )
    assert "Invalid username or password." in resp.text
    # Unknown error codes are not reflected.
    resp2 = client.get(
        "/application/o/login", params={"oidc_state_id": "s", "error": "<x>"}
    )
    assert "<x>" not in resp2.text


# ── POST /application/o/login ────────────────────────────────────────────────


def test_login_submit_expired_state_redirects(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: None)
    resp = client.post(
        "/application/o/login",
        data={"username": "jason", "password": "pw", "oidc_state_id": "gone"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/?error=oidc_expired" in resp.headers["location"]


def test_login_submit_bad_credentials_redirects_with_error(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: {"client_id": "omnigent"})
    monkeypatch.setattr(oidc_router, "get_db", _fake_db(("jason", "hash")))
    monkeypatch.setattr(
        oidc_router.auth_manager,
        "verify_password",
        lambda uid, pw, ip=None: types.SimpleNamespace(success=False, user_id=uid),
    )
    resp = client.post(
        "/application/o/login",
        data={"username": "jason", "password": "wrong", "oidc_state_id": "s1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=invalid" in resp.headers["location"]
    assert "oidc_state_id=s1" in resp.headers["location"]


def test_login_submit_unknown_user_redirects_with_error(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: {"client_id": "omnigent"})
    monkeypatch.setattr(oidc_router, "get_db", _fake_db(None))  # no such user
    resp = client.post(
        "/application/o/login",
        data={"username": "ghost", "password": "pw", "oidc_state_id": "s1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=invalid" in resp.headers["location"]


def test_login_submit_setup_required_hash_rejected(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: {"client_id": "omnigent"})
    monkeypatch.setattr(oidc_router, "get_db", _fake_db(("jason", "SETUP_REQUIRED")))
    resp = client.post(
        "/application/o/login",
        data={"username": "jason", "password": "pw", "oidc_state_id": "s1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=invalid" in resp.headers["location"]


def test_login_submit_success_sets_cookie_and_resumes(client, monkeypatch):
    monkeypatch.setattr(oidc_router, "get_pending_auth", lambda sid: {"client_id": "omnigent"})
    monkeypatch.setattr(oidc_router, "get_db", _fake_db(("jason", "hash")))
    monkeypatch.setattr(
        oidc_router.auth_manager,
        "verify_password",
        lambda uid, pw, ip=None: types.SimpleNamespace(success=True, user_id="jason"),
    )
    session = types.SimpleNamespace(session_id="sess-123")
    monkeypatch.setattr(
        oidc_router.session_manager,
        "authenticate",
        lambda req: types.SimpleNamespace(success=True, session=session),
    )
    resp = client.post(
        "/application/o/login",
        data={"username": "jason", "password": "right", "oidc_state_id": "s1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/application/o/authorize/complete?oidc_state_id=s1" in resp.headers["location"]
    set_cookie = resp.headers.get("set-cookie", "")
    assert "zoe_session=sess-123" in set_cookie
    assert "httponly" in set_cookie.lower()
