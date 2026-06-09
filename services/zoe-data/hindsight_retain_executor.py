"""Execute admitted Hindsight retain plans.

This module is the first runtime handoff after Zoe's memory admission gate. It
does not build admission decisions and it does not accept raw memory events; it
only executes an already-admitted retain plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from hindsight_memory import HindsightConfig, HindsightMemoryClient
from hindsight_retain_candidates import HindsightAdmittedRetainPlan


@dataclass(frozen=True)
class HindsightRetainExecutionResult:
    admission_id: str
    event_id: str
    bank_id: str
    attempted: bool
    retained: bool
    reason: str
    evidence_refs: tuple[str, ...]
    sidecar_result: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "event_id": self.event_id,
            "bank_id": self.bank_id,
            "attempted": self.attempted,
            "retained": self.retained,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "sidecar_result": dict(self.sidecar_result),
        }


async def execute_admitted_hindsight_retain_plan(
    plan: HindsightAdmittedRetainPlan,
    *,
    client: HindsightMemoryClient | None = None,
    config: HindsightConfig | None = None,
) -> HindsightRetainExecutionResult:
    """Execute an admitted retain plan against the Hindsight sidecar.

    Hindsight remains disabled by default through ``HindsightConfig``. When it
    is disabled, this returns a non-attempt result and performs no network call.
    """

    hindsight_client = client or HindsightMemoryClient(config)
    result = await hindsight_client.retain_payload(
        bank_id=plan.bank_id,
        payload=plan.payload,
        event_id=plan.event_id,
    )
    retained = bool(result.get("retained"))
    reason = _execution_reason(result, retained)
    return HindsightRetainExecutionResult(
        admission_id=plan.admission_id,
        event_id=plan.event_id,
        bank_id=plan.bank_id,
        attempted=bool(result.get("enabled", True)),
        retained=retained,
        reason=reason,
        evidence_refs=plan.evidence_refs,
        sidecar_result=result,
    )


def _execution_reason(result: Mapping[str, Any], retained: bool) -> str:
    if retained:
        return "retained"
    reason = str(result.get("reason") or "").strip()
    if reason:
        return reason
    if result.get("success") is False:
        return "sidecar_rejected"
    return "not_retained"


__all__ = [
    "HindsightRetainExecutionResult",
    "execute_admitted_hindsight_retain_plan",
]
