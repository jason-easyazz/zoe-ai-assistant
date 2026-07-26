import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import require_admin
from routers.system import router as system_router
from zoe_memory_router import MemoryBackend
from zoe_memory_router_runtime import (
    FEATURE_FLAG,
    PROMPT_PACKET_PREVIEW_FLAG,
    build_memory_route_trace,
    collect_memory_route_trace,
    compile_cached_memory_prompt_packet,
    memory_prompt_packet_preview_enabled,
    memory_router_runtime_enabled,
    memory_router_runtime_status,
    route_memory_for_runtime,
)
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType

pytestmark = pytest.mark.ci_safe


def test_memory_router_runtime_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)

    status = memory_router_runtime_status(include_samples=False)

    assert memory_router_runtime_enabled() is False
    assert status["enabled"] is False
    assert status["mode"] == "disabled"
    assert status["default_enabled"] is False
    assert status["chat_hot_path_enabled"] is False
    assert status["prompt_injection_enabled"] is False
    assert status["prompt_packet_preview_enabled"] is False
    assert status["prompt_packet_preview_flag"] == PROMPT_PACKET_PREVIEW_FLAG
    assert status["durable_writes_enabled"] is False


def test_memory_router_runtime_enabled_is_observe_only(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")

    decision = route_memory_for_runtime("What fix worked for this recurring failure?")

    assert decision["enabled"] is True
    assert decision["mode"] == "observe_only"
    assert decision["route"]["primary"] == MemoryBackend.HINDSIGHT.value
    assert decision["can_inject_prompt"] is False
    assert decision["can_write_memory"] is False
    assert "trace" not in decision


def test_cached_prompt_packet_preview_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(PROMPT_PACKET_PREVIEW_FLAG, raising=False)

    packet = compile_cached_memory_prompt_packet(
        "What fix worked for this recurring failure?",
        [{"content": "voice queue guard fixed it", "scope": "project", "evidence_refs": ["trace:1"]}],
    )

    assert memory_prompt_packet_preview_enabled() is False
    assert packet["enabled"] is False
    assert packet["mode"] == "disabled"
    assert packet["packet"] is None
    assert packet["can_inject_prompt"] is False
    assert packet["can_write_memory"] is False


def test_cached_prompt_packet_preview_builds_compact_cited_packet(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    packet = compile_cached_memory_prompt_packet(
        "What fix worked for this recurring failure?",
        [
            {
                "event_id": "mem-2",
                "content": "disputed old failure explanation",
                "scope": "project",
                "status": "disputed",
                "confidence": 0.9,
                "evidence_refs": ["trace:old"],
            },
            {
                "event_id": "mem-1",
                "content": "voice queue guard fixed duplicate weather responses",
                "scope": "project",
                "status": "active",
                "confidence": 0.8,
                "evidence_refs": ["trace:new", "pytest:test_voice"],
            },
        ],
        max_items=2,
        max_chars=260,
    )

    assert packet["enabled"] is True
    assert packet["mode"] == "preview_only"
    assert packet["can_inject_prompt"] is False
    assert packet["packet"]["route_primary"] == MemoryBackend.HINDSIGHT.value
    assert packet["packet"]["evidence_refs"] == ["trace:new", "pytest:test_voice", "trace:old"]
    assert packet["packet"]["lines"][0].startswith("[mem-1] status=active")
    assert "evidence=trace:new,pytest:test_voice" in packet["packet"]["lines"][0]
    assert packet["packet"]["statuses"] == {"active": 1, "disputed": 1}


def test_cached_prompt_packet_preview_rejects_uncited_and_cross_user_items(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    packet = compile_cached_memory_prompt_packet(
        "What does Jason prefer?",
        [
            {"content": "missing evidence", "scope": "personal", "user_id": "jason"},
            {"content": "wrong user", "scope": "personal", "user_id": "casey", "evidence_refs": ["chat:1"]},
            {"content": "right user", "scope": "personal", "user_id": "jason", "evidence_refs": ["chat:2"]},
        ],
        user_id="jason",
        scope="personal",
    )

    assert packet["accepted_count"] == 1
    assert packet["packet"]["evidence_refs"] == ["chat:2"]
    assert {item["reason"] for item in packet["rejected"]} == {"evidence_refs are required", "user_id mismatch"}


def test_cached_prompt_packet_preview_rejects_owned_shared_items_without_caller_user(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    packet = compile_cached_memory_prompt_packet(
        "What shared memory applies?",
        [{"content": "shared but owned", "scope": "shared", "user_id": "casey", "evidence_refs": ["chat:shared"]}],
        scope="project",
    )

    assert packet["accepted_count"] == 0
    assert packet["rejected"] == [
        {"index": "0", "reason": "user_id is required to include owned personal/shared memory"}
    ]


def test_cached_prompt_packet_preview_rejects_archived_and_scope_mismatch(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    packet = compile_cached_memory_prompt_packet(
        "Which memories apply?",
        [
            {"content": "archived memory", "scope": "project", "status": "archived", "evidence_refs": ["trace:old"]},
            {"content": "personal in project", "scope": "personal", "user_id": "jason", "evidence_refs": ["chat:personal"]},
            {"content": "project memory", "scope": "project", "evidence_refs": ["trace:project"]},
        ],
        user_id="jason",
        scope="project",
    )

    assert packet["accepted_count"] == 1
    assert packet["packet"]["evidence_refs"] == ["trace:project"]
    assert {item["reason"] for item in packet["rejected"]} == {"status 'archived' is suppressed", "scope mismatch"}


def test_cached_prompt_packet_disabled_path_reports_candidate_count(monkeypatch):
    monkeypatch.delenv(PROMPT_PACKET_PREVIEW_FLAG, raising=False)

    packet = compile_cached_memory_prompt_packet(
        "query",
        [
            {"content": "one", "scope": "project", "evidence_refs": ["trace:1"]},
            {"content": "two", "scope": "project", "evidence_refs": ["trace:2"]},
        ],
    )

    assert packet["candidate_count"] == 2
    assert packet["accepted_count"] == 0
    assert packet["packet"] is None


def test_cached_prompt_packet_preview_truncates_to_character_budget(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    packet = compile_cached_memory_prompt_packet(
        "Which governance layer gates memory?",
        [
            {
                "event_id": "long-memory",
                "content": "Multica gates memory writes. " * 20,
                "scope": "project",
                "evidence_refs": ["doc:adr"],
            }
        ],
        max_chars=120,
    )

    line = packet["packet"]["lines"][0]
    assert len(line) <= 120
    assert line.endswith("...")


def test_cached_prompt_packet_preview_does_not_exceed_budget_when_room_is_tiny(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    packet = compile_cached_memory_prompt_packet(
        "Which governance layer gates memory?",
        [
            {
                "event_id": "long-memory",
                "content": "x" * 20,
                "scope": "project",
                "evidence_refs": ["doc:adr"],
            }
        ],
        max_chars=64,
    )

    assert packet["packet"]["lines"] == []


def test_cached_prompt_packet_preview_validates_size_limits(monkeypatch):
    monkeypatch.setenv(PROMPT_PACKET_PREVIEW_FLAG, "true")

    with pytest.raises(ValueError, match="max_items must be positive"):
        compile_cached_memory_prompt_packet("query", [], max_items=0)
    with pytest.raises(ValueError, match="max_chars must be positive"):
        compile_cached_memory_prompt_packet("query", [], max_chars=0)


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
    assert decision["trace_collection"]["ok"] is True
    assert decision["trace_collection"]["accepted_count"] == 1
    assert decision["trace_collection"]["persisted"] is False
    assert decision["trace_collection"]["summary"]["types"] == {ObservationTraceType.MEMORY_ROUTE.value: 1}
    assert "traces" not in decision["trace_collection"]
    assert decision["can_inject_prompt"] is False
    assert decision["can_write_memory"] is False


def test_runtime_route_personal_trace_requires_user_id(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "true")

    with pytest.raises(ValueError, match="user_id is required"):
        route_memory_for_runtime(
            "What fix worked for this recurring failure?",
            include_trace=True,
            scope="personal",
        )


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
    assert decision["trace_collection"]["ok"] is True
    assert decision["trace_collection"]["persisted"] is False


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


def test_collect_memory_route_trace_rejects_non_memory_route_trace():
    trace = build_memory_route_trace(
        "What fix worked for this recurring failure?",
        purpose="chat",
        decision={
            "enabled": False,
            "mode": "disabled",
            "route": None,
            "can_inject_prompt": False,
            "can_write_memory": False,
            "reason": "runtime flag disabled",
        },
    )
    bad_trace = ObservationTrace(
        trace_id=trace.trace_id,
        trace_type=ObservationTraceType.RECALL.value,
        surface=trace.surface,
        scope=trace.scope,
        outcome=trace.outcome,
        summary=trace.summary,
        evidence_refs=trace.evidence_refs,
    )

    with pytest.raises(ValueError, match="trace_type 'recall' is not allowed"):
        collect_memory_route_trace(bad_trace)


def test_collect_memory_route_trace_rejects_non_memory_surface():
    trace = build_memory_route_trace(
        "What fix worked for this recurring failure?",
        purpose="chat",
        decision={
            "enabled": False,
            "mode": "disabled",
            "route": None,
            "can_inject_prompt": False,
            "can_write_memory": False,
            "reason": "runtime flag disabled",
        },
    )
    bad_trace = ObservationTrace(
        trace_id=trace.trace_id,
        trace_type=trace.trace_type,
        surface="chat",
        scope=trace.scope,
        outcome=trace.outcome,
        summary=trace.summary,
        evidence_refs=trace.evidence_refs,
    )

    with pytest.raises(ValueError, match="surface 'chat' is not allowed"):
        collect_memory_route_trace(bad_trace)


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
    assert decision["trace_collection"]["ok"] is True
    assert decision["trace_collection"]["persisted"] is False
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
    assert data["prompt_packet_preview_enabled"] is False
    assert data["prompt_packet_preview_flag"] == PROMPT_PACKET_PREVIEW_FLAG
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
