"""GET /api/system/stats - the desktop dashboard's System card (Wave 4b, 2026-10-19).

The card it replaces drew Math.random() numbers. These pin: the route answers a signed-in MEMBER
only (guest, the wall panel, a member-bound device token, an auth-outage principal and the
unauthenticated-admin promotion all get 403 - negative controls run the REAL gate, nothing is
overridden but the principal); the numbers are psutil's own; a service that does not answer is
"down", a sick one "degraded", never "ok"; and one snapshot is shared across callers.
"""
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import system_stats  # noqa: E402
from auth import get_current_user  # noqa: E402
from routers import system_stats as route  # noqa: E402

pytestmark = pytest.mark.ci_safe

MEMBER = {"user_id": "jason", "role": "admin", "username": "jason"}
PRINCIPALS_REFUSED = {
    "anonymous guest": {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []},
    "panel (device token)": {"user_id": "guest", "role": "guest", "panel_id": "panel-1", "device_token_role": "panel"},
    "panel bound to a member (rollback)": {"user_id": "jason", "role": "admin", "panel_id": "panel-1", "device_token_role": "member"},
    "auth outage": {"user_id": "jason", "role": "admin", "auth_degraded": True},
    "unauthenticated admin promotion": {"user_id": "jason", "role": "admin", "unauthenticated_override": True},
}

FAKE_BODY = {
    "cpu": {"percent": 12.5, "cores": 6, "load_1m": 0.8},
    "memory": {"total_bytes": 100, "used_bytes": 40, "available_bytes": 60, "percent": 40.0},
    "disk": {"path": "/", "total_bytes": 1000, "used_bytes": 250, "free_bytes": 750, "percent": 25.0},
    "uptime_seconds": 3600,
    "services": [{"id": "database", "name": "Database", "status": "ok"}],
    "generated_at": "2026-10-19T00:00:00+00:00",
}


def _client(monkeypatch, principal):
    async def fake_snapshot(**_kw):
        return FAKE_BODY

    monkeypatch.setattr(system_stats, "snapshot", fake_snapshot)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: principal
    return TestClient(app)


def test_member_gets_the_snapshot(monkeypatch):
    resp = _client(monkeypatch, MEMBER).get("/api/system/stats")
    assert resp.status_code == 200
    assert resp.json() == FAKE_BODY


@pytest.mark.parametrize("who", sorted(PRINCIPALS_REFUSED))
def test_everyone_but_a_member_is_refused(monkeypatch, who):
    resp = _client(monkeypatch, PRINCIPALS_REFUSED[who]).get("/api/system/stats")
    assert resp.status_code == 403, f"{who} must not read system stats"
    assert "cpu" not in resp.text


def test_route_declares_require_member():
    from panel_principal import require_member

    (r,) = [r for r in route.router.routes if getattr(r, "path", "") == "/api/system/stats"]
    assert r.methods == {"GET"}
    assert require_member in [d.call for d in r.dependant.dependencies]


def test_resource_snapshot_is_psutil_not_invented():
    psutil = pytest.importorskip("psutil")
    snap = system_stats.resource_snapshot("/")
    vm = psutil.virtual_memory()
    assert snap["memory"]["total_bytes"] == vm.total
    assert 0 <= snap["memory"]["percent"] <= 100
    assert snap["disk"]["total_bytes"] == psutil.disk_usage("/").total
    assert 0 <= snap["cpu"]["percent"] <= 100
    assert snap["cpu"]["cores"] == psutil.cpu_count(logical=True)
    assert snap["uptime_seconds"] > 0


def test_resource_snapshot_without_psutil_is_unknown_not_zero(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)  # import psutil -> ImportError
    assert system_stats.resource_snapshot("/") == {
        "cpu": None, "memory": None, "disk": None, "uptime_seconds": None,
    }


def _resp(status, body):
    return httpx.Response(status, json=body)


def test_service_judges_never_call_a_sick_service_ok():
    assert system_stats._ok_if_200(_resp(200, {"status": "ok"})) == "ok"
    assert system_stats._ok_if_200(_resp(503, {})) == "degraded"
    assert system_stats._judge_voice(_resp(200, {"pipeline_loaded": True})) == "ok"
    assert system_stats._judge_voice(_resp(200, {"pipeline_loaded": True, "degraded": True})) == "degraded"
    assert system_stats._judge_voice(_resp(200, {"pipeline_loaded": False})) == "degraded"
    assert system_stats._judge_home_assistant(_resp(200, {"status": "healthy", "ha_connected": True})) == "ok"
    assert system_stats._judge_home_assistant(_resp(200, {"status": "unhealthy", "ha_connected": False})) == "degraded"


@pytest.mark.asyncio
async def test_unreachable_service_is_down():
    # Port 9 (discard) on loopback: nothing listens, the connect is refused at once.
    assert await system_stats._http_status("http://127.0.0.1:9/health", system_stats._ok_if_200) == "down"


@pytest.mark.asyncio
async def test_probe_services_survives_a_raising_probe(monkeypatch):
    async def ok():
        return "ok"

    async def boom():
        raise RuntimeError("probe crashed")

    monkeypatch.setattr(system_stats, "_service_probes", lambda: [("a", "A", ok), ("b", "B", boom)])
    assert await system_stats.probe_services() == [
        {"id": "a", "name": "A", "status": "ok"},
        {"id": "b", "name": "B", "status": "down"},
    ]


@pytest.mark.asyncio
async def test_snapshot_is_shared_for_the_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_resources(*_a):
        calls["n"] += 1
        return {"cpu": None, "memory": None, "disk": None, "uptime_seconds": calls["n"]}

    async def fake_services():
        return []

    monkeypatch.setattr(system_stats, "resource_snapshot", fake_resources)
    monkeypatch.setattr(system_stats, "probe_services", fake_services)
    monkeypatch.setattr(system_stats, "_cache", {"at": 0.0, "body": None})

    first = await system_stats.snapshot()
    second = await system_stats.snapshot()
    assert calls["n"] == 1 and first is second
    fresh = await system_stats.snapshot(use_cache=False)
    assert calls["n"] == 2 and fresh["uptime_seconds"] == 2
