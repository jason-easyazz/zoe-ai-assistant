import json

import httpx
import pytest

from hindsight_memory import (
    HindsightConfig,
    HindsightMemoryClient,
    HindsightMemoryError,
    HindsightOfflineConfigError,
    event_to_hindsight_item,
)
from zoe_memory_contract import MemoryEvent, MemoryEventType, MemoryScope, MemorySource

pytestmark = pytest.mark.ci_safe


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


def test_config_defaults_are_safe_and_offline_only():
    config = HindsightConfig.from_env({})

    assert config.enabled is False
    assert config.auto_retain is False
    assert config.async_retain is True
    assert config.offline_only is True
    assert config.embeddings_provider == "local"
    assert config.embeddings_model == "BAAI/bge-small-en-v1.5"
    assert config.bank_id("Jason B", "project") == "zoe-project-jason-b"


def test_config_reads_local_env_without_enabling_auto_retain_by_accident():
    config = HindsightConfig.from_env(
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://hindsight.local:8888/",
            "HINDSIGHT_BANK_PREFIX": "Zoe Lab",
            "HINDSIGHT_TIMEOUT_SECONDS": "2.5",
            "HINDSIGHT_API_LLM_PROVIDER": "openai",
            "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL": "local-embedding",
        }
    )

    assert config.enabled is True
    assert config.base_url == "http://hindsight.local:8888"
    assert config.bank_prefix == "zoe-lab"
    assert config.timeout_seconds == 2.5
    assert config.auto_retain is False
    assert config.llm_provider == "openai"
    assert config.llm_base_url == "http://127.0.0.1:11434/v1"
    assert config.embeddings_provider == "openai"
    assert config.embeddings_base_url == "http://127.0.0.1:11434/v1"
    assert config.embeddings_model == "local-embedding"


def test_config_rejects_cloud_provider_when_enabled_offline_only():
    with pytest.raises(HindsightOfflineConfigError, match="not allowed"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "openai",
            }
        )


def test_config_rejects_public_sidecar_url_when_enabled_offline_only():
    with pytest.raises(HindsightOfflineConfigError, match="BASE_URL"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "https://api.example.com",
                "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            }
        )


def test_config_rejects_unknown_provider_without_local_base_url():
    with pytest.raises(HindsightOfflineConfigError, match="unrecognized"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "together",
            }
        )


def test_config_rejects_cloud_embeddings_provider_when_enabled_offline_only():
    with pytest.raises(HindsightOfflineConfigError, match="embeddings provider"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
                "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
            }
        )


def test_config_rejects_tei_embeddings_without_local_url():
    with pytest.raises(HindsightOfflineConfigError, match="TEI embeddings"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
                "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            }
        )


def test_config_allows_tei_embeddings_with_local_url():
    config = HindsightConfig.from_env(
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "http://127.0.0.1:8080",
        }
    )

    assert config.embeddings_provider == "tei"
    assert config.embeddings_base_url == "http://127.0.0.1:8080"


def test_config_rejects_tei_embeddings_when_only_openai_base_url_is_present():
    with pytest.raises(HindsightOfflineConfigError, match="TEI embeddings"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
                "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
                "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            }
        )


def test_config_rejects_tei_embeddings_with_cloud_tei_url_even_when_openai_base_url_is_local():
    with pytest.raises(HindsightOfflineConfigError, match="TEI embeddings"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
                "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
                "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "https://tei.example.com",
                "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            }
        )


def test_config_rejects_unknown_embeddings_provider_without_local_base_url():
    with pytest.raises(HindsightOfflineConfigError, match="embeddings provider"):
        HindsightConfig.from_env(
            {
                "HINDSIGHT_ENABLED": "true",
                "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
                "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
                "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "mystery",
            }
        )


def test_config_allows_unknown_embeddings_provider_with_local_base_url():
    config = HindsightConfig.from_env(
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "custom",
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
        }
    )

    assert config.embeddings_provider == "custom"


def test_config_rejects_malformed_boolean_env():
    with pytest.raises(ValueError, match="Unrecognized boolean"):
        HindsightConfig.from_env({"HINDSIGHT_ENABLED": "enabled"})


def test_config_allows_unknown_provider_with_local_base_url():
    config = HindsightConfig.from_env(
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "vllm",
            "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
        }
    )

    assert config.llm_provider == "vllm"


def test_config_allows_hindsight_builtin_llamacpp_provider():
    config = HindsightConfig.from_env(
        {
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
        }
    )

    assert config.enabled is True
    assert config.llm_provider == "llamacpp"


def test_event_to_hindsight_item_keeps_evidence_and_scope_tags():
    item = event_to_hindsight_item(_event())

    assert item["document_id"] == "mem_evt_test"
    assert "user:jason-b" in item["tags"]
    assert "scope:project" in item["tags"]
    assert "evidence:pytest-test_hindsight_memory" in item["tags"]
    assert "evidence_refs" in item["context"]


