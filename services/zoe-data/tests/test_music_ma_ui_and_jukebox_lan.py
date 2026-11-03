"""Music follow-ups to #2108: the gate in front of Music Assistant's own web UI, and the jukebox QR's origin.

* ``GET /api/music/ma-ui/gate`` is nginx's ``auth_request`` target for ``/modules/music-assistant/`` (MA's UI + WebSocket): 204
  only for a signed-in member, the panel, or a holder of the signed path-scoped cookie ``POST /api/music/ma-ui/session`` sets.
  Everyone else - an anonymous LAN device, a guest session, a forged / expired / wrong-purpose cookie, the panel's address over
  the tunnel, the legacy ``ZOE_UNAUTHENTICATED_ROLE`` promotion - gets 401. Each refusal has a positive twin in the same test.
* The jukebox QR encodes the LAN origin the panel loaded the page from: never ``ZOE_PUBLIC_URL``, never a tunnel origin
  (a tunnelled request mints nothing and renders nothing).

Real HTTP through the real ``get_current_user`` + device-token resolution (zoe-auth and the panel registry faked, the same
fixtures as test_arch_a2_auth_gaps.py). The nginx half is pinned in tests/unit/test_ma_ui_gate_nginx.py. Invented data;
TEST-NET addresses.
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
import auth_handoff
import ma_ui_gate
import panel_principal
import routers.music as music
import routers.music_ma_ui as ma_ui
import routers.panel_auth as panel_auth

pytestmark = pytest.mark.ci_safe

PANEL_IP, PHONE_IP, PROXY = "192.0.2.50", "192.0.2.77", "127.0.0.1"
MEMBER_SID, GUEST_SID, PANEL_TOKEN = "sid-member", "sid-guest", "tok-kitchen-panel"
LAN_HOST = "192.0.2.10"
TUNNEL_ORIGIN = "https://tunnel.example.test"
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
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_MA_UI_GATE",
                "ZOE_DEVICE_TOKEN_BINDS_MEMBER", "ZOE_PUBLIC_URL", "ZOE_MA_UI_TTL_S"):
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
    app.include_router(ma_ui.router)
    app.include_router(music.router)
    return TestClient(app, client=(peer, 50000), base_url="http://" + LAN_HOST)


def _gate(peer, headers=None, cookie=None):
    c = _client(peer)
    if cookie is not None:
        c.cookies.set(ma_ui_gate.COOKIE, cookie, path="/")
    return c.get("/api/music/ma-ui/gate", headers=headers or {})


# ── the Music Assistant UI gate ─────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_are_refused_at_the_gate(peer, headers):
    r = _gate(peer, headers)
    assert r.status_code == 401, (headers, r.text)
    assert list(r.json()) == ["detail"] and "set-cookie" not in r.headers  # nothing but the refusal


@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_members_and_the_panel_pass_the_gate(peer, headers):
    r = _gate(peer, headers)
    assert r.status_code == 204 and r.content == b""
    assert r.headers["cache-control"] == "no-store"


def test_ha_control_open_rollback_does_not_open_the_music_ui(monkeypatch):
    """ZOE_HA_CONTROL_SCOPE=open is a HOME-CONTROL rollback; this surface can reconfigure the whole music stack."""
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert _gate(PHONE_IP).status_code == 401
    assert _gate(PHONE_IP, {"X-Session-ID": MEMBER_SID}).status_code == 204  # control: the same env, a member still passes


def test_the_legacy_unauthenticated_admin_promotion_is_not_a_member(monkeypatch):
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")  # every header-less LAN device becomes admin under this
    assert _gate(PHONE_IP).status_code == 401
    assert _gate(PHONE_IP, {"X-Session-ID": MEMBER_SID}).status_code == 204


def test_the_off_switch_is_a_gate_wide_rollback(monkeypatch):
    assert _gate(PHONE_IP).status_code == 401
    monkeypatch.setenv("ZOE_MA_UI_GATE", "off")
    assert _gate(PHONE_IP).status_code == 204  # operator rollback, read per request


# ── the cookie a WebSocket handshake / window.open can carry ────────────────────────────────────────────────────────────────

def _mint_cookie(peer, headers):
    r = _client(peer).post("/api/music/ma-ui/session", headers=headers)
    assert r.status_code == 200, r.text
    set_cookie = r.headers["set-cookie"]
    value = set_cookie.split(";", 1)[0].split("=", 1)[1]
    return value, set_cookie, r


@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_a_member_or_the_panel_trades_credentials_for_a_path_scoped_cookie(peer, headers):
    value, set_cookie, r = _mint_cookie(peer, headers)
    low = set_cookie.lower()
    assert set_cookie.startswith(ma_ui_gate.COOKIE + "=")
    assert "httponly" in low and "samesite=strict" in low
    assert "path=/modules/music-assistant/" in low  # the cookie is sent to nothing but the gated path
    assert "domain=" not in low
    assert r.headers["cache-control"] == "no-store" and r.json()["ok"] is True
    # the cookie alone (an anonymous device, no headers) now passes the gate - that is what a WebSocket handshake carries
    assert _gate(PHONE_IP, cookie=value).status_code == 204
    assert _gate(PHONE_IP).status_code == 401  # control: the same device without it


@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_get_no_cookie(peer, headers):
    r = _client(peer).post("/api/music/ma-ui/session", headers=headers)
    assert r.status_code == 403
    assert "set-cookie" not in r.headers


def test_cookie_is_secure_only_over_https():
    http = _client(PHONE_IP).post("/api/music/ma-ui/session", headers={"X-Session-ID": MEMBER_SID})
    https = _client(PHONE_IP).post("/api/music/ma-ui/session",
                                   headers={"X-Session-ID": MEMBER_SID, "X-Forwarded-Proto": "https"})
    assert "secure" not in http.headers["set-cookie"].lower()  # a Secure cookie would be dropped on the :80 server block
    assert "secure" in https.headers["set-cookie"].lower()


def test_forged_expired_and_wrong_purpose_cookies_are_refused(monkeypatch):
    import music_jukebox
    import music_setup

    good, _, _ = _mint_cookie(PHONE_IP, {"X-Session-ID": MEMBER_SID})
    assert _gate(PHONE_IP, cookie=good).status_code == 204
    assert _gate(PHONE_IP, cookie=good[:-2] + "xx").status_code == 401                       # forged signature
    assert _gate(PHONE_IP, cookie="not-a-token").status_code == 401
    assert _gate(PHONE_IP, cookie=music_jukebox.mint("kitchen-panel")["token"]).status_code == 401   # another flow's key
    assert _gate(PHONE_IP, cookie=music_setup.mint("spotify")["token"]).status_code == 401
    other = ma_ui_gate._TOKENS.mint({"k": "other", "who": "casey"}, 60)                       # this key, another purpose
    assert _gate(PHONE_IP, cookie=other).status_code == 401
    monkeypatch.setattr(ma_ui_gate, "ttl_s", lambda: -10)
    assert _gate(PHONE_IP, cookie=ma_ui_gate.mint("casey")["token"]).status_code == 401      # expired


def test_a_cookie_for_another_secret_is_refused(monkeypatch):
    good, _, _ = _mint_cookie(PHONE_IP, {"X-Session-ID": MEMBER_SID})
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "rotated-internal-token")  # a rotated secret invalidates every outstanding cookie
    assert _gate(PHONE_IP, cookie=good).status_code == 401


# ── the jukebox QR encodes the LAN origin, never a public one ───────────────────────────────────────────────────────────────

@pytest.fixture
def encoded(monkeypatch):
    import setup_qr

    urls = []
    monkeypatch.setattr(setup_qr, "render_svg", lambda url: urls.append(url) or b"<svg/>")  # segno is not in the slim venv
    return urls


def _show_qr(peer, headers, start_headers=None):
    c = _client(peer)
    r = c.post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"}, headers=start_headers or headers)
    return c, r


def test_the_qr_names_the_lan_origin_even_when_a_public_url_is_configured(monkeypatch, encoded):
    monkeypatch.setenv("ZOE_PUBLIC_URL", TUNNEL_ORIGIN)
    c, r = _show_qr(PROXY, {"X-Real-IP": PANEL_IP})
    assert r.status_code == 200
    assert c.get(r.json()["qr_path"]).status_code == 200
    assert encoded[0].startswith(f"http://{LAN_HOST}/jukebox.html#t="), encoded
    assert TUNNEL_ORIGIN not in encoded[0]
    # negative control: the helper the setup flows use WOULD have put the public origin in this QR - the test can tell
    req = type("R", (), {"headers": {"host": LAN_HOST}, "url": type("U", (), {"scheme": "http"})(), "base_url": ""})()
    assert auth_handoff.phone_base_url(req) == TUNNEL_ORIGIN
    assert auth_handoff.lan_base_url(req) == f"http://{LAN_HOST}"


def test_the_qr_follows_the_host_and_scheme_the_panel_loaded(encoded):
    c = _client(PROXY)
    handle = c.post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"},
                    headers={"X-Real-IP": PANEL_IP}).json()["qr_path"]
    assert c.get(handle, headers={"Host": "zoe.local", "X-Forwarded-Proto": "https"}).status_code == 200
    assert encoded[0].startswith("https://zoe.local/jukebox.html#t=")


def test_a_tunnelled_request_mints_nothing_and_renders_nothing(monkeypatch, encoded):
    monkeypatch.setenv("ZOE_PUBLIC_URL", TUNNEL_ORIGIN)
    # a signed-in member over the tunnel (allowed elsewhere) still gets no jukebox code
    for tunnel in ({"cf-ray": "abc"}, {"cf-connecting-ip": "203.0.113.9"}, {"cf-visitor": '{"scheme":"https"}'}):
        r = _client(PROXY).post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"},
                                headers={"X-Session-ID": MEMBER_SID, **tunnel})
        assert r.status_code == 403, tunnel
    # a handle minted on the LAN and redeemed through the tunnel renders nothing - and is spent
    c, ok = _show_qr(PROXY, {"X-Real-IP": PANEL_IP})
    assert ok.status_code == 200  # control: the same call on the LAN works
    path = ok.json()["qr_path"]
    assert c.get(path, headers={"cf-ray": "abc"}).status_code == 404
    assert c.get(path).status_code == 404
    assert encoded == []


@pytest.mark.parametrize("host,ok", [
    ("192.168.1.218", True), ("192.168.1.218:8443", True), ("10.0.0.7", True), ("172.20.1.5", True), ("127.0.0.1:8000", True),
    ("[fd00::1]:443", True), ("zoe.local", True), ("zoe.home.arpa", True), ("zoe", True), ("localhost:8080", True),
    ("zoe.the411.life", False), ("tunnel.example.test", False), ("evil.example.com:443", False), ("zoe.local.evil.com", False),
    ("8.8.8.8", False), ("100.64.0.9", False), ("[2606:4700::1111]", False), ("", False), ("@evil.com", False),
])
def test_only_a_lan_host_is_ever_an_origin(host, ok, monkeypatch):
    monkeypatch.delenv("ZOE_LAN_HOSTS", raising=False)
    assert auth_handoff._is_lan_host(host) is ok, host


def test_a_lan_host_with_a_public_looking_name_is_an_explicit_operator_choice(monkeypatch):
    assert auth_handoff._is_lan_host("zoe.the411.life") is False
    monkeypatch.setenv("ZOE_LAN_HOSTS", "zoe.the411.life, other.example")
    assert auth_handoff._is_lan_host("zoe.the411.life:443") is True


def test_a_forged_public_host_mints_nothing_and_renders_nothing_even_without_cloudflare_headers(encoded):
    """Cross-review finding: a non-Cloudflare tunnel (or a caller) can put a public name in Host; that must never be an origin."""
    c = _client(PROXY)
    r = c.post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"},
               headers={"X-Real-IP": PANEL_IP, "Host": "zoe.the411.life"})
    assert r.status_code == 403
    ok = c.post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"}, headers={"X-Real-IP": PANEL_IP})
    assert ok.status_code == 200  # control: the LAN host works
    assert c.get(ok.json()["qr_path"], headers={"Host": "tunnel.example.test"}).status_code == 404
    assert encoded == []


def test_x_forwarded_host_is_not_an_input(encoded):
    c = _client(PROXY)
    path = c.post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"},
                  headers={"X-Real-IP": PANEL_IP}).json()["qr_path"]
    assert c.get(path, headers={"X-Forwarded-Host": "zoe.the411.life"}).status_code == 200
    assert encoded[0].startswith(f"http://{LAN_HOST}/jukebox.html#t="), encoded


def test_the_jukebox_qr_route_cannot_reach_the_public_url_helper():
    """Structural pin: neither jukebox handler may call ``phone_base_url`` (it reads ZOE_PUBLIC_URL)."""
    import inspect

    for fn in (music.jukebox_start, music.jukebox_qr):
        src = inspect.getsource(fn)
        assert "phone_base_url" not in src and "ZOE_PUBLIC_URL" not in src, fn.__name__
    assert "lan_base_url" in inspect.getsource(music.jukebox_qr)
