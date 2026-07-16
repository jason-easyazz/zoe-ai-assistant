import json

import httpx
import pytest

from hindsight_memory import HindsightConfig, HindsightMemoryClient
from hindsight_retain_candidates import (
    build_admitted_hindsight_retain_plan,
    build_hindsight_retain_admission_request,
    evaluate_hindsight_retain_candidate_admission,
)
from hindsight_retain_executor import execute_admitted_hindsight_retain_plan
from zoe_memory_contract import MemoryEvent, MemoryEventType, MemoryScope, MemorySource
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType

pytestmark = pytest.mark.ci_safe


def _event():
    return MemoryEvent(
        event_id="mem_evt_admitted_retain",
        user_id="jason",
        scope=MemoryScope.PERSONAL.value,
        source=MemorySource.CHAT.value,
        event_type=MemoryEventType.PREFERENCE.value,
        content="Jason prefers Zoe memory to remain offline-only.",
        entities=("zoe_memory",),
        evidence_refs=("chat:offline-memory",),
        confidence=0.91,
    )


def _admission_trace():
    return ObservationTrace(
        trace_id="trace_hindsight_retain_executor",
        trace_type=ObservationTraceType.ADMISSION.value,
        surface="multica",
        scope=MemoryScope.PERSONAL.value,
        user_id="jason",
        outcome=ObservationOutcome.SUCCESS.value,
        summary="Hindsight retain plan approved for sidecar execution.",
        evidence_refs=("multica:retain-review",),
    )


def _plan(config=None):
    request = build_hindsight_retain_admission_request(
        _event(),
        observation_traces=(_admission_trace(),),
        approval_refs=("approval:multica:retain-review",),
    )
    decision = evaluate_hindsight_retain_candidate_admission(
        _event(),
        observation_traces=(_admission_trace(),),
        approval_refs=("approval:multica:retain-review",),
    )
    return build_admitted_hindsight_retain_plan(request, decision, config=config)


@pytest.mark.asyncio
async def test_execute_admitted_hindsight_retain_plan_refuses_when_disabled():
    plan = _plan(config=HindsightConfig(enabled=False))

    result = await execute_admitted_hindsight_retain_plan(
        plan,
        config=HindsightConfig(enabled=False),
    )

    assert result.attempted is False
    assert result.retained is False
    assert result.reason == "disabled"
    assert result.admission_id == "admit_hindsight_retain_mem_evt_admitted_retain"
    assert result.sidecar_result["event_id"] == "mem_evt_admitted_retain"


@pytest.mark.asyncio
async def test_execute_admitted_hindsight_retain_plan_posts_exact_plan_payload():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"success": True, "items_count": 1})

    transport = httpx.MockTransport(handler)
    config = HindsightConfig(enabled=True, bank_prefix="zoe-test", async_retain=False)
    plan = _plan(config=config)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await execute_admitted_hindsight_retain_plan(plan, client=client)

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/default/banks/zoe-test-personal-jason/memories"
    assert seen["payload"] == plan.to_dict()["payload"]
    assert result.attempted is True
    assert result.retained is True
    assert result.reason == "retained"
    assert "approval:multica:retain-review" in result.evidence_refs


@pytest.mark.asyncio
async def test_execute_admitted_hindsight_retain_plan_reports_sidecar_rejection():
    async def handler(request):
        return httpx.Response(200, json={"success": False, "error": "duplicate document_id"})

    transport = httpx.MockTransport(handler)
    config = HindsightConfig(enabled=True)
    plan = _plan(config=config)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await execute_admitted_hindsight_retain_plan(plan, client=client)

    assert result.attempted is True
    assert result.retained is False
    assert result.reason == "sidecar_rejected"
    assert result.sidecar_result["error"] == "duplicate document_id"


@pytest.mark.asyncio
async def test_execute_admitted_hindsight_retain_plan_returns_structured_sidecar_error():
    async def handler(request):
        return httpx.Response(500, json={"error": "sidecar unavailable"})

    transport = httpx.MockTransport(handler)
    config = HindsightConfig(enabled=True)
    plan = _plan(config=config)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await execute_admitted_hindsight_retain_plan(plan, client=client)

    assert result.attempted is True
    assert result.retained is False
    assert result.reason == "sidecar_error"
    assert result.sidecar_result["bank_id"] == "zoe-personal-jason"
    assert result.sidecar_result["event_id"] == "mem_evt_admitted_retain"
    assert "Hindsight request failed" in result.sidecar_result["error"]
