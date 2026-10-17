"""Device-token hygiene (security follow-up 2026-10-18, ledger: "Device token hygiene").

A panel device token used to be minted with no expiry, with any role string (``admin`` included), and with a ``scopes`` field
written in two formats and enforced nowhere. Now (``routers/panel_auth.py``, lifecycle block):

  * ``issue_token`` refuses an admin role and a ``scopes`` field, and mints with an expiry (``ZOE_DEVICE_TOKEN_TTL_DAYS``,
    default 365; ``0`` = none); existing NULL-expiry tokens never expire, so no live panel is forced off;
  * ``POST /api/panels/device-token/renew`` lets the holder push ITS OWN token's expiry forward in place;
  * an unreadable ``expires_at`` refuses the token instead of raising out of ``get_current_user``;
  * even the ``ZOE_DEVICE_TOKEN_BINDS_MEMBER`` rollback never resolves a token to an admin.

Real HTTP over the real routers, real SQL over SQLite; only zoe-auth's session validation is faked. Invented data.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import routers.panel_auth as panel_auth
from database import get_db

pytestmark = pytest.mark.ci_safe

PHONE_IP = "192.0.2.77"
PANEL_IP = "192.0.2.50"
PROXY = "127.0.0.1"
ADMIN_SID = "sid-admin"
MEMBER_SID = "sid-member"
LIVE_TOKEN = "tok-live-expiring"
LEGACY_TOKEN = "tok-legacy-no-expiry"


def _now():
    return datetime.now(tz=timezone.utc)


class _Cur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()


class Db:
    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE panels (panel_id TEXT PRIMARY KEY);
            INSERT INTO panels VALUES ('kitchen-panel');
            CREATE TABLE device_tokens (id TEXT PRIMARY KEY, panel_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'voice-daemon', scopes TEXT DEFAULT '["voice"]',
                expires_at TEXT, revoked INTEGER DEFAULT 0, revoked_at TEXT);
            """
        )

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.con.commit()

    def token(self, raw, expires_at, revoked=0):
        h = panel_auth._hash_token(raw)
        self.con.execute("INSERT INTO device_tokens (id, panel_id, token_hash, name, role, expires_at, revoked)"
                         " VALUES (?, 'kitchen-panel', ?, 'voice-daemon', 'voice-daemon', ?, ?)", (raw, h, expires_at, revoked))
        self.con.commit()
        panel_auth._token_cache[h] = {"panel_id": "kitchen-panel", "role": "voice-daemon", "expires_at": expires_at,
                                      "revoked": revoked}
        return h

    def expiry(self, raw):
        return self.con.execute("SELECT expires_at FROM device_tokens WHERE token_hash = ?",
                                (panel_auth._hash_token(raw),)).fetchone()[0]


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(panel_auth, "_token_cache", {})
    monkeypatch.delenv("ZOE_DEVICE_TOKEN_TTL_DAYS", raising=False)
    monkeypatch.delenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", raising=False)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(session_id):
        if session_id == ADMIN_SID:
            return {"user_id": "family-admin", "role": "admin", "username": "admin", "permissions": []}
        if session_id == MEMBER_SID:
            return {"user_id": "casey", "role": "user", "username": "casey", "permissions": []}
        return None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    d = Db()
    yield d
    auth._session_cache.clear()


def _client(db, peer=PHONE_IP):
    app = FastAPI()
    app.include_router(panel_auth.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, client=(peer, 50000))


def _issue(db, body, headers=None, peer=PHONE_IP):
    return _client(db, peer).post("/api/panels/kitchen-panel/token", json=body,
                                  headers={"X-Session-ID": ADMIN_SID} if headers is None else headers)


def _renew(db, headers, peer=PHONE_IP):
    return _client(db, peer).post("/api/panels/device-token/renew", headers=headers)


def _count(db):
    return db.con.execute("SELECT COUNT(*) FROM device_tokens").fetchone()[0]


