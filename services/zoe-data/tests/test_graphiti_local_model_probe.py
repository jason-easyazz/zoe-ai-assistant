import httpx
import pytest

from graphiti_local_model_probe import (
    _chat_completions_url,
    _parse_json_object,
    probe_graphiti_local_model_contract,
    probe_graphiti_local_model_contract_sync,
)


def _valid_payload():
    return {
        "entities": [
            {"id": "weather_card", "type": "tool"},
            {"id": "voice_queue_guard", "type": "fix"},
            {"id": "test_voice_transcribe", "type": "test"},
        ],
        "relationships": [
            {"source": "zoe_weather_card", "type": "FAILED_ON", "target": "mobile_dashboard_render"},
            {"source": "voice_queue_guard", "type": "FIXED_BY", "target": "duplicate_weather_response"},
            {"source": "voice_queue_guard", "type": "MEASURED_BY", "target": "test_voice_transcribe"},
        ],
        "evidence_refs": ["trace:weather-queue:001", "pytest:test_voice_transcribe"],
    }


@pytest.mark.asyncio
async def test_local_model_probe_defaults_to_disabled_without_call(monkeypatch):
    async def fail_call(config):
        raise AssertionError("disabled probe must not call the model")

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fail_call)

    result = await probe_graphiti_local_model_contract(env={})

    assert result.status == "disabled"
    assert result.acceptable is True
    assert result.ok is False
    assert result.config["llm_base_url"] == "http://127.0.0.1:11434/v1"


@pytest.mark.asyncio
async def test_local_model_probe_rejects_public_endpoint():
    result = await probe_graphiti_local_model_contract(
        env={"GRAPHITI_LLM_BASE_URL": "https://openrouter.ai/api/v1"},
        run=True,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert "GRAPHITI_LLM_BASE_URL" in (result.reason or "")


@pytest.mark.asyncio
async def test_local_model_probe_reports_unavailable_endpoint(monkeypatch):
    async def fail_call(config):
        raise httpx.ConnectError("local endpoint refused connection")

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fail_call)

    result = await probe_graphiti_local_model_contract(env={}, run=True)

    assert result.status == "llm_unavailable"
    assert result.acceptable is False
    assert "local endpoint refused connection" in (result.reason or "")


@pytest.mark.asyncio
async def test_local_model_probe_reports_invalid_json(monkeypatch):
    async def fake_call(config):
        return "Here are some facts, but not JSON."

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fake_call)

    result = await probe_graphiti_local_model_contract(env={}, run=True)

    assert result.status == "invalid_json"
    assert result.acceptable is False
    assert result.raw_text == "Here are some facts, but not JSON."


@pytest.mark.asyncio
async def test_local_model_probe_reports_contract_mismatch(monkeypatch):
    async def fake_call(config):
        return '{"entities": [{"id": "zoe_weather_card"}], "relationships": [], "evidence_refs": []}'

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fake_call)

    result = await probe_graphiti_local_model_contract(env={}, run=True)

    assert result.status == "contract_mismatch"
    assert result.acceptable is False
    assert "relationships" in (result.reason or "")


@pytest.mark.asyncio
async def test_local_model_probe_reports_missing_entity_id(monkeypatch):
    payload = _valid_payload()
    payload["entities"] = ["weather_card"]

    async def fake_call(config):
        return __import__("json").dumps(payload)

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fake_call)

    result = await probe_graphiti_local_model_contract(env={}, run=True)

    assert result.status == "contract_mismatch"
    assert "voice_queue_guard" in (result.reason or "")


@pytest.mark.asyncio
async def test_local_model_probe_reports_missing_evidence_ref(monkeypatch):
    payload = _valid_payload()
    payload["evidence_refs"] = ["trace:weather-queue:001"]

    async def fake_call(config):
        return __import__("json").dumps(payload)

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fake_call)

    result = await probe_graphiti_local_model_contract(env={}, run=True)

    assert result.status == "contract_mismatch"
    assert "pytest:test_voice_transcribe" in (result.reason or "")


@pytest.mark.asyncio
async def test_local_model_probe_reports_structured_output_ready(monkeypatch):
    async def fake_call(config):
        return "```json\n" + __import__("json").dumps(_valid_payload()) + "\n```"

    monkeypatch.setattr("graphiti_local_model_probe._call_local_chat_completion", fake_call)

    result = await probe_graphiti_local_model_contract(env={}, run=True)

    assert result.ok is True
    assert result.acceptable is True
    assert result.status == "structured_output_ready"
    assert result.parsed["evidence_refs"] == ["trace:weather-queue:001", "pytest:test_voice_transcribe"]


def test_parse_json_object_accepts_wrapped_json():
    parsed = _parse_json_object("prefix " + __import__("json").dumps(_valid_payload()) + " suffix")

    assert parsed["entities"][0]["id"] == "weather_card"


def test_parse_json_object_uses_first_balanced_object():
    text = "prefix " + __import__("json").dumps(_valid_payload()) + " trailing {score: high}"
    parsed = _parse_json_object(text)

    assert parsed["relationships"][0]["type"] == "FAILED_ON"


def test_chat_completions_url_handles_supported_bases():
    assert _chat_completions_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/chat/completions"
    assert _chat_completions_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1/chat/completions"
    assert _chat_completions_url("http://127.0.0.1:11434/openai/v1") == "http://127.0.0.1:11434/openai/v1/chat/completions"
    assert _chat_completions_url("http://127.0.0.1:11434/v1/chat/completions") == "http://127.0.0.1:11434/v1/chat/completions"


def test_local_model_probe_rejects_ambiguous_proxy_path():
    payload = probe_graphiti_local_model_contract_sync(
        env={"GRAPHITI_LLM_BASE_URL": "http://127.0.0.1:11434/api/v2"},
        run=True,
    )

    assert payload["status"] == "misconfigured"
    assert "OpenAI-compatible" in (payload["reason"] or "")


def test_local_model_probe_sync_wrapper_returns_dict():
    payload = probe_graphiti_local_model_contract_sync(env={}, run=False)

    assert payload["status"] == "disabled"
    assert payload["acceptable"] is True


@pytest.mark.asyncio
async def test_local_model_probe_sync_wrapper_rejects_running_event_loop():
    with pytest.raises(RuntimeError, match="running event loop"):
        probe_graphiti_local_model_contract_sync(env={}, run=False)
