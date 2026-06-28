"""Read-only Graphiti runtime compatibility probe for Zoe.

This probe checks whether Zoe has enough local/offline runtime pieces to attempt
an actual Graphiti ingest bake-off. It never imports Graphiti unless enabled,
never ingests episodes, never writes graph data, and never calls an LLM generate
endpoint.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import httpx

from graphiti_sidecar_probe import GraphitiProbeConfig, GraphitiProbeConfigError, probe_graphiti_sidecar


class GraphitiRuntimeConfigError(ValueError):
    """Raised when visible Graphiti runtime probe config is invalid."""


@dataclass(frozen=True)
class GraphitiRuntimeConfig:
    enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    offline_only: bool = True
    timeout_seconds: float = 2.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GraphitiRuntimeConfig":
        values = env or os.environ
        config = cls(
            enabled=_env_bool(values.get("GRAPHITI_ENABLED"), default=False),
            llm_base_url=(values.get("GRAPHITI_LLM_BASE_URL") or "http://127.0.0.1:11434/v1").rstrip("/"),
            llm_model=values.get("GRAPHITI_LLM_MODEL") or "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
            offline_only=_env_bool(values.get("GRAPHITI_OFFLINE_ONLY"), default=True),
            timeout_seconds=_env_float(values.get("GRAPHITI_RUNTIME_PROBE_TIMEOUT_SECONDS"), default=2.0),
        )
        config.validate()
        return config

    @classmethod
    def snapshot_from_env(cls, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        values = env or os.environ
        return {
            "enabled": _env_bool_snapshot(values.get("GRAPHITI_ENABLED"), default=False),
            "llm_base_url": (values.get("GRAPHITI_LLM_BASE_URL") or "http://127.0.0.1:11434/v1").rstrip("/"),
            "llm_model": values.get("GRAPHITI_LLM_MODEL") or "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
            "offline_only": _env_bool_snapshot(values.get("GRAPHITI_OFFLINE_ONLY"), default=True),
            "timeout_seconds": _env_float_snapshot(values.get("GRAPHITI_RUNTIME_PROBE_TIMEOUT_SECONDS"), default=2.0),
        }

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise GraphitiRuntimeConfigError("GRAPHITI_RUNTIME_PROBE_TIMEOUT_SECONDS must be positive")
        if self.offline_only and not _is_local_or_private_url(self.llm_base_url):
            raise GraphitiRuntimeConfigError("GRAPHITI_LLM_BASE_URL must be localhost or private network when offline_only is enabled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "offline_only": self.offline_only,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class GraphitiRuntimeProbeResult:
    ok: bool
    acceptable: bool
    status: str
    config: dict[str, Any]
    packages: dict[str, Any]
    backend: dict[str, Any]
    llm: dict[str, Any]
    latency_ms: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acceptable": self.acceptable,
            "status": self.status,
            "config": self.config,
            "packages": self.packages,
            "backend": self.backend,
            "llm": self.llm,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
        }


async def probe_graphiti_runtime(
    *,
    env: Mapping[str, str] | None = None,
    include_process_scan: bool = True,
) -> GraphitiRuntimeProbeResult:
    """Probe Graphiti runtime readiness without graph ingest or writes."""

    started = time.perf_counter()
    try:
        runtime_config = GraphitiRuntimeConfig.from_env(env)
        backend_config = GraphitiProbeConfig.from_env(env)
    except (GraphitiRuntimeConfigError, GraphitiProbeConfigError, ValueError) as exc:
        backend_snapshot = GraphitiProbeConfig.snapshot_from_env(env)
        return GraphitiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            config={
                "runtime": GraphitiRuntimeConfig.snapshot_from_env(env),
                "backend": backend_snapshot,
            },
            packages=_package_snapshot(str(backend_snapshot.get("backend") or "falkordb")),
            backend={},
            llm={},
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    config = {"runtime": runtime_config.to_dict(), "backend": backend_config.to_dict()}
    if not runtime_config.enabled:
        return GraphitiRuntimeProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            config=config,
            packages=_package_snapshot(backend_config.backend),
            backend={"enabled": False, "reason": "GRAPHITI_ENABLED is false"},
            llm={"enabled": False, "reason": "GRAPHITI_ENABLED is false"},
            latency_ms=_elapsed_ms(started),
            reason="GRAPHITI_ENABLED is false",
        )

    packages = _package_snapshot(backend_config.backend)
    missing = [name for name, info in packages.items() if not info.get("available")]
    if missing:
        return GraphitiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="missing_dependency",
            config=config,
            packages=packages,
            backend={},
            llm={},
            latency_ms=_elapsed_ms(started),
            reason="missing Python package(s): " + ", ".join(missing),
        )

    backend = await probe_graphiti_sidecar(env=env, include_process_scan=include_process_scan)
    backend_payload = backend.to_dict()
    if not backend.ok:
        return GraphitiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="backend_offline" if backend.status == "offline" else backend.status,
            config=config,
            packages=packages,
            backend=backend_payload,
            llm={},
            latency_ms=_elapsed_ms(started),
            reason=backend.reason,
        )

    try:
        llm = await _probe_openai_compatible_llm(runtime_config)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return GraphitiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="llm_unavailable",
            config=config,
            packages=packages,
            backend=backend_payload,
            llm={},
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    if not llm.get("model_available"):
        return GraphitiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="llm_model_missing",
            config=config,
            packages=packages,
            backend=backend_payload,
            llm=llm,
            latency_ms=_elapsed_ms(started),
            reason=f"model {runtime_config.llm_model!r} was not advertised by local LLM endpoint",
        )

    return GraphitiRuntimeProbeResult(
        ok=True,
        acceptable=True,
        status="ready_for_ingest_trial",
        config=config,
        packages=packages,
        backend=backend_payload,
        llm=llm,
        latency_ms=_elapsed_ms(started),
    )


def _package_snapshot(backend: str) -> dict[str, Any]:
    packages = ["graphiti_core"]
    if backend == "neo4j":
        packages.append("neo4j")
    else:
        packages.append("falkordb")
    return {name: _package_info(name) for name in packages}


def _package_info(import_name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(import_name) is not None
    version = None
    if available:
        distribution_name = "graphiti-core" if import_name == "graphiti_core" else import_name
        try:
            version = importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            version = None
    return {"available": available, "version": version}


async def _probe_openai_compatible_llm(config: GraphitiRuntimeConfig) -> dict[str, Any]:
    url = _models_url(config.llm_base_url)
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    models = _extract_model_ids(payload)
    return {
        "base_url": config.llm_base_url,
        "models_url": url,
        "model": config.llm_model,
        "model_available": config.llm_model in models,
        "advertised_models": models[:20],
    }


def _models_url(base_url: str) -> str:
    base = base_url.rstrip("/") + "/"
    parsed = urlparse(base)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        return urljoin(base, "models")
    return urljoin(base, "v1/models")


def _extract_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, MappingABC):
        return []
    raw_models = payload.get("data") or payload.get("models") or []
    models: list[str] = []
    for item in raw_models:
        if isinstance(item, MappingABC):
            model_id = item.get("id") or item.get("model") or item.get("name")
            if model_id:
                models.append(str(model_id))
    return models


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unrecognized boolean env var value: {value!r}")


def _env_bool_snapshot(value: str | None, *, default: bool) -> bool | str:
    try:
        return _env_bool(value, default=default)
    except ValueError:
        return str(value)


def _env_float(value: str | None, *, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(str(value).strip())


def _env_float_snapshot(value: str | None, *, default: float) -> float | str:
    try:
        return _env_float(value, default=default)
    except ValueError:
        return str(value)


def _is_local_or_private_url(url: str | None) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.hostname
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in {"localhost", "zoe", "host.docker.internal"}:
        return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized.endswith(".local") or normalized.endswith(".lan")
    return ip.is_loopback or ip.is_private or ip.is_link_local


def probe_graphiti_runtime_sync(
    *,
    env: Mapping[str, str] | None = None,
    include_process_scan: bool = True,
) -> dict[str, Any]:
    """CLI-only sync wrapper; async callers should await probe_graphiti_runtime."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(probe_graphiti_runtime(env=env, include_process_scan=include_process_scan)).to_dict()

    raise RuntimeError("probe_graphiti_runtime_sync cannot be called from a running event loop")


__all__ = [
    "GraphitiRuntimeConfig",
    "GraphitiRuntimeConfigError",
    "GraphitiRuntimeProbeResult",
    "probe_graphiti_runtime",
    "probe_graphiti_runtime_sync",
]