# ---- issue_token ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["admin", "ADMIN", " admin ", "family-admin"])
def test_no_admin_token_can_be_issued(db, role):
    r = _issue(db, {"name": "probe", "role": role})
    assert r.status_code == 400, r.text
    assert _count(db) == 0


@pytest.mark.parametrize("scopes", [["voice"], ["chat", "voice"], "chat,voice"])
def test_scopes_are_refused_not_silently_promised(db, scopes):
    r = _issue(db, {"role": "voice-daemon", "scopes": scopes})
    assert r.status_code == 400 and "scopes" in r.json()["detail"], r.text
    assert _count(db) == 0


def test_a_new_token_expires_after_the_default_ttl(db):
    r = _issue(db, {"name": "voice-daemon", "role": "voice-daemon"})
    assert r.status_code == 200, r.text
    body = r.json()
    days = (panel_auth.parse_token_expiry(body["expires_at"]) - _now()).total_seconds() / 86400
    assert 364.9 < days <= 365
    assert db.con.execute("SELECT expires_at FROM device_tokens").fetchone()[0] == body["expires_at"]
    assert panel_auth.lookup_device_token(body["token"])["expires_at"] == body["expires_at"]


def test_ttl_is_operator_tunable_and_zero_means_no_expiry(db, monkeypatch):
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_TTL_DAYS", "30")
    days = (panel_auth.parse_token_expiry(_issue(db, {}).json()["expires_at"]) - _now()).total_seconds() / 86400
    assert 29.9 < days <= 30
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_TTL_DAYS", "0")
    assert _issue(db, {}).json()["expires_at"] is None
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_TTL_DAYS", "not-a-number")
    days = (panel_auth.parse_token_expiry(_issue(db, {}).json()["expires_at"]) - _now()).total_seconds() / 86400
    assert 364.9 < days <= 365  # a typo falls back to the default, never to "no expiry"


def test_an_explicit_expiry_must_be_a_future_timestamp(db):
    future = (_now() + timedelta(days=7)).isoformat()
    assert _issue(db, {"expires_at": future}).json()["expires_at"] == future
    assert _issue(db, {"expires_at": (_now() - timedelta(seconds=1)).isoformat()}).status_code == 400
    assert _issue(db, {"expires_at": "next tuesday"}).status_code == 400
    assert _count(db) == 1


@pytest.mark.parametrize("peer,headers", [
    (PHONE_IP, {}),                                     # anonymous LAN caller
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),               # spoofed address
    (PROXY, {"X-Real-IP": PANEL_IP}),                  # the panel by address is still not an admin
    (PHONE_IP, {"X-Session-ID": MEMBER_SID}),          # a member is not an admin
])
def test_only_an_admin_issues_tokens(db, peer, headers):
    assert _issue(db, {}, headers=headers, peer=peer).status_code == 403
    assert _count(db) == 0


def test_the_panels_own_token_cannot_mint_another(db):
    db.token(LIVE_TOKEN, (_now() + timedelta(days=10)).isoformat())
    assert _issue(db, {}, headers={"X-Device-Token": LIVE_TOKEN}).status_code == 403
    assert _count(db) == 1


