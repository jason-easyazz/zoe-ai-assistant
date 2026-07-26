"""Contract tests for the /api/geo Nominatim proxy (touch-settings location search).

The outbound call is mocked — no network. Slim import chain (fastapi +
stdlib-only agent_safety), so the suite is ci_safe.
"""

from __future__ import annotations

import io
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routers.geo as geo  # noqa: E402

pytestmark = pytest.mark.ci_safe


def _client(user_id: str = "test-user") -> TestClient:
    app = FastAPI()
    app.include_router(geo.router)
    app.dependency_overrides[geo.get_current_user] = lambda: {"user_id": user_id, "role": "user"}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit(monkeypatch):
    monkeypatch.setattr(geo, "_rate_limit", {})
    monkeypatch.setattr(geo, "_last_outbound_at", 0.0)
    monkeypatch.setattr(geo, "_MIN_OUTBOUND_GAP_S", 0.0)


@pytest.fixture
def fake_upstream(monkeypatch):
    """Replace guarded_urlopen with a canned-payload fake; records calls."""
    calls: list[dict] = []
    state = {"payload": b"[]"}

    @contextmanager
    def _fake(url, *, timeout, headers=None):
        calls.append({"url": url, "timeout": timeout, "headers": headers or {}})
        yield io.BytesIO(state["payload"])

    monkeypatch.setattr(geo, "guarded_urlopen", _fake)
    return {"calls": calls, "state": state}


def test_search_passes_nominatim_array_through(fake_upstream):
    results = [{"lat": "-31.95", "lon": "115.86", "address": {"city": "Perth", "country": "Australia"}}]
    fake_upstream["state"]["payload"] = json.dumps(results).encode()

    resp = _client().get("/api/geo/search", params={"q": "perth", "limit": 5})

    assert resp.status_code == 200
    assert resp.json() == results
    (call,) = fake_upstream["calls"]
    assert call["url"].startswith("https://nominatim.openstreetmap.org/search?")
    assert "q=perth" in call["url"]
    assert "limit=5" in call["url"]
    assert "addressdetails=1" in call["url"]
    assert "zoe-ai-assistant" in call["headers"]["User-Agent"]


def test_reverse_passes_nominatim_object_through(fake_upstream):
    result = {"lat": "-31.95", "lon": "115.86", "address": {"city": "Perth"}}
    fake_upstream["state"]["payload"] = json.dumps(result).encode()

    resp = _client().get("/api/geo/reverse", params={"lat": -31.95, "lon": 115.86})

    assert resp.status_code == 200
    assert resp.json() == result
    (call,) = fake_upstream["calls"]
    assert call["url"].startswith("https://nominatim.openstreetmap.org/reverse?")
    assert "lat=-31.95" in call["url"]
    assert "lon=115.86" in call["url"]


def test_search_rejects_short_query(fake_upstream):
    resp = _client().get("/api/geo/search", params={"q": "a"})
    assert resp.status_code == 422
    assert fake_upstream["calls"] == []


def test_reverse_rejects_out_of_range_coords(fake_upstream):
    resp = _client().get("/api/geo/reverse", params={"lat": 91.0, "lon": 0.0})
    assert resp.status_code == 422
    assert fake_upstream["calls"] == []


def test_upstream_failure_maps_to_502(monkeypatch):
    def _boom(url, *, timeout, headers=None):
        raise OSError("connection refused")

    monkeypatch.setattr(geo, "guarded_urlopen", _boom)
    resp = _client().get("/api/geo/search", params={"q": "perth"})
    assert resp.status_code == 502


def test_unexpected_payload_shape_maps_to_502(fake_upstream):
    fake_upstream["state"]["payload"] = b'{"not": "a list"}'
    resp = _client().get("/api/geo/search", params={"q": "perth"})
    assert resp.status_code == 502


def test_window_rate_limit_answers_429(fake_upstream, monkeypatch):
    monkeypatch.setattr(geo, "_RATE_LIMIT_MAX", 2)
    client = _client()
    assert client.get("/api/geo/search", params={"q": "perth"}).status_code == 200
    assert client.get("/api/geo/search", params={"q": "perth"}).status_code == 200
    assert client.get("/api/geo/search", params={"q": "perth"}).status_code == 429
    # the window is per-endpoint: reverse is not consumed by search traffic
    fake_upstream["state"]["payload"] = b"{}"
    assert client.get("/api/geo/reverse", params={"lat": 0.0, "lon": 0.0}).status_code == 200


def test_window_is_per_caller_not_global(fake_upstream, monkeypatch):
    monkeypatch.setattr(geo, "_RATE_LIMIT_MAX", 1)
    assert _client("panel-a").get("/api/geo/search", params={"q": "perth"}).status_code == 200
    # panel-a's window is now full…
    assert _client("panel-a").get("/api/geo/search", params={"q": "perth"}).status_code == 429
    # …but panel-b (a different resolved caller) is unaffected
    assert _client("panel-b").get("/api/geo/search", params={"q": "perth"}).status_code == 200


def test_anonymous_caller_resolves_to_guest_bucket(fake_upstream, monkeypatch):
    """Without a session, the house get_current_user policy resolves to the
    shared guest identity — the endpoint still works (read-only feature) and
    guests share one rate bucket."""
    monkeypatch.setattr(geo, "_RATE_LIMIT_MAX", 1)
    app = FastAPI()
    app.include_router(geo.router)  # no dependency override: real auth path
    client = TestClient(app)
    assert client.get("/api/geo/search", params={"q": "perth"}).status_code == 200
    assert "search:guest" in geo._rate_limit
    assert client.get("/api/geo/search", params={"q": "perth"}).status_code == 429


def test_outbound_gap_enforces_one_request_per_second(fake_upstream, monkeypatch):
    monkeypatch.setattr(geo, "_MIN_OUTBOUND_GAP_S", 1.0)
    monkeypatch.setattr(geo, "_last_outbound_at", time.monotonic())
    resp = _client().get("/api/geo/search", params={"q": "perth"})
    assert resp.status_code == 429
    assert fake_upstream["calls"] == []
