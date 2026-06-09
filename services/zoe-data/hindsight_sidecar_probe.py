"""Read-only Hindsight sidecar readiness probe for Zoe."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from hindsight_memory import HindsightConfig, HindsightMemoryClient, HindsightMemoryError, HindsightOfflineConfigError


@dataclass(frozen=True)
class HindsightSidecarProbeResult:
    ok: bool
    acceptable: bool
    status: str
    config: dict[str, Any]
    health: dict[str, Any]
    process_hits: tuple[str, ...]
    container_hits: tuple[str, ...]
    latency_ms: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acceptable": self.acceptable,
            "status": self.status,
            "config": self.config,
            "health": self.health,
            "process_hits": list(self.process_hits),
            "container_hits": list(self.container_hits),
            "latency_ms": self.latency_ms,
            "reason": self.reason,
        }


async def probe_hindsight_sidecar(
    *,
    env: Mapping[str, str] | None = None,
    include_process_scan: bool = True,
) -> HindsightSidecarProbeResult:
    """Probe Hindsight readiness without retain, recall, or memory writes."""

    started = time.perf_counter()
    process_hits: tuple[str, ...] = ()
    container_hits: tuple[str, ...] = ()
    if include_process_scan:
        process_hits, container_hits = await _scan_runtime()

    try:
        config = HindsightConfig.from_env(env)
    except (HindsightOfflineConfigError, ValueError) as exc:
        return HindsightSidecarProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            config=_config_snapshot(env),
            health={},
            process_hits=process_hits,
            container_hits=container_hits,
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    client = HindsightMemoryClient(config)

    if not config.enabled:
        return HindsightSidecarProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            config=client.enabled_status(),
            health={"enabled": False, "healthy": False, "reason": "disabled"},
            process_hits=process_hits,
            container_hits=container_hits,
            latency_ms=_elapsed_ms(started),
            reason="HINDSIGHT_ENABLED is false",
        )

    try:
        health = await client.health()
    except HindsightMemoryError as exc:
        return HindsightSidecarProbeResult(
            ok=False,
            acceptable=False,
            status="offline",
            config=client.enabled_status(),
            health={},
            process_hits=process_hits,
            container_hits=container_hits,
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    healthy = bool(health.get("ok") or health.get("healthy") or health.get("status") in {"ok", "healthy"})
    return HindsightSidecarProbeResult(
        ok=healthy,
        acceptable=healthy,
        status="healthy" if healthy else "unhealthy",
        config=client.enabled_status(),
        health=health,
        process_hits=process_hits,
        container_hits=container_hits,
        latency_ms=_elapsed_ms(started),
        reason=None if healthy else "health response did not report ok/healthy",
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _config_snapshot(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = env or os.environ
    return {
        "enabled": _env_bool_snapshot(values.get("HINDSIGHT_ENABLED"), default=False),
        "base_url": (values.get("HINDSIGHT_BASE_URL") or "http://127.0.0.1:8888").rstrip("/"),
        "bank_prefix": _slug_snapshot(values.get("HINDSIGHT_BANK_PREFIX") or "zoe"),
        "auto_retain": _env_bool_snapshot(values.get("HINDSIGHT_AUTO_RETAIN"), default=False),
        "async_retain": _env_bool_snapshot(values.get("HINDSIGHT_ASYNC_RETAIN"), default=True),
        "offline_only": _env_bool_snapshot(values.get("HINDSIGHT_OFFLINE_ONLY"), default=True),
        "llm_provider": values.get("HINDSIGHT_API_LLM_PROVIDER") or values.get("HINDSIGHT_LLM_PROVIDER") or None,
        "llm_base_url": values.get("HINDSIGHT_API_LLM_BASE_URL") or values.get("HINDSIGHT_LLM_BASE_URL") or None,
    }


def _env_bool_snapshot(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _slug_snapshot(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip().lower()).strip("-")
    return slug or "default"


async def _scan_runtime() -> tuple[tuple[str, ...], tuple[str, ...]]:
    process_hits, container_hits = await asyncio.gather(
        asyncio.to_thread(_scan_processes, ("hindsight",)),
        asyncio.to_thread(_scan_containers, ("hindsight",)),
    )
    return tuple(process_hits), tuple(container_hits)


def _scan_processes(markers: Sequence[str]) -> list[str]:
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return _matching_lines(proc.stdout.splitlines(), markers)


def _scan_containers(markers: Sequence[str]) -> list[str]:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Status}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return _matching_lines(proc.stdout.splitlines(), markers)


def _matching_lines(lines: Sequence[str], markers: Sequence[str]) -> list[str]:
    lowered_markers = tuple(marker.lower() for marker in markers)
    matches = []
    for line in lines:
        lowered = line.lower()
        if "hindsight_sidecar_probe.py" in lowered or " -m hindsight_sidecar_probe" in lowered:
            continue
        if any(marker in lowered for marker in lowered_markers):
            matches.append(" ".join(line.split())[:240])
    return matches


def probe_hindsight_sidecar_sync(
    *,
    env: Mapping[str, str] | None = None,
    include_process_scan: bool = True,
) -> dict[str, Any]:
    """CLI-only sync wrapper; async service callers should await probe_hindsight_sidecar."""

    return asyncio.run(
        probe_hindsight_sidecar(env=env, include_process_scan=include_process_scan)
    ).to_dict()


__all__ = [
    "HindsightSidecarProbeResult",
    "probe_hindsight_sidecar",
    "probe_hindsight_sidecar_sync",
]