# ---- lookup -----------------------------------------------------------------------------------------------------------------
def test_expiry_is_enforced_and_an_unreadable_one_refuses_instead_of_raising(db):
    db.token("tok-expired", (_now() - timedelta(seconds=1)).isoformat())
    db.token("tok-naive-future", (_now() + timedelta(days=1)).replace(tzinfo=None).isoformat())
    db.token("tok-zulu", (_now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    db.token("tok-garbage", "soon")
    db.token(LEGACY_TOKEN, None)
    assert panel_auth.lookup_device_token("tok-expired") is None
    assert panel_auth.lookup_device_token("tok-naive-future") is not None  # a naive stamp is UTC
    assert panel_auth.lookup_device_token("tok-zulu") is not None
    assert panel_auth.lookup_device_token("tok-garbage") is None
    assert panel_auth.lookup_device_token(LEGACY_TOKEN) is not None  # existing tokens: no forced expiry


# ---- renew ------------------------------------------------------------------------------------------------------------------
def test_the_panel_renews_its_own_token_in_place(db):
    db.token(LIVE_TOKEN, (_now() + timedelta(days=3)).isoformat())
    r = _renew(db, {"X-Device-Token": LIVE_TOKEN})
    assert r.status_code == 200 and r.json()["renewed"] is True, r.text
    days = (panel_auth.parse_token_expiry(db.expiry(LIVE_TOKEN)) - _now()).total_seconds() / 86400
    assert 364.9 < days <= 365
    assert panel_auth.lookup_device_token(LIVE_TOKEN)["expires_at"] == db.expiry(LIVE_TOKEN) == r.json()["expires_at"]


def test_renewal_never_adds_an_expiry_and_never_shortens_one(db):
    db.token(LEGACY_TOKEN, None)
    r = _renew(db, {"X-Device-Token": LEGACY_TOKEN})
    assert r.json() == {"ok": True, "panel_id": "kitchen-panel", "expires_at": None, "renewed": False}
    assert db.expiry(LEGACY_TOKEN) is None
    far = (_now() + timedelta(days=900)).isoformat()
    db.token(LIVE_TOKEN, far)
    assert _renew(db, {"X-Device-Token": LIVE_TOKEN}).json()["renewed"] is False
    assert db.expiry(LIVE_TOKEN) == far


@pytest.mark.parametrize("peer,headers", [
    (PHONE_IP, {}),                                     # anonymous LAN caller
    (PROXY, {"X-Real-IP": PANEL_IP}),                  # the panel's ADDRESS is not its token
    (PHONE_IP, {"X-Forwarded-For": PANEL_IP}),         # spoofed header
    (PHONE_IP, {"X-Session-ID": MEMBER_SID}),          # a member session renews nothing
    (PHONE_IP, {"X-Device-Token": "tok-expired"}),     # renewal is not a way back in
    (PHONE_IP, {"X-Device-Token": "tok-revoked"}),
])
def test_only_a_valid_token_holder_renews(db, peer, headers):
    db.token("tok-expired", (_now() - timedelta(seconds=1)).isoformat())
    db.token("tok-revoked", (_now() + timedelta(days=1)).isoformat(), revoked=1)
    before = db.con.execute("SELECT token_hash, expires_at FROM device_tokens ORDER BY token_hash").fetchall()
    assert _renew(db, headers, peer).status_code == 401
    assert db.con.execute("SELECT token_hash, expires_at FROM device_tokens ORDER BY token_hash").fetchall() == before


def test_a_token_revoked_in_the_db_but_not_the_cache_is_not_renewed(db):
    h = db.token(LIVE_TOKEN, (_now() + timedelta(days=3)).isoformat())
    db.con.execute("UPDATE device_tokens SET revoked = 1")
    assert _renew(db, {"X-Device-Token": LIVE_TOKEN}).status_code == 401
    assert panel_auth._token_cache[h]["revoked"] == 1
    assert panel_auth.lookup_device_token(LIVE_TOKEN) is None


# ---- the rollback never mints an admin ----------------------------------------------------------------------------------------
async def test_the_member_binding_rollback_never_resolves_an_admin(db, monkeypatch):
    import db_pool
    from contextlib import asynccontextmanager

    class _Bound:
        async def execute(self, sql, params=()):
            class C:
                async def fetchone(self_inner):
                    return {"user_id": "casey"}
            return C()

    @asynccontextmanager
    async def _ctx():
        yield _Bound()

    monkeypatch.setattr(db_pool, "get_db_ctx", _ctx)
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", "1")
    monkeypatch.setattr(panel_auth, "lookup_device_token", lambda raw: {"panel_id": "kitchen-panel", "role": "admin"})
    user = await panel_auth._resolve_device_token_user("legacy-admin-token")
    assert user["user_id"] == "casey" and user["role"] == "member"
    with pytest.raises(Exception):
        await auth.require_admin(user)
