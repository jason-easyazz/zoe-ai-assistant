"""Admin-only Pi intent lab router."""

from __future__ import annotations

import asyncio
import json
import time
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
    return _json_line(payload)


def _safe_error_message(exc: Exception, *, limit: int = 200) -> str:
    return str(exc)[:limit]


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
