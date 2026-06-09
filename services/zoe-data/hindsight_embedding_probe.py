"""Read-only Hindsight embedding readiness probe for Zoe.

This probe answers one narrow question before a live Hindsight bake-off:
does Zoe have an offline embedding path available right now? It never downloads
models, creates embeddings, writes memory, or calls Hindsight retain/recall.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlparse

import httpx

from hindsight_memory import HindsightConfig, HindsightOfflineConfigError


@dataclass(frozen=True)
class HindsightEmbeddingProbeResult:
    ok: bool
    acceptable: bool
    status: str
    provider: str
    model: str
    base_url: str | None
    checked_paths: tuple[str, ...]
    health: dict[str, Any]
    latency_ms: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acceptable": self.acceptable,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "checked_paths": list(self.checked_paths),
            "health": self.health,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
        }


async def probe_hindsight_embeddings(
    *,
    env: Mapping[str, str] | None = None,
) -> HindsightEmbeddingProbeResult:
    """Probe the offline embedding dependency without creating embeddings."""

    started = time.perf_counter()
    try:
        config = HindsightConfig.from_env(env)
    except (HindsightOfflineConfigError, ValueError) as exc:
        snapshot = _embedding_snapshot(env)
        return HindsightEmbeddingProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            provider=snapshot["provider"],
            model=snapshot["model"],
            base_url=snapshot["base_url"],
            checked_paths=(),
            health={},
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    provider = (config.embeddings_provider or "").strip().lower()
    if not config.enabled:
        return HindsightEmbeddingProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            provider=provider,
            model=config.embeddings_model,
            base_url=config.embeddings_base_url,
            checked_paths=(),
            health={"enabled": False, "reason": "HINDSIGHT_ENABLED is false"},
            latency_ms=_elapsed_ms(started),
            reason="HINDSIGHT_ENABLED is false",
        )

    if provider in {"local"}:
        return _probe_local_model(config, started, env=env)
    if provider in {"onnx"}:
        return _probe_onnx_model(config, started)
    if provider in {"tei", "openai"}:
        return await _probe_local_embedding_service(config, started)

    return HindsightEmbeddingProbeResult(
        ok=False,
        acceptable=False,
        status="misconfigured",
        provider=provider,
        model=config.embeddings_model,
        base_url=config.embeddings_base_url,
        checked_paths=(),
        health={},
        latency_ms=_elapsed_ms(started),
        reason=f"unsupported Hindsight embeddings provider {provider!r}",
    )


def _probe_local_model(
    config: HindsightConfig,
    started: float,
    *,
    env: Mapping[str, str] | None,
) -> HindsightEmbeddingProbeResult:
    paths = _candidate_local_model_paths(config.embeddings_model, env=env)
    existing = tuple(path for path in paths if Path(path).exists())
    if existing:
        return HindsightEmbeddingProbeResult(
            ok=True,
            acceptable=True,
            status="local_model_available",
            provider=config.embeddings_provider,
            model=config.embeddings_model,
            base_url=config.embeddings_base_url,
            checked_paths=tuple(paths),
            health={},
            latency_ms=_elapsed_ms(started),
        )
    return HindsightEmbeddingProbeResult(
        ok=False,
        acceptable=False,
        status="missing_local_model",
        provider=config.embeddings_provider,
        model=config.embeddings_model,
        base_url=config.embeddings_base_url,
        checked_paths=tuple(paths),
        health={},
        latency_ms=_elapsed_ms(started),
        reason="local embeddings provider requires a cached model path or Hugging Face cache entry",
    )


def _probe_onnx_model(config: HindsightConfig, started: float) -> HindsightEmbeddingProbeResult:
    path = Path(config.embeddings_model).expanduser()
    exists = path.exists()
    return HindsightEmbeddingProbeResult(
        ok=exists,
        acceptable=exists,
        status="onnx_model_available" if exists else "missing_onnx_model",
        provider=config.embeddings_provider,
        model=config.embeddings_model,
        base_url=config.embeddings_base_url,
        checked_paths=(str(path),),
        health={},
        latency_ms=_elapsed_ms(started),
        reason=None if exists else "ONNX embeddings provider requires an existing local model path",
    )


async def _probe_local_embedding_service(
    config: HindsightConfig,
    started: float,
) -> HindsightEmbeddingProbeResult:
    if not config.embeddings_base_url:
        return HindsightEmbeddingProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            provider=config.embeddings_provider,
            model=config.embeddings_model,
            base_url=config.embeddings_base_url,
            checked_paths=(),
            health={},
            latency_ms=_elapsed_ms(started),
            reason="local embedding service provider requires an embeddings base URL",
        )

    try:
        health = await _service_health(config.embeddings_provider, config.embeddings_base_url)
    except httpx.HTTPStatusError as exc:
        return HindsightEmbeddingProbeResult(
            ok=False,
            acceptable=False,
            status="service_unhealthy",
            provider=config.embeddings_provider,
            model=config.embeddings_model,
            base_url=config.embeddings_base_url,
            checked_paths=(),
            health={"status_code": exc.response.status_code},
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )
    except (httpx.HTTPError, OSError) as exc:
        return HindsightEmbeddingProbeResult(
            ok=False,
            acceptable=False,
            status="service_offline",
            provider=config.embeddings_provider,
            model=config.embeddings_model,
            base_url=config.embeddings_base_url,
            checked_paths=(),
            health={},
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    healthy = bool(health.get("ok") or health.get("healthy") or health.get("status") in {"ok", "healthy"})
    return HindsightEmbeddingProbeResult(
        ok=healthy,
        acceptable=healthy,
        status="service_healthy" if healthy else "service_unhealthy",
        provider=config.embeddings_provider,
        model=config.embeddings_model,
        base_url=config.embeddings_base_url,
        checked_paths=(),
        health=health,
        latency_ms=_elapsed_ms(started),
        reason=None if healthy else "embedding service response did not report ok/healthy",
    )


async def _service_health(provider: str, base_url: str) -> dict[str, Any]:
    url = _health_url(provider, base_url)
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("application/json"):
            payload = response.json()
            return payload if isinstance(payload, dict) else {"ok": True, "payload": payload}
        return {"ok": True, "status_code": response.status_code}


def _health_url(provider: str, base_url: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    base = base_url.rstrip("/") + "/"
    if normalized_provider == "openai":
        parsed = urlparse(base)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            return urljoin(base, "models")
        return urljoin(base, "v1/models")
    return urljoin(base, "health")


def _candidate_local_model_paths(model: str, *, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    values = env or os.environ
    model_text = str(model)
    raw = Path(model_text).expanduser()
    paths: list[Path] = []
    if raw.is_absolute() or ("/" in model_text and Path(model_text).exists()):
        paths.append(raw)

    cache_roots = _huggingface_cache_roots(values)
    hf_name = "models--" + model_text.strip().replace("/", "--")
    for root in cache_roots:
        paths.append(root / hf_name)
        paths.append(root / hf_name / "snapshots")
    return tuple(str(path) for path in _dedupe_paths(paths))


def _huggingface_cache_roots(values: Mapping[str, str]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for key in ("SENTENCE_TRANSFORMERS_HOME", "TRANSFORMERS_CACHE", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if values.get(key):
            roots.append(Path(str(values[key])).expanduser())
    if values.get("HF_HOME"):
        roots.append(Path(str(values["HF_HOME"])).expanduser() / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    return tuple(_dedupe_paths(roots))


def _dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _embedding_snapshot(env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    values = env or os.environ
    provider = values.get("HINDSIGHT_API_EMBEDDINGS_PROVIDER") or "local"
    return {
        "provider": provider,
        "model": (
            values.get("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL")
            or values.get("HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_PATH")
            or values.get("HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID")
            or values.get("HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL")
            or "BAAI/bge-small-en-v1.5"
        ),
        "base_url": (
            values.get("HINDSIGHT_API_EMBEDDINGS_TEI_URL")
            or values.get("HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL")
            or None
        ),
    }


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def probe_hindsight_embeddings_sync(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(probe_hindsight_embeddings(env=env)).to_dict()

    raise RuntimeError("probe_hindsight_embeddings_sync cannot be called from a running event loop")


__all__ = [
    "HindsightEmbeddingProbeResult",
    "probe_hindsight_embeddings",
    "probe_hindsight_embeddings_sync",
]
