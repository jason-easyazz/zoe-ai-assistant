"""Read-only Graphiti graph-backend readiness probe for Zoe."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class GraphitiProbeConfigError(ValueError):
    """Raised when visible Graphiti probe config is invalid or non-local."""


@dataclass(frozen=True)
class GraphitiProbeConfig:
    enabled: bool = False
    backend: str = "falkordb"
    falkordb_host: str = "127.0.0.1"
    falkordb_port: int = 6379
    neo4j_host: str = "127.0.0.1"
    neo4j_bolt_port: int = 7687
    offline_only: bool = True
    timeout_seconds: float = 1.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GraphitiProbeConfig":
        config = cls(**_config_values(env, tolerant=False))
        config.validate()
        return config

    @classmethod
    def snapshot_from_env(cls, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        return _config_values(env, tolerant=True)

    def validate(self) -> None:
        if self.backend not in {"falkordb", "neo4j"}:
            raise GraphitiProbeConfigError("GRAPHITI_BACKEND must be falkordb or neo4j")
        if (
            self.falkordb_port <= 0
            or self.falkordb_port > 65535
            or self.neo4j_bolt_port <= 0
            or self.neo4j_bolt_port > 65535
        ):
            raise GraphitiProbeConfigError("Graphiti backend ports must be integers in range 1-65535")
        if self.timeout_seconds <= 0:
            raise GraphitiProbeConfigError("GRAPHITI_PROBE_TIMEOUT_SECONDS must be positive")
        if not self.offline_only:
            return
        for host in (self.falkordb_host, self.neo4j_host):
            if not _is_local_or_private_host(host):
                raise GraphitiProbeConfigError("Graphiti backend hosts must be localhost or private network when offline_only is enabled")

    def target(self) -> tuple[str, int]:
        if self.backend == "neo4j":
            return self.neo4j_host, self.neo4j_bolt_port
        return self.falkordb_host, self.falkordb_port

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "falkordb_host": self.falkordb_host,
            "falkordb_port": self.falkordb_port,
            "neo4j_host": self.neo4j_host,
            "neo4j_bolt_port": self.neo4j_bolt_port,
            "offline_only": self.offline_only,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class GraphitiSidecarProbeResult:
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


async def probe_graphiti_sidecar(
    *,
    env: Mapping[str, str] | None = None,
    include_process_scan: bool = True,
) -> GraphitiSidecarProbeResult:
    """Probe Graphiti backend readiness without ingesting episodes or writing graph data."""

    started = time.perf_counter()
    process_hits: tuple[str, ...] = ()
    container_hits: tuple[str, ...] = ()
    if include_process_scan:
        process_hits, container_hits = await _scan_runtime()

    try:
        config = GraphitiProbeConfig.from_env(env)
    except (GraphitiProbeConfigError, ValueError) as exc:
        return GraphitiSidecarProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            config=GraphitiProbeConfig.snapshot_from_env(env),
            health={},
            process_hits=process_hits,
            container_hits=container_hits,
            latency_ms=_elapsed_ms(started),
            reason=str(exc),
        )

    if not config.enabled:
        return GraphitiSidecarProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            config=config.to_dict(),
            health={"enabled": False, "healthy": False, "reason": "disabled"},
            process_hits=process_hits,
            container_hits=container_hits,
            latency_ms=_elapsed_ms(started),
            reason="GRAPHITI_ENABLED is false",
        )

    host, port = config.target()
    reachable, reason = await asyncio.to_thread(_tcp_check, host, port, config.timeout_seconds)
    health = {
        "backend": config.backend,
        "host": host,
        "port": port,
        "tcp_reachable": reachable,
    }
    return GraphitiSidecarProbeResult(
        ok=reachable,
        acceptable=reachable,
        status="healthy" if reachable else "offline",
        config=config.to_dict(),
        health=health,
        process_hits=process_hits,
        container_hits=container_hits,
        latency_ms=_elapsed_ms(started),
        reason=None if reachable else reason,
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _config_values(env: Mapping[str, str] | None = None, *, tolerant: bool) -> dict[str, Any]:
    values = env or os.environ
    bool_reader = _env_bool_snapshot if tolerant else _env_bool
    port_reader = _env_int_snapshot if tolerant else _env_int
    timeout_reader = _env_float_snapshot if tolerant else _env_float
    return {
        "enabled": bool_reader(values.get("GRAPHITI_ENABLED"), default=False),
        "backend": (values.get("GRAPHITI_BACKEND") or "falkordb").strip().lower(),
        "falkordb_host": (values.get("GRAPHITI_FALKORDB_HOST") or "127.0.0.1").strip(),
        "falkordb_port": port_reader(values.get("GRAPHITI_FALKORDB_PORT"), default=6379),
        "neo4j_host": (values.get("GRAPHITI_NEO4J_HOST") or "127.0.0.1").strip(),
        "neo4j_bolt_port": port_reader(values.get("GRAPHITI_NEO4J_BOLT_PORT"), default=7687),
        "offline_only": bool_reader(values.get("GRAPHITI_OFFLINE_ONLY"), default=True),
        "timeout_seconds": timeout_reader(values.get("GRAPHITI_PROBE_TIMEOUT_SECONDS"), default=1.0),
    }


async def _scan_runtime() -> tuple[tuple[str, ...], tuple[str, ...]]:
    process_hits, container_hits = await asyncio.gather(
        asyncio.to_thread(_scan_processes, ("graphiti", "falkordb", "neo4j")),
        asyncio.to_thread(_scan_containers, ("graphiti", "falkordb", "neo4j")),
    )
    return tuple(process_hits), tuple(container_hits)


def _tcp_check(host: str, port: int, timeout_seconds: float) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, None
    except OSError as exc:
        return False, str(exc)


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
        if "graphiti_sidecar_probe.py" in lowered or " -m graphiti_sidecar_probe" in lowered:
            continue
        if any(marker in lowered for marker in lowered_markers):
            matches.append(" ".join(line.split())[:240])
    return matches


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


def _env_int(value: str | None, *, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(str(value).strip())


def _env_float(value: str | None, *, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(str(value).strip())


def _env_int_snapshot(value: str | None, *, default: int) -> int | str:
    try:
        return _env_int(value, default=default)
    except ValueError:
        return str(value)


def _env_float_snapshot(value: str | None, *, default: float) -> float | str:
    try:
        return _env_float(value, default=default)
    except ValueError:
        return str(value)


def _is_local_or_private_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = str(host).strip().lower()
    if normalized in {"localhost", "zoe", "host.docker.internal"}:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized.endswith(".local") or normalized.endswith(".lan")
    return ip.is_loopback or ip.is_private or ip.is_link_local


def probe_graphiti_sidecar_sync(
    *,
    env: Mapping[str, str] | None = None,
    include_process_scan: bool = True,
) -> dict[str, Any]:
    """CLI-only sync wrapper; async service callers should await probe_graphiti_sidecar."""

    return asyncio.run(probe_graphiti_sidecar(env=env, include_process_scan=include_process_scan)).to_dict()


__all__ = [
    "GraphitiProbeConfig",
    "GraphitiProbeConfigError",
    "GraphitiSidecarProbeResult",
    "probe_graphiti_sidecar",
    "probe_graphiti_sidecar_sync",
]