def test_enabled_status_includes_embedding_config():
    client = HindsightMemoryClient(
        HindsightConfig(
            enabled=True,
            embeddings_provider="tei",
            embeddings_base_url="http://127.0.0.1:8080",
            embeddings_model="local-embedding",
        )
    )

    status = client.enabled_status()

    assert status["embeddings_provider"] == "tei"
    assert status["embeddings_base_url"] == "http://127.0.0.1:8080"
    assert status["embeddings_model"] == "local-embedding"


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
async def test_retain_payload_posts_pre_admitted_payload_without_auto_retain():
    seen = {}
    payload = {"async": False, "items": (event_to_hindsight_item(_event()),)}

    async def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"operation_id": "op_123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True, auto_retain=False), client=http_client)
        result = await client.retain_payload(
            bank_id="zoe-project-jason-b",
            payload=payload,
            event_id="mem_evt_test",
        )

    assert seen["path"] == "/v1/default/banks/zoe-project-jason-b/memories"
    assert seen["payload"]["async"] is False
    assert seen["payload"]["items"][0]["document_id"] == "mem_evt_test"
    assert result["retained"] is True
    assert result["event_id"] == "mem_evt_test"


@pytest.mark.asyncio
async def test_retain_payload_refuses_when_disabled():
    client = HindsightMemoryClient(HindsightConfig(enabled=False))

    result = await client.retain_payload(
        bank_id="zoe-project-jason-b",
        payload={"async": True, "items": []},
        event_id="mem_evt_test",
    )

    assert result == {
        "enabled": False,
        "retained": False,
        "reason": "disabled",
        "bank_id": "zoe-project-jason-b",
        "event_id": "mem_evt_test",
    }


@pytest.mark.asyncio
async def test_operation_status_gets_hindsight_operation_endpoint():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"operation_id": "op_123", "status": "completed"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True), client=http_client)
        result = await client.operation_status(bank_id="zoe-project-jason", operation_id="op_123")

    assert seen["method"] == "GET"
    assert seen["path"] == "/v1/default/banks/zoe-project-jason/operations/op_123"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_wait_for_operation_returns_immediately_when_disabled():
    client = HindsightMemoryClient(HindsightConfig(enabled=False))

    result = await client.wait_for_operation(
        bank_id="zoe-project-jason",
        operation_id="op_123",
        timeout_seconds=120,
        poll_seconds=120,
    )

    assert result == {"enabled": False, "status": "disabled", "reason": "disabled"}


@pytest.mark.asyncio
async def test_wait_for_retain_results_waits_for_async_operations():
    async def handler(request):
        return httpx.Response(200, json={"operation_id": "op_123", "status": "completed", "unit_ids_count": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True), client=http_client)
        result = await client.wait_for_retain_results(
            [{"operation_id": "op_123", "bank_id": "zoe-project-jason"}],
            timeout_seconds=1,
            poll_seconds=0.1,
        )

    assert result == [{"operation_id": "op_123", "status": "completed", "unit_ids_count": 1}]


@pytest.mark.asyncio
async def test_wait_for_retain_results_skips_non_async_responses():
    client = HindsightMemoryClient(HindsightConfig(enabled=True))

    result = await client.wait_for_retain_results([{"success": True, "items_count": 1}])

    assert result == [{"status": "not_async", "retain_result": {"success": True, "items_count": 1}}]


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


@pytest.mark.asyncio
async def test_recall_uses_user_and_scope_isolated_bank_ids():
    seen_paths = []

    async def handler(request):
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True, bank_prefix="zoe-test"), client=http_client)
        await client.recall(user_id="Jason", scope="personal", query="What failed?")
        await client.recall(user_id="Alex", scope="personal", query="What failed?")
        await client.recall(user_id="Jason", scope="shared", query="What failed?")

    assert seen_paths == [
        "/v1/default/banks/zoe-test-personal-jason/memories/recall",
        "/v1/default/banks/zoe-test-personal-alex/memories/recall",
        "/v1/default/banks/zoe-test-shared-jason/memories/recall",
    ]


def test_event_to_hindsight_item_serializes_structured_context_as_json():
    item = event_to_hindsight_item(_event())

    assert 'evidence_refs=["pytest:test_hindsight_memory"]' in item["context"]
    assert "evidence_refs=['" not in item["context"]


@pytest.mark.asyncio
async def test_request_wraps_non_json_response():
    async def handler(request):
        return httpx.Response(200, text="not json")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True), client=http_client)
        with pytest.raises(HindsightMemoryError, match="not valid JSON"):
            await client.health()


@pytest.mark.asyncio
async def test_wait_for_retain_results_polls_async_operations_concurrently():
    call_count = 0

    async def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"operation_id": request.url.path.rsplit("/", 1)[-1], "status": "completed"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HindsightMemoryClient(HindsightConfig(enabled=True), client=http_client)
        result = await client.wait_for_retain_results(
            [
                {"operation_id": "op_1", "bank_id": "zoe-project-jason"},
                {"operation_id": "op_2", "bank_id": "zoe-project-jason"},
            ],
            timeout_seconds=1,
            poll_seconds=0.1,
        )

    assert [item["operation_id"] for item in result] == ["op_1", "op_2"]
    assert call_count == 2
