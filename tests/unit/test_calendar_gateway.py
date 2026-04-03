import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "services/zoe-core"))

from services.calendar_gateway import (  # type: ignore
    build_idempotency_key,
    is_calendar_sync_enabled,
    KeeperCalendarGateway,
)


class DummyResponse:
    def __init__(self, ok=True, status_code=200, payload=None, text=""):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_idempotency_key_is_stable():
    key1 = build_idempotency_key("u1", "create", "123", {"title": "A"})
    key2 = build_idempotency_key("u1", "create", "123", {"title": "A"})
    assert key1 == key2


def test_sync_flag_env(monkeypatch):
    monkeypatch.setenv("ZOE_CALENDAR_SYNC_ENABLED", "true")
    assert is_calendar_sync_enabled() is True
    monkeypatch.setenv("ZOE_CALENDAR_SYNC_ENABLED", "false")
    assert is_calendar_sync_enabled() is False


def test_keeper_create_success(monkeypatch):
    monkeypatch.setenv("KEEPER_BASE_URL", "http://keeper.test")
    monkeypatch.setenv("KEEPER_AUTH_TOKEN", "token-1")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return DummyResponse(ok=True, status_code=201, payload={"provider_event_id": "p-1", "etag": "e-1"})

    monkeypatch.setattr("services.calendar_gateway.requests.post", fake_post)
    gateway = KeeperCalendarGateway()
    result = gateway.create_event("user-1", {"title": "Meeting"}, "idem-1")

    assert result.success is True
    assert result.provider_event_id == "p-1"
    assert captured["url"].endswith("/api/sync/events/create")
    assert captured["headers"]["X-Idempotency-Key"] == "idem-1"
    assert captured["headers"]["Authorization"] == "Bearer token-1"


def test_keeper_update_error(monkeypatch):
    monkeypatch.setenv("KEEPER_BASE_URL", "http://keeper.test")

    def fake_post(url, headers=None, json=None, timeout=None):
        return DummyResponse(ok=False, status_code=400, payload={"detail": "bad request"})

    monkeypatch.setattr("services.calendar_gateway.requests.post", fake_post)
    gateway = KeeperCalendarGateway()
    result = gateway.update_event("user-1", "event-1", {"title": "Changed"}, "idem-2", provider_event_id="prov-1")

    assert result.success is False
    assert result.status_code == 400
    assert result.error == "bad request"
