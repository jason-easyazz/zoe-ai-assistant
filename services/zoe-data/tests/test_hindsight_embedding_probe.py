import httpx
import pytest

from hindsight_embedding_probe import (
    _health_url,
    _service_health,
    probe_hindsight_embeddings,
    probe_hindsight_embeddings_sync,
)


@pytest.mark.asyncio
async def test_embedding_probe_reports_disabled_as_acceptable():
    result = await probe_hindsight_embeddings(env={})

    assert result.ok is False
    assert result.acceptable is True
    assert result.status == "disabled"
    assert result.reason == "HINDSIGHT_ENABLED is false"


@pytest.mark.asyncio
async def test_embedding_probe_reports_missing_default_local_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HF_HOME": str(tmp_path / "hf"),
        }
    )

    assert result.ok is False
    assert result.acceptable is False
    assert result.status == "missing_local_model"
    assert result.provider == "local"
    assert result.model == "BAAI/bge-small-en-v1.5"
    assert any("models--BAAI--bge-small-en-v1.5" in path for path in result.checked_paths)


@pytest.mark.asyncio
async def test_embedding_probe_accepts_existing_local_model_path(tmp_path):
    model_dir = tmp_path / "models" / "bge"
    model_dir.mkdir(parents=True)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "local",
            "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL": str(model_dir),
        }
    )

    assert result.ok is True
    assert result.acceptable is True
    assert result.status == "local_model_available"
    assert str(model_dir) in result.checked_paths


@pytest.mark.asyncio
async def test_embedding_probe_accepts_cached_huggingface_model(tmp_path):
    cached = tmp_path / "hf" / "hub" / "models--BAAI--bge-small-en-v1.5" / "snapshots"
    cached.mkdir(parents=True)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HF_HOME": str(tmp_path / "hf"),
        }
    )

    assert result.ok is True
    assert result.status == "local_model_available"
    assert str(cached) in result.checked_paths


@pytest.mark.asyncio
async def test_embedding_probe_accepts_huggingface_hub_cache_override(tmp_path):
    cached = tmp_path / "hf-hub" / "models--BAAI--bge-small-en-v1.5" / "snapshots"
    cached.mkdir(parents=True)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HF_HUB_CACHE": str(tmp_path / "hf-hub"),
        }
    )

    assert result.ok is True
    assert result.status == "local_model_available"
    assert str(cached) in result.checked_paths


@pytest.mark.asyncio
async def test_embedding_probe_reports_missing_onnx_path(tmp_path):
    missing = tmp_path / "missing.onnx"
    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "onnx",
            "HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_PATH": str(missing),
        }
    )

    assert result.ok is False
    assert result.status == "missing_onnx_model"
    assert result.checked_paths == (str(missing),)


@pytest.mark.asyncio
async def test_embedding_probe_rejects_cloud_embedding_provider():
    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
        }
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert "embeddings provider" in (result.reason or "")


@pytest.mark.asyncio
async def test_embedding_probe_accepts_local_tei_service(monkeypatch):
    async def fake_health(provider, base_url):
        assert provider == "tei"
        assert base_url == "http://127.0.0.1:8080"
        return {"status": "ok"}

    monkeypatch.setattr("hindsight_embedding_probe._service_health", fake_health)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "http://127.0.0.1:8080",
        }
    )

    assert result.ok is True
    assert result.status == "service_healthy"


@pytest.mark.asyncio
async def test_embedding_probe_reports_local_service_offline(monkeypatch):
    async def fake_health(provider, base_url):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("hindsight_embedding_probe._service_health", fake_health)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "http://127.0.0.1:8080",
        }
    )

    assert result.ok is False
    assert result.status == "service_offline"
    assert "connection refused" in (result.reason or "")


@pytest.mark.asyncio
async def test_embedding_probe_reports_local_service_unhealthy_status(monkeypatch):
    async def fake_health(provider, base_url):
        return {"status": "loading"}

    monkeypatch.setattr("hindsight_embedding_probe._service_health", fake_health)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "http://127.0.0.1:8080",
        }
    )

    assert result.ok is False
    assert result.acceptable is False
    assert result.status == "service_unhealthy"


@pytest.mark.asyncio
async def test_embedding_probe_reports_http_error_as_service_unhealthy(monkeypatch):
    async def fake_health(provider, base_url):
        request = httpx.Request("GET", "http://127.0.0.1:8080/health")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr("hindsight_embedding_probe._service_health", fake_health)

    result = await probe_hindsight_embeddings(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "tei",
            "HINDSIGHT_API_EMBEDDINGS_TEI_URL": "http://127.0.0.1:8080",
        }
    )

    assert result.ok is False
    assert result.acceptable is False
    assert result.status == "service_unhealthy"
    assert result.health == {"status_code": 500}


@pytest.mark.asyncio
async def test_service_health_reports_malformed_json_as_unhealthy(monkeypatch):
    class FakeResponse:
        headers = {"content-type": "application/json"}
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("bad json")

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            assert url == "http://127.0.0.1:8080/health"
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    health = await _service_health("tei", "http://127.0.0.1:8080")

    assert health == {"status": "invalid_json", "status_code": 200}


def test_embedding_probe_health_url_for_openai_compatible_base():
    assert _health_url("openai", "http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/models"
    assert _health_url("openai", "http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1/models"
    assert _health_url("tei", "http://127.0.0.1:8080") == "http://127.0.0.1:8080/health"


def test_embedding_probe_sync_wrapper_returns_dict():
    payload = probe_hindsight_embeddings_sync(env={})

    assert payload["status"] == "disabled"
    assert payload["acceptable"] is True


@pytest.mark.asyncio
async def test_embedding_probe_sync_wrapper_rejects_running_event_loop():
    with pytest.raises(RuntimeError, match="cannot be called from a running event loop"):
        probe_hindsight_embeddings_sync(env={})
