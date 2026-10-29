"""The gate in front of Home Assistant's own web UI / API / WebSocket through nginx (``/ha/``, routers/ha_ui.py).

``GET /api/ha-ui/gate`` is nginx's ``auth_request`` target: 204 only for a signed-in member, the panel (strict registered form),
or a holder of the signed ``/ha/``-scoped cookie ``POST /api/ha-ui/session`` sets. Everyone else - an anonymous LAN device, a
guest session, a forged / expired / wrong-purpose cookie, the panel's address over the tunnel, the legacy
``ZOE_UNAUTHENTICATED_ROLE`` promotion - gets 401. Each refusal has a positive twin in the same test.

Real HTTP through the real ``get_current_user`` + device-token resolution (zoe-auth and the panel registry faked). The nginx
wiring is pinned in tests/unit/test_ha_ui_gate_nginx.py and proven against a real nginx by test_ha_ui_gate_nginx_live.py
(opt-in). Invented data; TEST-NET addresses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import panel_principal
import path_cookie_gate
import routers.ha_ui as ha_ui
import routers.panel_auth as panel_auth

pytestmark = pytest.mark.ci_safe

PANEL_IP, PHONE_IP, PROXY = "192.0.2.50", "192.0.2.77", "127.0.0.1"
MEMBER_SID, GUEST_SID, PANEL_TOKEN = "sid-member", "sid-guest", "tok-kitchen-panel"
LAN_HOST = "192.0.2.10"
ACTIVE_PANELS = {PANEL_IP, "kitchen-panel"}

STRANGERS = [
    (PHONE_IP, {}),                                                  # anonymous, straight at :8000 on the LAN
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),                            # spoofed X-Real-IP from an untrusted peer
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}),    # a minted guest session is not the panel
    (PROXY, {"X-Real-IP": PANEL_IP, "cf-ray": "abc"}),              # the panel's address, but over Cloudflare
    (PHONE_IP, {"X-Device-Token": "not-a-real-token"}),             # an unknown device token is a guest
]
PANEL = [(PROXY, {"X-Real-IP": PANEL_IP}), (PHONE_IP, {"X-Device-Token": PANEL_TOKEN})]
MEMBER = [(PHONE_IP, {"X-Session-ID": MEMBER_SID}), (PROXY, {"X-Session-ID": MEMBER_SID, "cf-ray": "abc"})]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_HA_UI_GATE",
                "ZOE_DEVICE_TOKEN_BINDS_MEMBER", "ZOE_HA_UI_TTL_S", "ZOE_HA_UI_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(sid):
        role = {MEMBER_SID: ("casey", "user"), GUEST_SID: ("guest", "guest")}.get(sid)
        return {"user_id": role[0], "role": role[1], "username": role[0], "permissions": []} if role else None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(PANEL_TOKEN),
                        {"panel_id": "kitchen-panel", "role": "voice-daemon", "expires_at": None, "revoked": 0})

    async def _with_registry(_db, check, value):
        return value in ACTIVE_PANELS

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    yield
    auth._session_cache.clear()


def _client(peer):
    app = FastAPI()
    app.include_router(ha_ui.router)
    return TestClient(app, client=(peer, 50000), base_url="http://" + LAN_HOST)


def _gate(peer, headers=None, cookie=None, name=None):
    c = _client(peer)
    if cookie is not None:
        c.cookies.set(name or ha_ui.GATE.cookie, cookie, path="/")
    return c.get("/api/ha-ui/gate", headers=headers or {})


@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_are_refused_at_the_gate(peer, headers):
    r = _gate(peer, headers)
    assert r.status_code == 401, (headers, r.text)
    assert list(r.json()) == ["detail"] and "set-cookie" not in r.headers


@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_members_and_the_panel_pass_the_gate(peer, headers):
    r = _gate(peer, headers)
    assert r.status_code == 204 and r.content == b""
    assert r.headers["cache-control"] == "no-store"


def test_negative_control_the_panel_passes_only_while_registered():
    assert _gate(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 204
    ACTIVE_PANELS.discard(PANEL_IP)
    try:
        assert _gate(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 401
    finally:
        ACTIVE_PANELS.add(PANEL_IP)


def test_ha_control_open_rollback_does_not_open_the_ha_ui(monkeypatch):
    """ZOE_HA_CONTROL_SCOPE=open is the HOME-CONTROL rollback; it must not open Home Assistant's own UI."""
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert _gate(PHONE_IP).status_code == 401
    assert _gate(PHONE_IP, {"X-Session-ID": MEMBER_SID}).status_code == 204


