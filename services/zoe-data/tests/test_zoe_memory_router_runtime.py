import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import require_admin
from routers.system import router as system_router
from zoe_memory_router import MemoryBackend
from zoe_memory_router_runtime import (
    FEATURE_FLAG,
    build_memory_route_trace,
    memory_router_runtime_enabled,
    memory_router_runtime_status,
    route_memory_for_runtime,
)
from zoe_observation_trace import ObservationOutcome, ObservationTraceType


def test_memory_router_runtime_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)

    status = memory_router_runtime_status(include_samples=False)

    assert memory_router_runtime_enabled() is False
    assert status["enabled"] is False
    assert status["mode"] == "disabled"
    assert status["default_enabled"] is False
    assert status["chat_hot_path_enabled"] is False
    assert status["prompt_injection_enabled"] is False
    assert status["durable_writes_enabled"] is False


def test_memory_router_runtime_enabled_is_observe_only(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")

    decision = route_memory_for_runtime("What fix worked for this recurring failure?")

    assert decision["enabled"] is True
    assert decision["mode"] == "observe_only"
    assert decision["route"]["primary"] == MemoryBackend.HINDSIGHT.value
    assert decision["can_inject_prompt"] is False
    assert decision["can_write_memory"] is False


def test_runtime_route_can_include_observation_trace(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")

    decision = route_memory_for_runtime(
        "What fix worked for this recurring failure?",
        include_trace=True,
        trace_id="trace_test_memory_route",
    )

    trace = decision["trace"]
    assert trace["trace_id"] == "trace_test_memory_route"
    assert trace["trace_type"] == ObservationTraceType.MEMORY_ROUTE.value
    assert trace["surface"] == "memory"
    assert trace["outcome"] == ObservationOutcome.SUCCESS.value
    assert trace["metadata"]["primary"] == MemoryBackend.HINDSIGHT.value
    assert trace["metadata"]["query_length"] == len("What fix worked for this recurring failure?")
    assert "query" not in trace["metadata"]
    assert decision["can_inject_prompt"] is False
    assert decision["can_write_memory"] is False


def test_disabled_runtime_trace_is_skipped_and_does_not_route(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)

    decision = route_memory_for_runtime(
        "Create an upgrade proposal for a new capability.",
        include_trace=True,
        trace_id="trace_disabled_memory_route",
    )

    assert decision["route"] is None
    assert decision["trace"]["outcome"] == ObservationOutcome.SKIPPED.value
    assert decision["trace"]["metadata"]["primary"] is None
    assert decision["trace"]["metadata"]["enabled"] is False


def test_build_memory_route_trace_requires_user_for_personal_scope(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")
    decision = route_memory_for_runtime("What fix worked for this recurring failure?")

    trace = build_memory_route_trace(
        "What fix worked for this recurring failure?",
        purpose="chat",
        decision=decision,
        scope="personal",
    )

    with pytest.raises(ValueError, match="user_id is required"):
        trace.validate()


def test_status_can_include_sample_route_decisions(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")

    status = memory_router_runtime_status(
        samples=(("self_evolution", "Create an upgrade proposal for a new capability."),)
    )

    assert status["sample_routes"][0]["id"] == "self_evolution"
    assert status["sample_routes"][0]["decision"]["route"]["primary"] == MemoryBackend.MULTICA.value
    assert status["sample_routes"][0]["decision"]["can_write_memory"] is False


def test_status_can_include_sample_route_traces(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")

    status = memory_router_runtime_status(
        include_traces=True,
        samples=(("relational", "Which approval superseded the old tool trust?"),),
    )

    decision = status["sample_routes"][0]["decision"]
    assert decision["trace"]["trace_id"] == "trace_memory_route_sample_relational"
    assert decision["trace"]["metadata"]["primary"] == MemoryBackend.GRAPHITI.value
    assert "query" not in decision["trace"]["metadata"]


def test_disabled_runtime_does_not_compute_route_decision(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)

    decision = route_memory_for_runtime("Create an upgrade proposal for a new capability.")
    status = memory_router_runtime_status()

    assert decision["route"] is None
    assert status["sample_routes"] == []


def test_system_memory_router_status_endpoint_is_admin_scoped(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    app = FastAPI()
    app.include_router(system_router)

    async def fake_admin():
        return {"user_id": "admin", "role": "family-admin"}

    app.dependency_overrides[require_admin] = fake_admin

    resp = TestClient(app).get("/api/system/memory-router/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["surface"] == "zoe_memory_router"
    assert data["enabled"] is False
    assert data["prompt_injection_enabled"] is False
    assert data["durable_writes_enabled"] is False
    assert data["sample_routes"] == []


def test_system_memory_router_status_endpoint_rejects_non_admin(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/memory-router/status")

    assert resp.status_code == 403
