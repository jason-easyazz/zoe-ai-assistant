"""Backend contract tests for the Skybridge surface."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routers.skybridge import router  # noqa: E402


def test_skybridge_status_endpoint_returns_runtime_contract():
    app = FastAPI()
    app.include_router(router)

    resp = TestClient(app).get("/api/skybridge/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["surface"] == "skybridge"
    assert data["status"] == "ready"
    assert data["entrypoint"] == "/touch/skybridge.html"
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
    resp = TestClient(app).get("/api/skybridge/status?panel_id=zoe-touch-pi",
                               headers={"X-Forwarded-For": "192.168.1.61"})
    u = resp.json()["user"]
    assert u["guest"] is False and u["username"] == "Jason" and u["source"] == "panel_binding"
    # Anti-enumeration: a caller NOT at the panel's registered IP stays guest.
    resp2 = TestClient(app).get("/api/skybridge/status?panel_id=zoe-touch-pi",
                                headers={"X-Forwarded-For": "192.168.1.99"})
    assert resp2.json()["user"]["guest"] is True


def test_status_no_panel_id_stays_guest():
    from fastapi import FastAPI
    import routers.skybridge as sky
    app = FastAPI()
    app.include_router(sky.router)
    resp = TestClient(app).get("/api/skybridge/status")
    assert resp.json()["user"]["guest"] is True
