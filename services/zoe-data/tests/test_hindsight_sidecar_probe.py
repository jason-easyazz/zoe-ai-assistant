import pytest

from hindsight_sidecar_probe import _matching_lines, probe_hindsight_sidecar, probe_hindsight_sidecar_sync


@pytest.mark.asyncio
async def test_probe_reports_disabled_without_health_request():
    result = await probe_hindsight_sidecar(env={}, include_process_scan=False)

    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["acceptable"] is True
    assert payload["status"] == "disabled"
    assert payload["reason"] == "HINDSIGHT_ENABLED is false"
    assert payload["health"]["reason"] == "disabled"


@pytest.mark.asyncio
async def test_probe_rejects_cloud_model_config():
    result = await probe_hindsight_sidecar(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "openai",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["enabled"] is True
    assert result.config["base_url"] == "http://127.0.0.1:8888"
    assert result.config["llm_provider"] == "openai"
    assert "not allowed" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_reports_process_hits_when_config_is_invalid(monkeypatch):
    monkeypatch.setattr("hindsight_sidecar_probe._scan_processes", lambda markers: ["456 hindsight-server"])
    monkeypatch.setattr("hindsight_sidecar_probe._scan_containers", lambda markers: ["hindsight-sidecar image"])

    result = await probe_hindsight_sidecar(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "openai",
        },
        include_process_scan=True,
    )

    assert result.status == "misconfigured"
    assert result.process_hits == ("456 hindsight-server",)
    assert result.container_hits == ("hindsight-sidecar image",)


@pytest.mark.asyncio
async def test_probe_reports_malformed_env_as_misconfigured():
    result = await probe_hindsight_sidecar(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_TIMEOUT_SECONDS": "not-a-number",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["base_url"] == "http://127.0.0.1:8888"
    assert result.config["bank_prefix"] == "zoe"
    assert "could not convert string to float" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_reports_healthy_sidecar(monkeypatch):
    async def fake_health(self):
        return {"status": "ok", "version": "test"}

    monkeypatch.setattr("hindsight_sidecar_probe.HindsightMemoryClient.health", fake_health)

    result = await probe_hindsight_sidecar(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
        },
        include_process_scan=False,
    )

    assert result.ok is True
    assert result.acceptable is True
    assert result.status == "healthy"
    assert result.health["status"] == "ok"


@pytest.mark.asyncio
async def test_probe_reports_unhealthy_sidecar_response(monkeypatch):
    async def fake_health(self):
        return {"status": "starting"}

    monkeypatch.setattr("hindsight_sidecar_probe.HindsightMemoryClient.health", fake_health)

    result = await probe_hindsight_sidecar(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
        },
        include_process_scan=False,
    )

    assert result.ok is False
    assert result.acceptable is False
    assert result.status == "unhealthy"
    assert result.reason == "health response did not report ok/healthy"


@pytest.mark.asyncio
async def test_probe_reports_offline_sidecar(monkeypatch):
    async def fake_health(self):
        from hindsight_memory import HindsightMemoryError

        raise HindsightMemoryError("connection refused")

    monkeypatch.setattr("hindsight_sidecar_probe.HindsightMemoryClient.health", fake_health)

    result = await probe_hindsight_sidecar(
        env={
            "HINDSIGHT_ENABLED": "true",
            "HINDSIGHT_BASE_URL": "http://127.0.0.1:8888",
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
        },
        include_process_scan=False,
    )

    assert result.ok is False
    assert result.acceptable is False
    assert result.status == "offline"
    assert "connection refused" in (result.reason or "")


def test_probe_sync_wrapper_returns_dict():
    payload = probe_hindsight_sidecar_sync(env={}, include_process_scan=False)

    assert payload["status"] == "disabled"
    assert payload["acceptable"] is True
    assert isinstance(payload["latency_ms"], float)


def test_process_scan_ignores_probe_process_itself():
    matches = _matching_lines(
        [
            "123 python3 scripts/maintenance/hindsight_sidecar_probe.py --json",
            "124 python3 -m hindsight_sidecar_probe",
            "456 hindsight-server --host 127.0.0.1",
        ],
        ("hindsight",),
    )

    assert matches == ["456 hindsight-server --host 127.0.0.1"]
