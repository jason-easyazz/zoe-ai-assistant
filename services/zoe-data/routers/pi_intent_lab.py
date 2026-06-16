"""Admin-only Pi intent lab router."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import auth
from pi_intent_lab import compare_pi_intent_lab

router = APIRouter(prefix="/api/pi-intent-lab", tags=["pi-intent-lab"])


async def require_lab_operator(request: Request) -> dict:
    """Allow admin users or Zoe internal loopback/token callers to use the lab."""
    try:
        await auth.require_internal_token(request)
        return {"user_id": "internal-pi-intent-lab", "role": "admin", "auth_path": "internal"}
    except HTTPException as internal_exc:
        if request.headers.get("X-Internal-Token") and getattr(auth, "_ZOE_INTERNAL_TOKEN", ""):
            raise internal_exc

    user = await auth.get_current_user(request)
    return await auth.require_admin(user)


class PiIntentLabCompareRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    context_turns: str = Field(default="", max_length=2000)
    run_pi: bool = True
    pi_transport: Literal["rpc", "print"] | None = "rpc"
    allow_pi_execution: bool | None = None
    local_model_configured: bool | None = None
    measure_zoe_agent_baseline: bool = False
    zoe_agent_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    zoe_agent_max_tokens: int = Field(default=64, gt=0, le=512)
    include_hybrid_status: bool = True
    include_safe_fulfillment: bool = False
    safe_fulfillment_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=90)


@router.post("/compare")
async def compare_pi_intent(payload: PiIntentLabCompareRequest, user: dict = Depends(require_lab_operator)):
    """Compare Zoe router, optional Zoe Agent fallback, and standalone Pi without dispatching."""
    pressure = await _pi_lab_resource_pressure_blocker(payload)
    if pressure:
        raise HTTPException(status_code=503, detail=pressure)
    try:
        return await asyncio.wait_for(
            _run_compare(payload, user_id=str(user.get("user_id") or "admin")),
            timeout=payload.request_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Pi intent lab comparison timed out") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/hybrid-stream")
async def stream_pi_hybrid_flow(payload: PiIntentLabCompareRequest, user: dict = Depends(require_lab_operator)):
    """Stream the lab hybrid flow as cue-first NDJSON, then Pi/final evidence."""
    return StreamingResponse(
        _hybrid_stream_events(payload, user),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_compare(payload: PiIntentLabCompareRequest, *, user_id: str) -> dict[str, Any]:
    return await compare_pi_intent_lab(
        payload.text,
        user_id=user_id,
        context_turns=payload.context_turns,
        run_pi=payload.run_pi,
        pi_transport=payload.pi_transport,
        allow_pi_execution=payload.allow_pi_execution,
        local_model_configured=payload.local_model_configured,
        measure_zoe_agent_baseline=payload.measure_zoe_agent_baseline,
        zoe_agent_timeout_seconds=payload.zoe_agent_timeout_seconds,
        zoe_agent_max_tokens=payload.zoe_agent_max_tokens,
        include_hybrid_status=payload.include_hybrid_status,
        include_safe_fulfillment=payload.include_safe_fulfillment,
        safe_fulfillment_timeout_seconds=payload.safe_fulfillment_timeout_seconds,
    )


async def _hybrid_stream_events(payload: PiIntentLabCompareRequest, user: Mapping[str, Any]) -> AsyncIterator[str]:
    started = time.perf_counter()
    cue = _safe_processing_cue()
    yield _json_line(
        {
            "event": "processing_cue",
            "phase": "cue",
            "elapsed_ms": _elapsed_ms(started),
            "cue": cue,
            "contract": {
                "admin_only": True,
                "production_route_change": False,
                "memory_writes_enabled": False,
                "shadow_writes_enabled": False,
                "promotion_enabled": False,
            },
        }
    )
    pressure = await _pi_lab_resource_pressure_blocker(payload)
    if pressure:
        yield _stream_error(
            started,
            error=str(pressure.get("detail") or "Pi intent lab blocked by resource pressure"),
            error_type="resource_pressure",
            resource=pressure,
        )
        return
    try:
        result = await asyncio.wait_for(
            _run_compare(payload, user_id=str(user.get("user_id") or "admin")),
            timeout=payload.request_timeout_seconds,
        )
    except asyncio.TimeoutError:
        yield _stream_error(started, error="Pi intent lab comparison timed out", error_type="timeout")
        return
    except ValueError as exc:
        yield _stream_error(started, error=_safe_error_message(exc), error_type="validation")
        return
    except Exception as exc:  # pragma: no cover - defensive streaming guard
        yield _stream_error(
            started,
            error=_safe_error_message(exc),
            error_type="exception",
            exception_class=exc.__class__.__name__,
        )
        return

    yield _json_line(
        {
            "event": "final",
            "phase": "final",
            "elapsed_ms": _elapsed_ms(started),
            "result": result,
            "production_route_change": False,
        }
    )


def _safe_processing_cue() -> dict[str, Any]:
    try:
        return _processing_cue()
    except Exception as exc:  # pragma: no cover - defensive streaming guard
        return {
            "available": False,
            "latency_ms": None,
            "event": None,
            "text": "",
            "error": _safe_error_message(exc),
            "error_type": exc.__class__.__name__,
        }


def _processing_cue() -> dict[str, Any]:
    from voice_presence import processing_ack_event

    started = time.perf_counter()
    event = processing_ack_event(index=0)
    return {
        "available": event is not None,
        "latency_ms": _elapsed_ms(started),
        "event": _safe_voice_event(event),
        "text": str((event or {}).get("text") or ""),
    }


def _safe_voice_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    safe = dict(event)
    audio = safe.pop("audio_base64", None)
    if audio:
        safe["audio_base64_chars"] = len(str(audio))
    return safe


def _stream_error(
    started: float,
    *,
    error: str,
    error_type: str,
    exception_class: str | None = None,
    resource: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "event": "error",
        "phase": "final",
        "elapsed_ms": _elapsed_ms(started),
        "error": error,
        "error_type": error_type,
        "production_route_change": False,
    }
    if exception_class:
        payload["exception_class"] = exception_class
    if resource:
        payload["resource"] = dict(resource)
    return _json_line(payload)


async def _pi_lab_resource_pressure_blocker(payload: PiIntentLabCompareRequest) -> dict[str, Any] | None:
    if not _env_bool("ZOE_PI_LAB_RESOURCE_GUARD_ENABLED", default=True):
        return None
    if not (payload.run_pi or payload.include_safe_fulfillment or payload.measure_zoe_agent_baseline):
        return None
    min_available_mb = _env_float("ZOE_PI_LAB_MIN_AVAILABLE_MB", 2048.0)
    min_swap_free_mb = _env_float("ZOE_PI_LAB_MIN_SWAP_FREE_MB", 256.0)
    if min_available_mb <= 0 and min_swap_free_mb <= 0:
        return None
    mem = await asyncio.to_thread(_read_meminfo_mb)
    if not mem:
        return None
    available_mb = mem.get("MemAvailable")
    swap_free_mb = mem.get("SwapFree")
    blockers: list[str] = []
    if min_available_mb > 0 and available_mb is not None and available_mb < min_available_mb:
        blockers.append("available_memory_below_threshold")
    if min_swap_free_mb > 0 and swap_free_mb is not None and swap_free_mb < min_swap_free_mb:
        blockers.append("swap_free_below_threshold")
    if not blockers:
        return None
    return {
        "error_type": "resource_pressure",
        "detail": "Pi intent lab blocked to avoid zoe-data OOM restart",
        "blockers": blockers,
        "available_mb": available_mb,
        "swap_free_mb": swap_free_mb,
        "min_available_mb": int(min_available_mb),
        "min_swap_free_mb": int(min_swap_free_mb),
        "production_route_change": False,
    }


def _read_meminfo_mb(path: str = "/proc/meminfo") -> dict[str, int] | None:
    try:
        target = Path(path)
        if not target.is_file():
            return None
        values: dict[str, int] = {}
        for raw in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = raw.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                if key in {"MemAvailable", "MemFree", "Buffers", "Cached", "SwapFree"}:
                    values[key] = int(parts[1]) // 1024
        if "MemAvailable" not in values and "MemFree" in values:
            values["MemAvailable"] = values["MemFree"] + values.get("Buffers", 0) + values.get("Cached", 0)
        return values
    except (OSError, ValueError, IndexError):
        return None


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _safe_error_message(exc: Exception, *, limit: int = 200) -> str:
    return str(exc)[:limit]


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
