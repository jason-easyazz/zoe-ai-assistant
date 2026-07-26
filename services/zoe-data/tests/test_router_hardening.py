from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routers import ha_control, portrait, weather

pytestmark = pytest.mark.ci_safe


def _ha_client() -> TestClient:
    app = FastAPI()
    app.include_router(ha_control.router)
    app.dependency_overrides[ha_control.get_current_user] = lambda: {
        "user_id": "u-1",
        "role": "admin",
    }
    return TestClient(app)


def _portrait_client() -> TestClient:
    app = FastAPI()
    app.include_router(portrait.router)

    async def fake_get_db():
        yield object()

    app.dependency_overrides[portrait.get_current_user] = lambda: {
        "user_id": "u-1",
        "role": "admin",
    }
    app.dependency_overrides[portrait.get_db] = fake_get_db
    return TestClient(app)


def test_ha_control_exception_returns_generic_message_and_logs(monkeypatch, caplog):
    async def fail_bridge_post(path, body):
        raise RuntimeError("secret stack detail")

    monkeypatch.setattr(ha_control, "_bridge_post", fail_bridge_post)

    with caplog.at_level("ERROR", logger=ha_control.logger.name):
        response = _ha_client().post(
            "/api/ha/control",
            json={"entity_id": "light.kitchen", "action": "toggle"},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "HA bridge request failed"}
    assert "secret stack detail" not in response.text
    assert any("ha/control error" in record.message for record in caplog.records)
    assert any(record.exc_info for record in caplog.records)


def test_ha_control_valid_request_success_payload_unchanged(monkeypatch):
    captured = {}

    async def fake_bridge_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"changed": True}

    monkeypatch.setattr(ha_control, "_bridge_post", fake_bridge_post)

    response = _ha_client().post(
        "/api/ha/control",
        json={
            "entity_id": "light.kitchen",
            "action": "turn_on",
            "params": {"brightness_pct": 80},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "entity_id": "light.kitchen",
        "service": "turn_on",
        "result": {"changed": True},
    }
    assert captured == {
        "path": "/devices/control",
        "body": {
            "entity_id": "light.kitchen",
            "action": "turn_on",
            "data": {"brightness_pct": 80},
        },
    }


def test_ha_control_rejects_absurd_input_length():
    response = _ha_client().post(
        "/api/ha/control",
        json={"entity_id": "light." + ("x" * 300), "action": "toggle"},
    )

    assert response.status_code == 422


def test_portrait_emotional_moments_limit_cap_rejects_absurd_value():
    response = _portrait_client().get("/api/portrait/me/emotional-moments", params={"limit": 10000})

    assert response.status_code == 422


def test_portrait_emotional_moments_valid_limit_success_payload_unchanged(monkeypatch):
    class Ref:
        def __init__(self, index):
            self.id = f"mem-{index}"
            self.text = f"moment {index}"
            self.added_at = f"2026-06-2{index}"
            self.tags = ["emotional"]
            self.memory_type = "emotional_moment"

    class FakeMemoryService:
        async def load_for_prompt(self, user_id, limit):
            assert user_id == "u-1"
            assert limit == 200
            return [Ref(1), Ref(2), Ref(3)]

    module = types.SimpleNamespace(get_memory_service=lambda: FakeMemoryService())
    monkeypatch.setitem(sys.modules, "memory_service", module)

    response = _portrait_client().get("/api/portrait/me/emotional-moments", params={"limit": 2})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "u-1",
        "emotional_moments": [
            {"id": "mem-1", "text": "moment 1", "added_at": "2026-06-21", "tags": ["emotional"]},
            {"id": "mem-2", "text": "moment 2", "added_at": "2026-06-22", "tags": ["emotional"]},
        ],
        "count": 2,
    }


def test_portrait_regenerate_exception_returns_generic_message_and_logs(monkeypatch, caplog):
    async def fail_synthesis(user_id, db=None):
        raise RuntimeError("portrait backend secret")

    module = types.SimpleNamespace(run_portrait_synthesis=fail_synthesis)
    monkeypatch.setitem(sys.modules, "user_portrait", module)

    with caplog.at_level("ERROR", logger=portrait.logger.name):
        response = _portrait_client().post("/api/portrait/me/regenerate")

    assert response.status_code == 500
    assert response.json() == {"detail": "Portrait generation failed"}
    assert "portrait backend secret" not in response.text
    assert any("portrait regenerate failed" in record.message for record in caplog.records)
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_weather_provider_fallback_returns_generic_error_and_logs(monkeypatch, caplog):
    # Private dict via monkeypatch (auto-restored) — never mutate the module's
    # real cache in place, it leaks entries into other tests' keyed lookups.
    monkeypatch.setattr(weather, "_weather_cache", {})

    class FakeResponse:
        def raise_for_status(self):
            raise RuntimeError("weather provider secret")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(weather.httpx, "AsyncClient", FakeClient)

    with caplog.at_level("ERROR", logger=weather.logger.name):
        result = await weather._fetch_openmeteo_current(1.0, 2.0, "Perth", "AU")

    assert result == {"cached": False, "error": "Weather provider unavailable"}
    assert "weather provider secret" not in str(result)
    assert any("open-meteo current weather fetch failed" in record.message for record in caplog.records)
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_weather_cached_fallback_success_shape_unchanged(monkeypatch):
    cached = {"temp": 23, "city": "Perth", "country": "AU"}
    monkeypatch.setattr(weather, "_weather_cache", {})
    # Keyed cache: the provider-down fallback only serves a stale reading for
    # the SAME coords — seed it where the fetch below will look.
    weather._cache_put("current", 1.0, 2.0, cached)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise RuntimeError("provider down")

    monkeypatch.setattr(weather.httpx, "AsyncClient", FakeClient)

    result = await weather._fetch_openmeteo_current(1.0, 2.0, "Perth", "AU")

    assert result is cached
