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
    assert exc_info.value.detail == "Home Assistant request failed"


def test_automation_upstream_http_error_reaches_client(bridge_module, monkeypatch):
    async def raise_unauthorized():
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(bridge_module.ha_bridge, "get_automations", raise_unauthorized)

    response = TestClient(bridge_module.app).get("/automations")

    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_lights_upstream_http_error_reaches_client(bridge_module, monkeypatch):
    async def raise_missing():
        raise HTTPException(status_code=404, detail="missing states")

    monkeypatch.setattr(bridge_module.ha_bridge, "get_states", raise_missing)

    response = TestClient(bridge_module.app).get("/lights")

    assert response.status_code == 404
    assert response.json()["detail"] == "missing states"


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

    calls = 0

    async def fake_get_states():
        nonlocal calls
        calls += 1
        return states

    monkeypatch.setattr(bridge_module.ha_bridge, "get_states", fake_get_states)

    response = TestClient(bridge_module.app).get("/analysis")

    assert response.status_code == 200
    summary = response.json()["analysis"]["summary"]
    assert summary["total_entities"] == 4
    assert summary["total_automations"] == 1
    assert summary["total_scenes"] == 1
    assert summary["total_scripts"] == 1
    assert calls == 1


# -- 2026-10-04 container log review: quiet polls, loud failures ---------------------------


def _access_record(bridge_module, method, path, status):
    import logging

    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("172.18.0.1:1234", method, path, "1.1", status), None,
    )


@pytest.mark.parametrize(
    "method,path,status,kept",
    [
        ("GET", "/", 200, False),  # Docker healthcheck, every 30 s
        ("GET", "/entities", 200, False),  # zoe-data entity poll
        ("GET", "/entities?domain=light", 200, False),  # query string is not part of the path
        ("GET", "/", 503, True),  # a failing health probe is signal
        ("GET", "/entities", 500, True),
        ("POST", "/devices/control", 200, True),  # a user action is the audit trail
        ("GET", "/entities/light.x", 200, True),  # only the exact poll paths are quiet
        ("POST", "/", 200, True),
    ],
)
def test_access_log_drops_only_successful_polls(bridge_module, method, path, status, kept):
    flt = bridge_module.QuietPollAccessFilter()
    assert flt.filter(_access_record(bridge_module, method, path, status)) is kept


def test_access_filter_passes_records_it_cannot_parse(bridge_module):
    import logging

    flt = bridge_module.QuietPollAccessFilter()
    odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "plain text", None, None)
    assert flt.filter(odd) is True


def test_filter_is_installed_on_the_uvicorn_access_logger(bridge_module):
    import logging

    installed = logging.getLogger("uvicorn.access").filters
    assert any(type(f).__name__ == "QuietPollAccessFilter" for f in installed)


class _RaisingClient:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        raise self.exc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory,expected",
    [
        (lambda m: _FakeAsyncClient(401), "401"),
        (lambda m: _RaisingClient(m.httpx.TimeoutException("slow")), "408"),
        (lambda m: _RaisingClient(m.httpx.ConnectError("down")), "503"),
        (lambda m: _RaisingClient(RuntimeError("boom")), "500"),
    ],
)
async def test_every_ha_failure_is_logged_not_swallowed(bridge_module, monkeypatch, caplog, factory, expected):
    # The /entities handler turns these HTTPExceptions into HTTP 200 bodies, so before this the
    # only trace of an expired token or a dead HA was a 200 in the access log.
    import logging

    monkeypatch.setattr(bridge_module.httpx, "AsyncClient", lambda: factory(bridge_module))
    bridge = bridge_module.HomeAssistantBridge("http://ha.local", "token")
    with caplog.at_level(logging.WARNING, logger="zoe.ha_bridge"):
        with pytest.raises(HTTPException):
            await bridge._make_request("GET", "states")
    msgs = [r.getMessage() for r in caplog.records if r.name == "zoe.ha_bridge"]
    assert len(msgs) == 1 and f"-> {expected}" in msgs[0], msgs
    assert "token" not in msgs[0].lower().replace("states", ""), "never log credentials"


@pytest.mark.asyncio
async def test_repeated_failures_are_rate_limited_per_interval(bridge_module, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(bridge_module.httpx, "AsyncClient", lambda: _FakeAsyncClient(503))
    clock = {"t": 1000.0}
    monkeypatch.setattr(bridge_module.time, "monotonic", lambda: clock["t"])
    bridge = bridge_module.HomeAssistantBridge("http://ha.local", "token")

    async def fail():
        with pytest.raises(HTTPException):
            await bridge._make_request("GET", "states")

    with caplog.at_level(logging.WARNING, logger="zoe.ha_bridge"):
        for _ in range(6):  # an HA outage polled every 10 s for a minute
            await fail()
            clock["t"] += 10
        assert len([r for r in caplog.records if r.name == "zoe.ha_bridge"]) == 1
        clock["t"] += bridge_module._FAILURE_LOG_INTERVAL_S
        await fail()
        assert len([r for r in caplog.records if r.name == "zoe.ha_bridge"]) == 2
