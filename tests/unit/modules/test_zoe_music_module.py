"""
Regression tests for the zoe-music module bridge (modules/zoe-music/main.py).

Covers the hardening fixes:
  (b) an unauthenticated external call to a state-changing tool is rejected,
      a correctly-tokened call is accepted (the auth gate),
  (P3) request/search models reject empty queries / out-of-range values.
"""
import pytest
from fastapi.testclient import TestClient

from conftest import load_music_main

TOKEN = "test-service-token-123"


@pytest.fixture()
def main(monkeypatch):
    mod = load_music_main()
    monkeypatch.setenv("ZOE_MUSIC_SERVICE_TOKEN", TOKEN)

    # Keep the gate tests off the network: pretend MA is down and stub the HA
    # fallback so an authorized /tools/play returns cleanly.
    async def _ma_down():
        return False

    async def _ha_noop(service, extra=None):
        return None

    monkeypatch.setattr(mod, "_ma_available", _ma_down)
    monkeypatch.setattr(mod, "_ha_service", _ha_noop)
    return mod


@pytest.fixture()
def client(main):
    return TestClient(main.app)


def auth(extra=None):
    h = {"X-Zoe-Service-Token": TOKEN}
    if extra:
        h.update(extra)
    return h


# ── (b) auth gate ──────────────────────────────────────────────────────────

def test_unauthenticated_state_change_is_rejected(client):
    r = client.post("/tools/play", json={"query": "beatles"})
    assert r.status_code == 401


def test_wrong_token_is_rejected(client):
    r = client.post(
        "/tools/play", json={"query": "beatles"},
        headers={"X-Zoe-Service-Token": "nope"},
    )
    assert r.status_code == 401


def test_legit_tokened_call_succeeds(client):
    r = client.post("/tools/play", json={"query": "beatles"}, headers=auth())
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize("path", [
    "/tools/play", "/tools/pause", "/tools/resume",
    "/tools/skip", "/tools/previous", "/tools/volume",
])
def test_every_state_changing_route_requires_token(client, path):
    body = {"level": 30} if path.endswith("volume") else {"query": "x"}
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers=auth()).status_code == 200


def test_read_routes_also_gated(client):
    assert client.get("/tools/now_playing").status_code == 401
    assert client.get("/tools/search", params={"query": "x"}).status_code == 401


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


def test_fail_closed_when_token_unset(monkeypatch):
    mod = load_music_main()
    monkeypatch.delenv("ZOE_MUSIC_SERVICE_TOKEN", raising=False)
    c = TestClient(mod.app)
    # No token configured server-side -> tool calls are refused, not allowed.
    assert c.post("/tools/play", json={"query": "x"}).status_code == 503


# ── (P3) input bounds ──────────────────────────────────────────────────────

def test_empty_query_rejected(client):
    assert client.post("/tools/play", json={"query": ""}, headers=auth()).status_code == 422
    assert client.post("/tools/play", json={"query": "   "}, headers=auth()).status_code == 422


def test_oversized_query_rejected(client):
    big = "a" * 501
    assert client.post("/tools/play", json={"query": big}, headers=auth()).status_code == 422


def test_volume_out_of_range_rejected(client):
    assert client.post("/tools/volume", json={"level": 150}, headers=auth()).status_code == 422
    assert client.post("/tools/volume", json={"level": -5}, headers=auth()).status_code == 422


def test_volume_in_range_ok(client):
    assert client.post("/tools/volume", json={"level": 50}, headers=auth()).status_code == 200


def test_search_empty_query_rejected(client):
    assert client.get("/tools/search", params={"query": ""}, headers=auth()).status_code == 422


def test_search_limit_capped(client):
    assert client.get(
        "/tools/search", params={"query": "x", "limit": 999}, headers=auth()
    ).status_code == 422
