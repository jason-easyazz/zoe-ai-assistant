import json

import httpx
import pytest

from hindsight_memory import HindsightConfig, HindsightMemoryClient, event_to_hindsight_item
from samantha_memory_contract import MemoryEvent, MemoryEventType, MemoryScope, MemorySource


def _event():
    return MemoryEvent(
        event_id="mem_evt_test",
        user_id="Jason B",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TEST.value,
        event_type=MemoryEventType.EXPERIENCE.value,
        content="Zoe learned that Hindsight recall should be measured before live chat integration.",
        entities=("Hindsight", "live chat"),
        evidence_refs=("pytest:test_hindsight_memory",),
    )


def test_config_defaults_are_safe():
    config = HindsightConfig.from_env({})

    assert config.enabled is False
    assert config.auto_retain is False
    assert config.async_retain is True
    assert config.bank_id("Jason B", "project") == "zoe-project-jason-b"


def test_config_reads_env_without_enabling_auto_retain_by_accident():
    config = HindsightConfig.from_env(
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://hindsight.local:8888/",
            "HINDSIGHT_BANK_PREFIX": "Zoe Lab",
            "HINDSIGHT_TIMEOUT_SECONDS": "2.5",
        }
    )

    assert config.enabled is True
    assert config.base_url == "http://hindsight.local:8888"
    assert config.bank_prefix == "zoe-lab"
    assert config.timeout_seconds == 2.5
    assert config.auto_retain is False


def test_event_to_hindsight_item_keeps_evidence_and_scope_tags():
    item = event_to_hindsight_item(_event())

    assert item["document_id"] == "mem_evt_test"
    assert "user:jason-b" in item["tags"]
    assert "scope:project" in item["tags"]
    assert "evidence:pytest-test_hindsight_memory" in item["tags"]
    assert "evidence_refs" in item["context"]


@pytest.mark.asyncio
async def test_retain_refuses_write_when_disabled():
    client = HindsightMemoryClient(HindsightConfig(enabled=False))

    result = await client.retain_event(_event(), allow_auto=True)

    assert result == {"enabled": False, "retained": False, "reason": "disabled", "event_id": "mem_evt_test"}


@pytest.mark.asyncio
async def test_retain_refuses_auto_write_when_gate_closed():
    client = HindsightMemoryClient(HindsightConfig(enabled=True, auto_retain=False))

    result = await client.retain_event(_event())

    assert result["retained"] is False
    assert result["reason"] == "auto_retain_disabled"


@pytest.mark.asyncio
async def test_retain_posts_to_hindsight_memories_endpoint():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"success": True, "items_count": 1, "async": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True, auto_retain=False), client=http_client)
        result = await client.retain_event(_event(), allow_auto=True)

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/default/banks/zoe-project-jason-b/memories"
    assert seen["payload"]["async"] is True
    assert seen["payload"]["items"][0]["document_id"] == "mem_evt_test"
    assert result["success"] is True
    assert result["event_id"] == "mem_evt_test"


@pytest.mark.asyncio
async def test_recall_posts_trace_enabled_request():
    seen = {}

    async def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"results": [{"text": "voice queue guard fixed weather"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True), client=http_client)
        result = await client.recall(user_id="Jason B", scope="project", query="What fixed weather?")

    assert seen["path"] == "/v1/default/banks/zoe-project-jason-b/memories/recall"
    assert seen["payload"]["trace"] is True
    assert seen["payload"]["types"] == ["world", "experience"]
    assert result["results"][0]["text"] == "voice queue guard fixed weather"
