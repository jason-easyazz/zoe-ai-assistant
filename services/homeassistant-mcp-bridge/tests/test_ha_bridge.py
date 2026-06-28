import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).resolve().parents[1] / "main.py"


@pytest.fixture()
def bridge_module():
    spec = importlib.util.spec_from_file_location("ha_bridge_main", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, status_code, text="upstream error", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, status_code):
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse(self.status_code, text=f"HA returned {self.status_code}")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 404])
async def test_make_request_preserves_upstream_http_status(bridge_module, monkeypatch, status_code):
    monkeypatch.setattr(
        bridge_module.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(status_code),
    )
    bridge = bridge_module.HomeAssistantBridge("http://ha.local", "token")

    with pytest.raises(HTTPException) as exc_info:
        await bridge._make_request("GET", "states")

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == f"HA returned {status_code}"


def test_automation_upstream_http_error_reaches_client(bridge_module, monkeypatch):
    async def raise_unauthorized():
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(bridge_module.ha_bridge, "get_automations", raise_unauthorized)

    response = TestClient(bridge_module.app).get("/automations")

    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_automation_scene_and_script_endpoints_filter_states(bridge_module, monkeypatch):
    states = [
        {
            "entity_id": "automation.morning_lights",
            "state": "on",
            "attributes": {
                "friendly_name": "Morning Lights",
                "last_triggered": "2026-06-28T10:00:00+00:00",
            },
        },
        {
            "entity_id": "scene.movie_time",
            "state": "scening",
            "attributes": {"friendly_name": "Movie Time"},
        },
        {
            "entity_id": "script.goodnight",
            "state": "off",
            "attributes": {"friendly_name": "Goodnight"},
        },
        {
            "entity_id": "light.kitchen",
            "state": "on",
            "attributes": {"friendly_name": "Kitchen"},
        },
    ]

    async def fake_get_states():
        return states

    monkeypatch.setattr(bridge_module.ha_bridge, "get_states", fake_get_states)
    client = TestClient(bridge_module.app)

    assert client.get("/automations").json() == {
        "automations": [
            {
                "entity_id": "automation.morning_lights",
                "name": "Morning Lights",
                "state": "on",
                "last_triggered": "2026-06-28T10:00:00+00:00",
            }
        ],
        "count": 1,
    }
    assert client.get("/scenes").json() == {
        "scenes": [
            {
                "entity_id": "scene.movie_time",
                "name": "Movie Time",
                "state": "scening",
            }
        ],
        "count": 1,
    }
    assert client.get("/scripts").json() == {
        "scripts": [
            {
                "entity_id": "script.goodnight",
                "name": "Goodnight",
                "state": "off",
            }
        ],
        "count": 1,
    }


def test_analysis_uses_state_filtered_automation_scene_script_counts(bridge_module, monkeypatch):
    states = [
        {"entity_id": "automation.morning_lights", "state": "on", "attributes": {}},
        {"entity_id": "scene.movie_time", "state": "scening", "attributes": {}},
        {"entity_id": "script.goodnight", "state": "off", "attributes": {}},
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    ]

    async def fake_get_states():
        return states

    monkeypatch.setattr(bridge_module.ha_bridge, "get_states", fake_get_states)

    response = TestClient(bridge_module.app).get("/analysis")

    assert response.status_code == 200
    summary = response.json()["analysis"]["summary"]
    assert summary["total_entities"] == 4
    assert summary["total_automations"] == 1
    assert summary["total_scenes"] == 1
    assert summary["total_scripts"] == 1
