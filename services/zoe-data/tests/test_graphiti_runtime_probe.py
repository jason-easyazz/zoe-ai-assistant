import pytest
import httpx

from graphiti_runtime_probe import (
    GraphitiRuntimeConfig,
    _extract_model_ids,
    _model_is_advertised,
    _models_url,
    probe_graphiti_runtime,
    probe_graphiti_runtime_sync,
)


@pytest.mark.asyncio
async def test_runtime_probe_reports_disabled_without_backend_or_llm(monkeypatch):
    async def fail_sidecar(*args, **kwargs):
        raise AssertionError("disabled runtime probe should not call backend")

    monkeypatch.setattr("graphiti_runtime_probe.probe_graphiti_sidecar", fail_sidecar)

    result = await probe_graphiti_runtime(env={}, include_process_scan=False)

    assert result.ok is False
    assert result.acceptable is True
    assert result.status == "disabled"
    assert result.reason == "GRAPHITI_ENABLED is false"


@pytest.mark.asyncio
async def test_runtime_probe_rejects_public_llm_base_url():
    result = await probe_graphiti_runtime(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_LLM_BASE_URL": "https://api.example.com/v1",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert "GRAPHITI_LLM_BASE_URL" in (result.reason or "")


@pytest.mark.asyncio
async def test_runtime_probe_reports_missing_python_dependencies(monkeypatch):
    monkeypatch.setattr(
        "graphiti_runtime_probe._package_snapshot",
        lambda backend: {
            "graphiti_core": {"available": False, "version": None},
            "falkordb": {"available": True, "version": "1.6.1"},
        },
    )

    result = await probe_graphiti_runtime(env={"GRAPHITI_ENABLED": "true"}, include_process_scan=False)

    assert result.status == "missing_dependency"
    assert result.acceptable is False
    assert "graphiti_core" in (result.reason or "")


@pytest.mark.asyncio
async def test_runtime_probe_reports_backend_offline(monkeypatch):
    monkeypatch.setattr(
        "graphiti_runtime_probe._package_snapshot",
        lambda backend: {
            "graphiti_core": {"available": True, "version": "0.29.2"},
            "falkordb": {"available": True, "version": "1.6.1"},
        },
    )

    class Backend:
        ok = False
        status = "offline"
        reason = "connection refused"

        def to_dict(self):
            return {"ok": False, "status": "offline", "reason": self.reason}

    async def fake_sidecar(*args, **kwargs):
        return Backend()

    monkeypatch.setattr("graphiti_runtime_probe.probe_graphiti_sidecar", fake_sidecar)

    result = await probe_graphiti_runtime(env={"GRAPHITI_ENABLED": "true"}, include_process_scan=False)

    assert result.status == "backend_offline"
    assert result.reason == "connection refused"


@pytest.mark.asyncio
async def test_runtime_probe_reports_missing_llm_model(monkeypatch):
    monkeypatch.setattr(
        "graphiti_runtime_probe._package_snapshot",
        lambda backend: {
            "graphiti_core": {"available": True, "version": "0.29.2"},
            "falkordb": {"available": True, "version": "1.6.1"},
        },
    )

    class Backend:
        ok = True
        status = "healthy"
        reason = None

        def to_dict(self):
            return {"ok": True, "status": "healthy"}

    async def fake_sidecar(*args, **kwargs):
        return Backend()

    async def fake_llm(config):
        return {"model": config.llm_model, "model_available": False, "advertised_models": ["other"]}

    monkeypatch.setattr("graphiti_runtime_probe.probe_graphiti_sidecar", fake_sidecar)
    monkeypatch.setattr("graphiti_runtime_probe._probe_openai_compatible_llm", fake_llm)

    result = await probe_graphiti_runtime(env={"GRAPHITI_ENABLED": "true"}, include_process_scan=False)

    assert result.status == "llm_model_missing"
    assert result.acceptable is False


@pytest.mark.asyncio
async def test_runtime_probe_reports_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        "graphiti_runtime_probe._package_snapshot",
        lambda backend: {
            "graphiti_core": {"available": True, "version": "0.29.2"},
            "falkordb": {"available": True, "version": "1.6.1"},
        },
    )

    class Backend:
        ok = True
        status = "healthy"
        reason = None

        def to_dict(self):
            return {"ok": True, "status": "healthy"}

    async def fake_sidecar(*args, **kwargs):
        return Backend()

    async def fail_llm(config):
        raise httpx.ConnectError("local llama-server unavailable")

    monkeypatch.setattr("graphiti_runtime_probe.probe_graphiti_sidecar", fake_sidecar)
    monkeypatch.setattr("graphiti_runtime_probe._probe_openai_compatible_llm", fail_llm)

    result = await probe_graphiti_runtime(env={"GRAPHITI_ENABLED": "true"}, include_process_scan=False)

    assert result.status == "llm_unavailable"
    assert result.acceptable is False
    assert "local llama-server unavailable" in (result.reason or "")


