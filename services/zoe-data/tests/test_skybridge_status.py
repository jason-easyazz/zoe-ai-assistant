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
    assert set(u.keys()) == {"user_id", "username", "role", "guest"}
    assert u["guest"] is True  # no session on a bare client