def test_the_legacy_unauthenticated_admin_promotion_is_not_a_member(monkeypatch):
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    assert _gate(PHONE_IP).status_code == 401
    assert _gate(PHONE_IP, {"X-Session-ID": MEMBER_SID}).status_code == 204


def test_the_off_switch_is_a_gate_wide_rollback(monkeypatch):
    assert _gate(PHONE_IP).status_code == 401
    monkeypatch.setenv("ZOE_HA_UI_GATE", "off")
    assert _gate(PHONE_IP).status_code == 204
    monkeypatch.setenv("ZOE_HA_UI_GATE", "of")  # a typo is not "off": the gate stays on
    assert _gate(PHONE_IP).status_code == 401


def _mint_cookie(peer, headers):
    r = _client(peer).post("/api/ha-ui/session", headers=headers)
    assert r.status_code == 200, r.text
    set_cookie = r.headers["set-cookie"]
    return set_cookie.split(";", 1)[0].split("=", 1)[1], set_cookie, r


@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_a_member_or_the_panel_trades_credentials_for_a_path_scoped_cookie(peer, headers):
    value, set_cookie, r = _mint_cookie(peer, headers)
    low = set_cookie.lower()
    assert set_cookie.startswith("zoe_ha_ui=")
    assert "httponly" in low and "samesite=strict" in low and "path=/ha/" in low and "domain=" not in low
    assert r.headers["cache-control"] == "no-store" and r.json()["ok"] is True
    assert _gate(PHONE_IP, cookie=value).status_code == 204   # the cookie alone: what a WebSocket handshake carries
    assert _gate(PHONE_IP).status_code == 401                  # control: the same device without it


def test_the_cookie_is_secure_behind_https():
    _, set_cookie, _ = _mint_cookie(PHONE_IP, {"X-Session-ID": MEMBER_SID, "X-Forwarded-Proto": "https"})
    assert "secure" in set_cookie.lower()


@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_cannot_mint_a_cookie(peer, headers):
    r = _client(peer).post("/api/ha-ui/session", headers=headers)
    assert r.status_code in (401, 403) and "set-cookie" not in r.headers


def test_forged_tampered_expired_and_other_purpose_cookies_are_refused(monkeypatch):
    value, _, _ = _mint_cookie(PHONE_IP, {"X-Session-ID": MEMBER_SID})
    assert _gate(PHONE_IP, cookie=value).status_code == 204                     # control
    assert _gate(PHONE_IP, cookie=value[:-2] + "xx").status_code == 401        # tampered signature
    assert _gate(PHONE_IP, cookie="garbage").status_code == 401
    assert _gate(PHONE_IP, cookie=value, name="zoe_ma_ui").status_code == 401  # right value, wrong cookie name
    # another gate's cookie, signed with the SAME base secret, must not verify here (purpose-separated keys + claims)
    other = path_cookie_gate.PathCookieGate(
        purpose="ma-ui", cookie="zoe_ha_ui", paths=("/x/",), secret_env="ZOE_TEST_OTHER_SECRET",
        key=lambda: path_cookie_gate.derived_key("", "test-internal-token", "zoe-ma-ui-v1"),
        ttl_s=lambda: 600, enabled=lambda: True)
    assert _gate(PHONE_IP, cookie=other.mint("casey")["token"]).status_code == 401
    # expired
    import time as _time
    real = _time.time()
    monkeypatch.setattr("auth_handoff.time.time", lambda: real + ha_ui.ttl_s() + 5)
    assert _gate(PHONE_IP, cookie=value).status_code == 401


def test_derived_keys_are_per_label():
    a = path_cookie_gate.derived_key("", "base", "zoe-ha-ui-v1")
    assert a and a != path_cookie_gate.derived_key("", "base", "zoe-ma-ui-v1") and a != "base"
    assert path_cookie_gate.derived_key("own", "base", "zoe-ha-ui-v1") == "own"
    assert path_cookie_gate.derived_key("", "", "zoe-ha-ui-v1") is None
