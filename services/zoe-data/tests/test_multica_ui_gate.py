"""The Zoe sign-in gate in front of the Multica board (multica_ui_gate.py + routers/multica_ui.py).

Real HTTP through the real router, the REAL ``get_current_user`` + device-token resolution (zoe-auth's session lookup and the
panel registry are faked), and the REAL adults-only check (``manner_block.eligible``) reading a real sqlite with the two
tables it reads. tests/unit/test_multica_ui_gate_nginx.py pins the nginx half; labs/multica-signin-gate/ runs both in front of
the real Multica images. Invented data; TEST-NET addresses.
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import auth_handoff
import multica_ui_gate
import persona_layer
import routers.multica_ui as multica_ui
import routers.panel_auth as panel_auth

pytestmark = pytest.mark.ci_safe

PEER = "192.0.2.77"
SIDS = {
    "sid-ada": ("ada_uid", "user"),          # an adult member
    "sid-fam": ("fam_uid", "family-admin"),  # the household admin
    "sid-mia": ("mia_uid", "child"),         # a child account
    "sid-kid": ("kid_uid", "user"),          # an adult ROLE, but member_modes says minor
    "sid-odd": ("odd_uid", "visitor"),       # an unknown role: not positively an adult
    "sid-ghost": ("ghost_uid", "user"),      # a session whose account row is missing
    "sid-guest": ("guest", "guest"),         # a minted guest session
}
PANEL_TOKEN = "tok-kitchen-panel"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for var in ("ZOE_MULTICA_UI_GATE", "ZOE_MULTICA_UI_TTL_S", "ZOE_DEVICE_TOKEN_BINDS_MEMBER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZOE_MULTICA_UI_SECRET", "test-multica-ui-secret")
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(sid):
        hit = SIDS.get(sid)
        return {"user_id": hit[0], "role": hit[1], "username": hit[0], "permissions": []} if hit else None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(PANEL_TOKEN),
                        {"panel_id": "kitchen-panel", "role": "voice-daemon", "expires_at": None, "revoked": 0})

    path = str(tmp_path / "m.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT NOT NULL, minor INTEGER NOT NULL DEFAULT 0,"
                " updated_by TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL)")
    con.executemany("INSERT INTO auth_users VALUES (?,?,?)", [
        ("ada_uid", "ada", "user"), ("fam_uid", "fam", "family-admin"), ("mia_uid", "mia", "child"),
        ("kid_uid", "kid", "user"), ("odd_uid", "odd", "visitor")])
    con.execute("INSERT INTO member_modes VALUES ('kid_uid', 'kid', 1, 'o', 't')")
    con.commit()
    con.close()

    @asynccontextmanager
    async def ctx(db=None):
        if db is not None:
            yield db
            return
        conn = await aiosqlite.connect(path)
        try:
            yield conn
        finally:
            await conn.close()

    monkeypatch.setattr(persona_layer, "_db", ctx)
    yield
    auth._session_cache.clear()


def _client():
    app = FastAPI()
    app.include_router(multica_ui.router)
    return TestClient(app, client=(PEER, 50000))


def _gate(headers=None, cookies=None):
    c = _client()
    for k, v in (cookies or {}).items():
        c.cookies.set(k, v)
    return c.get("/api/multica-ui/gate", headers=headers or {})


# -- the gate, credentials -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sid", ["sid-ada", "sid-fam"])
def test_adult_members_pass(sid):
    r = _gate({"X-Session-ID": sid})
    assert r.status_code == 204 and r.content == b""
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [
    {},                                        # anonymous
    {"X-Session-ID": "sid-guest"},             # a minted guest session
    {"X-Session-ID": "not-a-session"},         # an invalid / expired session (get_current_user's 401)
    {"X-Device-Token": "not-a-real-token"},    # an unknown device token is a guest
    {"X-Device-Token": PANEL_TOKEN},           # the wall panel: a kiosk is whoever stands at it
    {"X-Real-IP": "192.0.2.50"},               # a forged panel address
    {"X-Zoe-User-Id": "ada_uid"},              # forged internal-trust headers (nginx blanks them; zoe-data must not care)
    {"X-Internal-Token": "anything", "X-Zoe-User-Id": "ada_uid"},
])
def test_strangers_and_the_panel_are_401(headers):
    assert _gate(headers).status_code == 401
    r = _client().post("/api/multica-ui/session", headers=headers)
    assert r.status_code == 401 and not r.headers.get_list("set-cookie")


@pytest.mark.parametrize("sid", ["sid-mia", "sid-kid", "sid-odd", "sid-ghost"])
def test_signed_in_but_not_an_adult_member_is_403(sid):
    """A child role, a minor member_modes row, an unknown role, a missing account row: not POSITIVELY an adult."""
    assert _gate({"X-Session-ID": sid}).status_code == 403


def test_the_bound_panel_rollback_is_still_not_a_person(monkeypatch):
    """ZOE_DEVICE_TOKEN_BINDS_MEMBER=1 resolves a bound panel token to its member - the board still refuses the device."""
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", "1")
    principal = {"user_id": "ada_uid", "role": "member", "panel_id": "kitchen-panel", "device_token_role": "voice-daemon"}
    assert multica_ui_gate.is_signed_in(principal) is False


@pytest.mark.parametrize("role", ["family-admin", "user", "admin", "member"])
def test_no_unauthenticated_role_setting_is_a_sign_in(monkeypatch, role):
    """ZOE_UNAUTHENTICATED_ROLE=family-admin makes every header-less LAN caller an admin; the board does not trust it, nor any
    other value of that setting."""
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", role)
    assert _gate().status_code == 401
    assert _client().post("/api/multica-ui/session").status_code == 401


def test_zoe_auth_down_is_closed(monkeypatch):
    async def _down(sid):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    monkeypatch.setattr(auth, "_validate_with_auth_service", _down)
    assert _gate({"X-Session-ID": "sid-ada"}).status_code == 503  # nginx: a non-2xx/401/403 auth_request answer is a 500


def test_a_failed_eligibility_lookup_is_closed(monkeypatch):
    @asynccontextmanager
    async def broken(db=None):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(persona_layer, "_db", broken)
    assert _gate({"X-Session-ID": "sid-ada"}).status_code == 403


def test_rollback_flag_opens_the_gate(monkeypatch):
    monkeypatch.setenv("ZOE_MULTICA_UI_GATE", "off")
    assert _gate().status_code == 204


# -- the cookie ----------------------------------------------------------------------------------------------------------------

def _mint(sid):
    return _client().post("/api/multica-ui/session", headers={"X-Session-ID": sid})


def test_an_adult_member_gets_one_strict_cookie_per_board_path():
    r = _mint("sid-ada")
    assert r.status_code == 200 and r.json()["ok"] is True
    cookies = r.headers.get_list("set-cookie")
    assert len(cookies) == 3, cookies
    paths = set()
    token = None
    for c in cookies:
        parts = [p.strip() for p in c.split(";")]
        name, value = parts[0].split("=", 1)
        assert name == multica_ui_gate.COOKIE
        token = token or value
        assert value == token  # one token, three paths
        low = [p.lower() for p in parts]
        assert "httponly" in low and "samesite=strict" in low
        assert "secure" not in low  # plain http request; https sets Secure (below)
        paths.add(next(p.split("=", 1)[1] for p in parts if p.lower().startswith("path=")))
    assert paths == {"/multica/", "/multica-api/", "/_next/"}
    assert _gate(cookies={multica_ui_gate.COOKIE: token}).status_code == 204


def test_the_cookie_is_secure_over_https():
    r = _client().post("/api/multica-ui/session", headers={"X-Session-ID": "sid-ada", "X-Forwarded-Proto": "https"})
    assert all("secure" in [p.strip().lower() for p in c.split(";")] for c in r.headers.get_list("set-cookie"))


@pytest.mark.parametrize("sid,want", [("sid-mia", 403), ("sid-kid", 403), ("sid-guest", 401), (None, 401),
                                      ("not-a-session", 401)])
def test_nobody_else_can_mint_the_cookie(sid, want):
    r = _client().post("/api/multica-ui/session", headers={"X-Session-ID": sid} if sid else {})
    assert r.status_code == want
    assert not r.headers.get_list("set-cookie")


def test_the_panel_cannot_mint_the_cookie():
    r = _client().post("/api/multica-ui/session", headers={"X-Device-Token": PANEL_TOKEN})
    assert r.status_code == 401 and not r.headers.get_list("set-cookie")


def _forge(claims, ttl=600, key="test-multica-ui-secret"):
    return auth_handoff.SignedTokens("X_UNUSED", lambda: key).mint(claims, ttl)


@pytest.mark.parametrize("token", [
    "garbage",
    "a.b",
    _forge({"k": "multica-ui", "who": "x"}, key="some-other-key"),     # wrong key
    _forge({"k": "ma-ui", "who": "x"}),                                # another purpose's token, same key
    _forge({"k": "multica-ui", "who": "x"}, ttl=-5),                   # expired
])
def test_a_bad_cookie_is_401(token):
    assert _gate(cookies={multica_ui_gate.COOKIE: token}).status_code == 401


def test_a_rotated_secret_kills_old_cookies(monkeypatch):
    token = _mint("sid-ada").headers.get_list("set-cookie")[0].split(";")[0].split("=", 1)[1]
    assert _gate(cookies={multica_ui_gate.COOKIE: token}).status_code == 204
    monkeypatch.setenv("ZOE_MULTICA_UI_SECRET", "rotated")
    assert _gate(cookies={multica_ui_gate.COOKIE: token}).status_code == 401


def test_the_key_is_purpose_derived_from_the_internal_token(monkeypatch):
    """With no own secret, the key is derived for this purpose: never the raw internal token, never the MA UI key."""
    monkeypatch.delenv("ZOE_MULTICA_UI_SECRET", raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "internal-xyz")
    key = multica_ui_gate._key()
    assert key and key != "internal-xyz"
    assert _gate(cookies={multica_ui_gate.COOKIE: _forge({"k": "multica-ui", "who": "x"}, key="internal-xyz")}).status_code == 401
    assert _gate(cookies={multica_ui_gate.COOKIE: _forge({"k": "multica-ui", "who": "x"}, key=key)}).status_code == 204


def test_ttl_floor_and_default(monkeypatch):
    assert multica_ui_gate.ttl_s() == 28800
    monkeypatch.setenv("ZOE_MULTICA_UI_TTL_S", "5")
    assert multica_ui_gate.ttl_s() == 300