@pytest.mark.asyncio
async def test_runtime_probe_reports_ready(monkeypatch):
    monkeypatch.setattr(
        "graphiti_runtime_probe._package_snapshot",
        lambda backend: {
            "graphiti_core": {"available": True, "version": "0.29.2"},
            "falkordb": {"available": True, "version": "1.6.1"},
        },
    )

    class Backend:
        ok = True
        status = "healthy"
        reason = None

        def to_dict(self):
            return {"ok": True, "status": "healthy"}

    async def fake_sidecar(*args, **kwargs):
        return Backend()

    async def fake_llm(config):
        return {"model": config.llm_model, "model_available": True, "advertised_models": [config.llm_model]}

    monkeypatch.setattr("graphiti_runtime_probe.probe_graphiti_sidecar", fake_sidecar)
    monkeypatch.setattr("graphiti_runtime_probe._probe_openai_compatible_llm", fake_llm)

    result = await probe_graphiti_runtime(env={"GRAPHITI_ENABLED": "true"}, include_process_scan=False)

    assert result.ok is True
    assert result.acceptable is True
    assert result.status == "ready_for_ingest_trial"


def test_runtime_config_defaults_to_local_gemma_endpoint():
    config = GraphitiRuntimeConfig.from_env({})

    assert config.llm_base_url == "http://127.0.0.1:11434/v1"
    assert config.llm_model == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert config.offline_only is True


def test_models_url_handles_v1_base():
    assert _models_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/models"
    assert _models_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1/models"


def test_extract_model_ids_accepts_openai_and_llama_shapes():
    assert _extract_model_ids({"data": [{"id": "a"}]}) == ["a"]
    assert _extract_model_ids({"models": [{"model": "b"}, {"name": "c"}]}) == ["b", "c"]


def test_model_is_advertised_matches_gguf_and_bare_id_forms():
    canonical = "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    bare = "gemma-4-E4B-it-qat-UD-Q4_K_XL"

    # endpoint advertises the bare id; config holds the .gguf filename (and vice versa)
    assert _model_is_advertised(canonical, [bare]) is True
    assert _model_is_advertised(bare, [canonical]) is True
    # full on-disk path and sharded suffix still resolve to the canonical brain
    assert _model_is_advertised(canonical, ["/models/" + canonical]) is True
    assert _model_is_advertised(canonical, [bare + "-00001-of-00002.gguf"]) is True


def test_model_is_advertised_rejects_unrelated_models():
    canonical = "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"

    assert _model_is_advertised(canonical, ["llama-3.1-8b-instruct", "gemma"]) is False
    assert _model_is_advertised(canonical, []) is False


def test_runtime_probe_sync_wrapper_returns_dict():
    payload = probe_graphiti_runtime_sync(env={}, include_process_scan=False)

    assert payload["status"] == "disabled"
    assert payload["acceptable"] is True


@pytest.mark.asyncio
async def test_runtime_probe_sync_wrapper_rejects_running_event_loop():
    with pytest.raises(RuntimeError, match="running event loop"):
        probe_graphiti_runtime_sync(env={}, include_process_scan=False)
