import pytest

from graphiti_sidecar_probe import (
    GraphitiProbeConfig,
    _matching_lines,
    probe_graphiti_sidecar,
    probe_graphiti_sidecar_sync,
)


@pytest.mark.asyncio
async def test_probe_reports_disabled_without_tcp_check(monkeypatch):
    def fail_tcp_check(host, port, timeout_seconds):
        raise AssertionError("disabled probe should not connect")

    monkeypatch.setattr("graphiti_sidecar_probe._tcp_check", fail_tcp_check)

    result = await probe_graphiti_sidecar(env={}, include_process_scan=False)

    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["acceptable"] is True
    assert payload["status"] == "disabled"
    assert payload["reason"] == "GRAPHITI_ENABLED is false"
    assert payload["health"]["reason"] == "disabled"


@pytest.mark.asyncio
async def test_probe_rejects_public_backend_host():
    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_BACKEND": "falkordb",
            "GRAPHITI_FALKORDB_HOST": "graph.example.com",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["falkordb_host"] == "graph.example.com"
    assert "localhost or private network" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_rejects_unknown_backend():
    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_BACKEND": "remotegraph",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert "GRAPHITI_BACKEND" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_reports_malformed_port_as_misconfigured():
    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_FALKORDB_PORT": "not-a-port",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["falkordb_port"] == "not-a-port"
    assert "invalid literal" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_reports_unrecognized_boolean_as_misconfigured():
    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "enabled",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["enabled"] == "enabled"
    assert "Unrecognized boolean" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_reports_out_of_range_port_as_misconfigured():
    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_FALKORDB_PORT": "99999",
        },
        include_process_scan=False,
    )

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["falkordb_port"] == 99999
    assert "1-65535" in (result.reason or "")


@pytest.mark.asyncio
async def test_probe_reports_healthy_falkordb_backend(monkeypatch):
    monkeypatch.setattr("graphiti_sidecar_probe._tcp_check", lambda host, port, timeout: (True, None))

    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_BACKEND": "falkordb",
            "GRAPHITI_FALKORDB_HOST": "127.0.0.1",
            "GRAPHITI_FALKORDB_PORT": "6379",
        },
        include_process_scan=False,
    )

    assert result.ok is True
    assert result.acceptable is True
    assert result.status == "healthy"
    assert result.health == {
        "backend": "falkordb",
        "host": "127.0.0.1",
        "port": 6379,
        "tcp_reachable": True,
    }


@pytest.mark.asyncio
async def test_probe_reports_healthy_neo4j_backend(monkeypatch):
    monkeypatch.setattr("graphiti_sidecar_probe._tcp_check", lambda host, port, timeout: (True, None))

    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_BACKEND": "neo4j",
            "GRAPHITI_NEO4J_HOST": "127.0.0.1",
            "GRAPHITI_NEO4J_BOLT_PORT": "7687",
        },
        include_process_scan=False,
    )

    assert result.ok is True
    assert result.status == "healthy"
    assert result.health["backend"] == "neo4j"
    assert result.health["port"] == 7687


@pytest.mark.asyncio
async def test_probe_reports_offline_backend(monkeypatch):
    monkeypatch.setattr("graphiti_sidecar_probe._tcp_check", lambda host, port, timeout: (False, "connection refused"))

    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_BACKEND": "falkordb",
        },
        include_process_scan=False,
    )

    assert result.ok is False
    assert result.acceptable is False
    assert result.status == "offline"
    assert result.reason == "connection refused"


@pytest.mark.asyncio
async def test_probe_reports_process_hits_when_config_is_invalid(monkeypatch):
    monkeypatch.setattr("graphiti_sidecar_probe._scan_processes", lambda markers: ["456 falkordb-server"])
    monkeypatch.setattr("graphiti_sidecar_probe._scan_containers", lambda markers: ["neo4j graph image"])

    result = await probe_graphiti_sidecar(
        env={
            "GRAPHITI_ENABLED": "true",
            "GRAPHITI_BACKEND": "remotegraph",
        },
        include_process_scan=True,
    )

    assert result.status == "misconfigured"
    assert result.process_hits == ("456 falkordb-server",)
    assert result.container_hits == ("neo4j graph image",)


def test_probe_sync_wrapper_returns_dict():
    payload = probe_graphiti_sidecar_sync(env={}, include_process_scan=False)

    assert payload["status"] == "disabled"
    assert payload["acceptable"] is True
    assert isinstance(payload["latency_ms"], float)


def test_process_scan_ignores_probe_process_itself():
    matches = _matching_lines(
        [
            "123 python3 scripts/maintenance/graphiti_sidecar_probe.py --json",
            "124 python3 -m graphiti_sidecar_probe",
            "456 falkordb-server --port 6379",
            "789 neo4j console",
        ],
        ("graphiti", "falkordb", "neo4j"),
    )

    assert matches == ["456 falkordb-server --port 6379", "789 neo4j console"]


def test_config_defaults_to_falkordb_offline_localhost():
    config = GraphitiProbeConfig.from_env({})

    assert config.enabled is False
    assert config.backend == "falkordb"
    assert config.falkordb_host == "127.0.0.1"
    assert config.falkordb_port == 6379
    assert config.offline_only is True
