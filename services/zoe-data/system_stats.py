"""Host resource snapshot for the desktop dashboard's System card (Wave 4b, 2026-10-19).

Read-only. CPU, memory, disk and uptime come from psutil (zoe-data runs host-native, so these are
the box's own numbers); service health comes from short local probes of the services the household
actually depends on. Nothing is estimated: a reading psutil cannot give is ``None`` and the card
says so instead of showing a number (the card it replaces drew ``Math.random()``).

One snapshot is shared for ``_CACHE_TTL_S`` so several open dashboards cost one CPU sample and one
round of probes, not one each.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

from typed_env import env_str

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 5.0
_PROBE_TIMEOUT_S = 1.5
_CPU_SAMPLE_S = 0.25

_cache: dict[str, Any] = {"at": 0.0, "body": None}
_lock = asyncio.Lock()


def _pct(value: Any) -> Optional[float]:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def resource_snapshot(disk_path: str = "/") -> dict[str, Any]:
    """CPU / memory / disk / uptime. Each block is ``None`` when psutil cannot read it."""
    try:
        import psutil
    except Exception:  # noqa: BLE001 - the card says "unavailable", never a guess
        return {"cpu": None, "memory": None, "disk": None, "uptime_seconds": None}

    cpu: Optional[dict[str, Any]] = None
    try:
        load_1m = None
        try:
            load_1m = round(os.getloadavg()[0], 2)
        except (AttributeError, OSError):
            pass
        cpu = {
            "percent": _pct(psutil.cpu_percent(interval=_CPU_SAMPLE_S)),
            "cores": psutil.cpu_count(logical=True),
            "load_1m": load_1m,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("system_stats: cpu read failed (%s)", type(exc).__name__)

    memory: Optional[dict[str, Any]] = None
    try:
        vm = psutil.virtual_memory()
        memory = {
            "total_bytes": int(vm.total),
            "used_bytes": int(vm.total - vm.available),
            "available_bytes": int(vm.available),
            "percent": _pct(vm.percent),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("system_stats: memory read failed (%s)", type(exc).__name__)

    disk: Optional[dict[str, Any]] = None
    try:
        du = psutil.disk_usage(disk_path)
        disk = {
            "path": disk_path,
            "total_bytes": int(du.total),
            "used_bytes": int(du.used),
            "free_bytes": int(du.free),
            "percent": _pct(du.percent),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("system_stats: disk read failed (%s)", type(exc).__name__)

    uptime: Optional[int] = None
    try:
        uptime = max(0, int(time.time() - psutil.boot_time()))
    except Exception as exc:  # noqa: BLE001
        logger.debug("system_stats: uptime read failed (%s)", type(exc).__name__)

    return {"cpu": cpu, "memory": memory, "disk": disk, "uptime_seconds": uptime}


async def _http_status(url: str, judge: Callable[[httpx.Response], str]) -> str:
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.get(url)
    except Exception:  # noqa: BLE001 - unreachable is "down", never an error page
        return "down"
    try:
        return judge(resp)
    except Exception:  # noqa: BLE001 - a malformed answer is "degraded"
        return "degraded"


def _ok_if_200(resp: httpx.Response) -> str:
    return "ok" if resp.status_code == 200 else "degraded"


def _judge_voice(resp: httpx.Response) -> str:
    if resp.status_code != 200:
        return "degraded"
    body = resp.json()
    if not body.get("pipeline_loaded") or body.get("degraded"):
        return "degraded"
    return "ok"


def _judge_home_assistant(resp: httpx.Response) -> str:
    if resp.status_code != 200:
        return "degraded"
    body = resp.json()
    return "ok" if body.get("status") == "healthy" and body.get("ha_connected") else "degraded"


async def _database_status() -> str:
    """ok only if a pooled connection answers ``SELECT 1`` in time; anything else is "down".

    Deliberately NOT ``db_pool.check_pool_health``: that is a restart-watchdog probe, fail-OPEN by
    design (a failed query or an uninitialised pool reports healthy), so the card would read "ok"
    through a real outage. This card is fail-CLOSED.
    """

    async def _query() -> None:
        from db_pool import get_pool

        async with get_pool().acquire() as conn:
            await conn.fetchval("SELECT 1")

    try:
        await asyncio.wait_for(_query(), timeout=_PROBE_TIMEOUT_S)
        return "ok"
    except Exception:  # noqa: BLE001 - uninitialised pool, exhausted pool, failed query, timeout
        return "down"


def _service_probes() -> list[tuple[str, str, Callable[[], Awaitable[str]]]]:
    llama = env_str("ZOE_LLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    kokoro = env_str("ZOE_KOKORO_SIDECAR_URL", "http://127.0.0.1:10201").rstrip("/")
    bridge = env_str("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007").rstrip("/")
    return [
        ("database", "Database", _database_status),
        ("brain", "Brain", lambda: _http_status(f"{llama}/health", _ok_if_200)),
        ("voice", "Voice", lambda: _http_status(f"{kokoro}/health", _judge_voice)),
        ("home_assistant", "Home Assistant", lambda: _http_status(f"{bridge}/", _judge_home_assistant)),
    ]


async def probe_services() -> list[dict[str, str]]:
    """``[{id, name, status}]`` with status ``ok`` | ``degraded`` | ``down``. Never raises."""
    probes = _service_probes()
    results = await asyncio.gather(*(probe() for _id, _name, probe in probes), return_exceptions=True)
    return [
        {"id": sid, "name": name, "status": status if isinstance(status, str) else "down"}
        for (sid, name, _probe), status in zip(probes, results)
    ]


async def snapshot(*, use_cache: bool = True) -> dict[str, Any]:
    """The System card's payload: resources + services + when it was read."""
    async with _lock:
        if use_cache and _cache["body"] is not None and time.monotonic() - _cache["at"] < _CACHE_TTL_S:
            return _cache["body"]
        resources, services = await asyncio.gather(
            asyncio.to_thread(resource_snapshot), probe_services()
        )
        body = {
            **resources,
            "services": services,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _cache.update(at=time.monotonic(), body=body)
        return body
