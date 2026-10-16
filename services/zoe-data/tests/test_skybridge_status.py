"""Backend contract tests for the Skybridge surface."""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routers.skybridge import router  # noqa: E402

pytestmark = pytest.mark.ci_safe


def test_skybridge_status_endpoint_returns_runtime_contract():
    app = FastAPI()
    app.include_router(router)

    resp = TestClient(app).get("/api/skybridge/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["surface"] == "skybridge"
    assert data["status"] == "ready"
    assert data["entrypoint"] == "/touch/home.html"
    assert data["card_contract"]["status"] == "wired_for_calendar_weather_lists_people_clock_actions_v1"
    assert data["card_contract"]["supported_major"] == 1
    assert data["card_contract"]["data_domains"] == ["calendar", "weather", "lists", "people", "clock"]
    assert data["card_contract"]["voice_ws_domains"] == ["calendar", "weather", "lists", "people", "clock"]
    assert data["card_contract"]["action_domains"] == ["calendar", "lists", "people"]
    assert data["card_contract"]["context_refresh"] is True
    assert data["transports"]["local_ws"] is True
    assert "livekit" in data["transports"]
    assert data["capabilities"]["settings"] == 22
    assert data["capabilities"]["dynamic_cards"] == "calendar_weather_lists_people_clock_data_and_action_cards"


def test_status_includes_current_user_shape():
    """The panel's profile chip needs the device-session user from /status.
    Unauthenticated TestClient resolves to guest — assert the SHAPE + guest flag;
    the signed-in path is covered by get_current_user's own suite."""
    from main import app
    resp = TestClient(app).get("/api/skybridge/status")
    assert resp.status_code == 200
    u = resp.json().get("user")
    assert u is not None
    assert set(u.keys()) == {"user_id", "username", "role", "guest", "source"}
    assert u["guest"] is True  # no session on a bare client


def test_status_panel_binding_display_identity(monkeypatch):
    """A guest browser session on a bound panel shows the panel's default user
    as DISPLAY identity (source=panel_binding) — display, not authorization."""
    from fastapi import FastAPI
    import routers.skybridge as sky

    import contextlib
    import db_pool

    class _FakeDb:
        async def fetchrow(self, q, *a):
            if "FROM panels" in q:
                return {"ip_address": "192.168.1.61", "is_active": True}
            assert "panel_user_bindings" in q and a and a[0] == "zoe-touch-pi"
            return {"user_id": "jason", "name": "Jason"}

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield _FakeDb()
    monkeypatch.setattr(db_pool, "get_db_ctx", fake_ctx)
    app = FastAPI()
    app.include_router(sky.router)
    # The TCP peer is nginx (our proxy); the browser's address arrives in X-Real-IP.
    via_nginx = TestClient(app, client=("172.18.0.5", 50000))
    resp = via_nginx.get("/api/skybridge/status?panel_id=zoe-touch-pi",
                         headers={"X-Real-IP": "192.168.1.61"})
    u = resp.json()["user"]
    assert u["guest"] is False and u["username"] == "Jason" and u["source"] == "panel_binding"
    # Anti-enumeration: a caller NOT at the panel's registered IP stays guest.
    resp2 = via_nginx.get("/api/skybridge/status?panel_id=zoe-touch-pi",
                          headers={"X-Real-IP": "192.168.1.99"})
    assert resp2.json()["user"]["guest"] is True
    # Spoof attempt: client-controlled X-Forwarded-For must be IGNORED entirely.
    resp3 = via_nginx.get("/api/skybridge/status?panel_id=zoe-touch-pi",
                          headers={"X-Forwarded-For": "192.168.1.61"})
    assert resp3.json()["user"]["guest"] is True
    # Spoof attempt: a LAN device talking to :8000 DIRECTLY writes X-Real-IP itself - it is judged by its own address.
    direct = TestClient(app, client=("192.168.1.99", 50000))
    resp4 = direct.get("/api/skybridge/status?panel_id=zoe-touch-pi", headers={"X-Real-IP": "192.168.1.61"})
    assert resp4.json()["user"]["guest"] is True
    # A request that crossed the Cloudflare tunnel is never the panel by address.
    resp5 = via_nginx.get("/api/skybridge/status?panel_id=zoe-touch-pi",
                          headers=dict([("X-Real-IP", "192.168.1.61"), ("CF-Ray", "1")]))
    assert resp5.json()["user"]["guest"] is True


def test_status_panel_display_identity_follows_the_kiosk_credential_not_the_address(monkeypatch):
    """Over the private link the kiosk's address is no longer the registered one; its kiosk credential (minted from the
    device token by the panel's gate) still identifies it. Flag-dark: without ZOE_PANEL_KIOSK_CREDENTIAL it is ignored."""
    import contextlib
    import hashlib

    import db_pool
    import panel_kiosk_credential as kiosk
    import routers.skybridge as sky
    from routers import panel_auth

    class _FakeDb:
        async def fetchrow(self, q, *a):
            if "FROM panels" in q:
                return dict(ip_address="192.168.1.61", is_active=True)
            return dict(user_id="jason", name="Jason")

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield _FakeDb()

    monkeypatch.setattr(db_pool, "get_db_ctx", fake_ctx)
    raw = "skybridge-test-device-token"
    th = hashlib.sha256(raw.encode()).hexdigest()
    monkeypatch.setitem(panel_auth._token_cache, th, dict(panel_id="zoe-touch-pi", revoked=0, expires_at=None))
    cred = kiosk.mint_credential("zoe-touch-pi", th)
    other = kiosk.mint_credential("another-panel", th)
    app = FastAPI()
    app.include_router(sky.router)
    tailnet = TestClient(app, client=("172.18.0.5", 50000))
    url = "/api/skybridge/status?panel_id=zoe-touch-pi"

    def hdrs(cookie=None, *extra):
        h = [("X-Real-IP", "100.101.102.103")]
        if cookie:
            h.append(("Cookie", kiosk.COOKIE_NAME + "=" + cookie))
        return dict(h + list(extra))

    monkeypatch.delenv("ZOE_PANEL_KIOSK_CREDENTIAL", raising=False)
    assert tailnet.get(url, headers=hdrs(cred)).json()["user"]["guest"] is True          # flag off: ignored
    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")
    assert tailnet.get(url, headers=hdrs(cred)).json()["user"]["source"] == "panel_binding"
    # negative controls: no credential, another panel's credential, or the credential over the tunnel
    assert tailnet.get(url, headers=hdrs()).json()["user"]["guest"] is True
    assert tailnet.get(url, headers=hdrs(other)).json()["user"]["guest"] is True
    assert tailnet.get(url, headers=hdrs(cred, ("CF-Ray", "1"))).json()["user"]["guest"] is True


def test_status_no_panel_id_stays_guest():
    from fastapi import FastAPI
    import routers.skybridge as sky
    app = FastAPI()
    app.include_router(sky.router)
    resp = TestClient(app).get("/api/skybridge/status")
    assert resp.json()["user"]["guest"] is True
