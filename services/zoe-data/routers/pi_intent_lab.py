"""Admin-only Pi intent lab router."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_admin
from pi_intent_lab import compare_pi_intent_lab

router = APIRouter(prefix="/api/pi-intent-lab", tags=["pi-intent-lab"])


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


@router.post("/compare")
async def compare_pi_intent(payload: PiIntentLabCompareRequest, user: dict = Depends(require_admin)):
    """Compare Zoe router, optional Zoe Agent fallback, and standalone Pi without dispatching."""
    try:
        return await compare_pi_intent_lab(
            payload.text,
            user_id=str(user.get("user_id") or "admin"),
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
