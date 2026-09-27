"""First-boot panel pairing: who may confirm, and who may collect the token.

Auth audit 2026-09-27 found three holes in routers/panel_provision.py:

  * ``/confirm`` depended on ``get_current_user``, which resolves an anonymous
    caller to GUEST instead of refusing it — so any LAN device holding the
    6-char code could pair a panel and mint a kiosk device token;
  * the unauthenticated status poll handed that raw token to whoever polled
    the code first;
  * codes came from ``random.choices``.

Falsifiable pins (each negative control below goes red if its fix is removed):
  * swap ``_require_member_session`` back to ``get_current_user`` → the
    anonymous/guest/device-token confirm tests go red;
  * drop the ``X-Provision-Secret`` check → the second-poller test gets the token;
  * drop the pickup grace check → the stale-token test gets the token.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import inspect
import sqlite3
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import routers.panel_provision as pp
from database import get_db

_SCHEMA = [
    """CREATE TABLE panel_provision_codes (
        code TEXT PRIMARY KEY, device_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', panel_id TEXT, token TEXT,
        created_at TEXT, expires_at TEXT NOT NULL, confirmed_by TEXT,
        poll_secret_hash TEXT)""",
    """CREATE TABLE panels (
        panel_id TEXT PRIMARY KEY, name TEXT, location TEXT, panel_type TEXT,
        allow_guest INTEGER, ssh_user TEXT)""",
    """CREATE TABLE device_tokens (
        id TEXT PRIMARY KEY, panel_id TEXT, token_hash TEXT, role TEXT,
        scopes TEXT, revoked INTEGER DEFAULT 0)""",
    """CREATE TABLE panel_user_bindings (
        id TEXT PRIMARY KEY, panel_id TEXT, user_id TEXT, binding_type TEXT)""",
]

_SESSIONS = {
    "sess-member": {"user_id": "jason", "role": "member", "username": "jason"},
    "sess-admin": {"user_id": "family-admin", "role": "admin", "username": "admin"},
    "sess-guest": {"user_id": "guest", "role": "guest", "username": "guest"},
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "zoe.db")
    con = sqlite3.connect(path)
    for stmt in _SCHEMA:
        con.execute(stmt)
    con.commit()
    con.close()

    async def _fake_validate(session_id: str):
        return dict(_SESSIONS[session_id]) if session_id in _SESSIONS else None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _fake_validate)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()
    pp._rate_limit.clear()
    return path


@pytest.fixture
def client(db_path):
    app = FastAPI()
    app.include_router(pp.router)

    async def _db():
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _start(client, device_id="aa:bb:cc:dd:ee:ff"):
    r = client.post("/api/panels/provision/request", json={"device_id": device_id})
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client, code, headers=None, panel_id="kitchen-panel"):
    return client.post(
        f"/api/panels/provision/{code}/confirm",
        json={"name": "Kitchen", "panel_id": panel_id},
        headers=headers or {},
    )


def _row(db_path, code):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT * FROM panel_provision_codes WHERE code = ?", (code,)).fetchone())
    finally:
        con.close()


def _count(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


# ── /confirm: a signed-in household member only ──────────────────────────────

def test_anonymous_confirm_is_refused_and_mints_nothing(client, db_path):
    code = _start(client)["code"]
    r = _confirm(client, code)
    assert r.status_code == 401
    assert _row(db_path, code)["status"] == "pending"
    assert _count(db_path, "device_tokens") == 0
    assert _count(db_path, "panels") == 0


def test_guest_session_confirm_is_refused(client, db_path):
    code = _start(client)["code"]
    r = _confirm(client, code, headers={"X-Session-ID": "sess-guest"})
    assert r.status_code == 403
    assert _count(db_path, "device_tokens") == 0


def test_device_token_alone_cannot_confirm(client, db_path, monkeypatch):
    """A paired kiosk's device token resolves to its bound member, but a device
    is not a person — without a session header the confirm is refused."""
    import routers.panel_auth as panel_auth

    async def _bound(_raw):
        return {"user_id": "jason", "role": "member", "panel_id": "hall", "device_token_role": "kiosk"}

    monkeypatch.setattr(panel_auth, "_resolve_device_token_user", _bound)
    code = _start(client)["code"]
    r = _confirm(client, code, headers={"X-Device-Token": "some-kiosk-token"})
    assert r.status_code == 401
    assert _count(db_path, "device_tokens") == 0


def test_unauthenticated_admin_override_does_not_open_confirm(client, db_path, monkeypatch):
    """ZOE_UNAUTHENTICATED_ROLE=family-admin promotes a header-less caller to
    admin everywhere else; pairing still demands a real session."""
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    code = _start(client)["code"]
    assert _confirm(client, code).status_code == 401


def test_invalid_session_is_refused(client):
    code = _start(client)["code"]
    assert _confirm(client, code, headers={"X-Session-ID": "forged"}).status_code == 401


@pytest.mark.parametrize("sid,uid", [("sess-member", "jason"), ("sess-admin", "family-admin")])
def test_member_or_admin_session_confirms_and_binds_that_user(client, db_path, sid, uid):
    code = _start(client)["code"]
    r = _confirm(client, code, headers={"X-Session-ID": sid})
    assert r.status_code == 200, r.text
    row = _row(db_path, code)
    assert row["status"] == "confirmed" and row["confirmed_by"] == uid
    con = sqlite3.connect(db_path)
    try:
        bound = con.execute("SELECT user_id FROM panel_user_bindings").fetchone()[0]
    finally:
        con.close()
    assert bound == uid  # never the guest, never a hardcoded admin fallback


def test_confirm_on_expired_attempt_is_refused(client, db_path):
    code = _start(client)["code"]
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(db_path)
    con.execute("UPDATE panel_provision_codes SET expires_at = ? WHERE code = ?", (past, code))
    con.commit()
    con.close()
    r = _confirm(client, code, headers={"X-Session-ID": "sess-member"})
    assert r.status_code == 410
    assert _count(db_path, "device_tokens") == 0


# ── status poll: the token goes to the pairing device only ───────────────────

def _poll(client, code, secret=None):
    headers = {"X-Provision-Secret": secret} if secret is not None else {}
    return client.get(f"/api/panels/provision/{code}", headers=headers)


def test_poll_secret_is_issued_once_and_only_its_hash_is_stored(client, db_path):
    started = _start(client)
    assert len(started["poll_secret"]) >= 32
    stored = _row(db_path, started["code"])["poll_secret_hash"]
    assert stored and started["poll_secret"] not in stored
    assert stored == pp._hash_secret(started["poll_secret"])


def test_token_is_not_released_to_a_second_poller(client, db_path):
    started = _start(client)
    code, secret = started["code"], started["poll_secret"]
    assert _confirm(client, code, headers={"X-Session-ID": "sess-member"}).status_code == 200

    # Someone else on the LAN read the code off the screen and polls it first.
    assert _poll(client, code).status_code == 403
    assert _poll(client, code, secret="guessed").status_code == 403
    assert _row(db_path, code)["token"], "a refused poll must not consume the token"

    # The pairing device, with its own secret, still collects it — exactly once.
    first = _poll(client, code, secret=secret).json()
    assert first["status"] == "confirmed" and first["token"]
    again = _poll(client, code, secret=secret).json()
    assert again == {"status": "confirmed"}


def test_secret_from_another_attempt_does_not_unlock_this_one(client):
    a = _start(client, device_id="aa:aa:aa:aa:aa:aa")
    b = _start(client, device_id="bb:bb:bb:bb:bb:bb")
    assert _confirm(client, a["code"], headers={"X-Session-ID": "sess-member"}).status_code == 200
    assert _poll(client, a["code"], secret=b["poll_secret"]).status_code == 403


def test_expired_pending_attempt_is_refused(client, db_path):
    started = _start(client)
    code = started["code"]
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(db_path)
    con.execute("UPDATE panel_provision_codes SET expires_at = ? WHERE code = ?", (past, code))
    con.commit()
    con.close()
    assert _poll(client, code, secret=started["poll_secret"]).json() == {"status": "expired"}
    assert _confirm(client, code, headers={"X-Session-ID": "sess-member"}).status_code == 409


def test_uncollected_token_expires_and_its_device_token_is_revoked(client, db_path):
    started = _start(client)
    code = started["code"]
    assert _confirm(client, code, headers={"X-Session-ID": "sess-member"}).status_code == 200
    stale = (datetime.now(tz=timezone.utc) - timedelta(seconds=pp._PICKUP_GRACE_S + 5)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(db_path)
    con.execute("UPDATE panel_provision_codes SET expires_at = ? WHERE code = ?", (stale, code))
    con.commit()
    con.close()

    assert _poll(client, code, secret=started["poll_secret"]).json() == {"status": "expired"}
    assert _row(db_path, code)["token"] is None
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT revoked FROM device_tokens").fetchone()[0] == 1
    finally:
        con.close()


def test_legacy_row_without_a_secret_hash_never_releases_its_token(client, db_path):
    con = sqlite3.connect(db_path)
    future = (datetime.now(tz=timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO panel_provision_codes (code, device_id, status, token, panel_id, expires_at)"
        " VALUES ('OLD234', 'x', 'confirmed', 'raw', 'p', ?)", (future,))
    con.commit()
    con.close()
    assert _poll(client, "OLD234").status_code == 403
    assert _poll(client, "OLD234", secret="").status_code == 403


# ── code generation ──────────────────────────────────────────────────────────

def test_codes_come_from_secrets_not_random():
    src = inspect.getsource(pp._generate_code)
    assert "secrets." in src and "random." not in src
    code = pp._generate_code()
    assert len(code) == 6 and set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


# ── server-side expiry: no poll needed (Greptile, #1741) ─────────────────────

def _age(db_path, code, seconds_past_expiry):
    ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=seconds_past_expiry)
          ).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(db_path)
    con.execute("UPDATE panel_provision_codes SET expires_at = ? WHERE code = ?", (ts, code))
    con.commit()
    con.close()


def _revoked(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT revoked FROM device_tokens").fetchone()[0]
    finally:
        con.close()


async def _sweep(db_path):
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        return await pp.sweep_uncollected_tokens(conn)
    finally:
        await conn.close()


async def test_sweep_revokes_an_abandoned_pairing_without_any_poll(client, db_path):
    code = _start(client)["code"]
    assert _confirm(client, code, headers={"X-Session-ID": "sess-member"}).status_code == 200
    assert await _sweep(db_path) == 0          # still inside the pickup window
    assert _revoked(db_path) == 0 and _row(db_path, code)["token"]

    _age(db_path, code, pp._PICKUP_GRACE_S + 5)  # clock past grace; the Pi never polls again
    assert await _sweep(db_path) == 1
    assert _row(db_path, code)["token"] is None
    assert _revoked(db_path) == 1


async def test_sweep_expires_stale_pending_codes(client, db_path):
    code = _start(client)["code"]
    _age(db_path, code, 5)
    await _sweep(db_path)
    assert _row(db_path, code)["status"] == "expired"


def test_any_later_request_revokes_a_stale_token(client, db_path):
    code = _start(client)["code"]
    assert _confirm(client, code, headers={"X-Session-ID": "sess-member"}).status_code == 200
    _age(db_path, code, pp._PICKUP_GRACE_S + 5)
    assert client.get(f"/api/panels/provision/{code}/public").status_code == 200
    assert _row(db_path, code)["token"] is None and _revoked(db_path) == 1


def test_sweep_is_scheduled_at_startup():
    from pathlib import Path
    main_src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "_panel_provision.sweep_uncollected_tokens" in main_src
    assert 'id="panel_provision_token_sweep"' in main_src
