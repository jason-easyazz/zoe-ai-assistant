"""Execute admitted Zoe evolution outcome memory through Hindsight.

This is the durable writer bridge for verified self-evolution outcomes. It
stays behind the existing memory admission gate and the Hindsight executor; it
does not accept raw unadmitted candidates and it does not wire production chat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from hindsight_memory import HindsightConfig, HindsightMemoryClient
from hindsight_retain_candidates import HindsightRetainAdmissionError, build_admitted_hindsight_retain_plan
from hindsight_retain_executor import HindsightRetainExecutionResult, execute_admitted_hindsight_retain_plan
from zoe_evolution_outcome_admission import EvolutionOutcomeAdmissionResult, evaluate_evolution_outcome_admission
from zoe_evolution_proposal import EvolutionProposal
from zoe_observation_trace import ObservationTrace


@dataclass(frozen=True)
class EvolutionOutcomeRetainResult:
    admission: EvolutionOutcomeAdmissionResult
    execution: HindsightRetainExecutionResult | None
    retained: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission": self.admission.to_dict(),
            "execution": self.execution.to_dict() if self.execution else None,
            "retained": self.retained,
            "reason": self.reason,
        }


async def retain_admitted_evolution_outcome_in_hindsight(
    admission: EvolutionOutcomeAdmissionResult,
    *,
    client: HindsightMemoryClient | None = None,
    config: HindsightConfig | None = None,
) -> EvolutionOutcomeRetainResult:
    """Retain an already-evaluated evolution outcome when admission approves it."""

    try:
        plan = build_admitted_hindsight_retain_plan(
            admission.request,
            admission.decision,
            config=config or (client.config if client else None),
        )
    except HindsightRetainAdmissionError as exc:
        return EvolutionOutcomeRetainResult(
            admission=admission,
            execution=None,
            retained=False,
            reason=str(exc),
        )

    execution = await execute_admitted_hindsight_retain_plan(plan, client=client, config=config)
    return EvolutionOutcomeRetainResult(
        admission=admission,
        execution=execution,
        retained=execution.retained,
        reason=execution.reason,
    )


async def evaluate_and_retain_evolution_outcome_in_hindsight(
    proposal: EvolutionProposal,
    traces: Sequence[ObservationTrace],
    *,
    admission_id: str | None = None,
    approval_refs: Sequence[str] = (),
    user_id: str | None = None,
    scope: str | None = None,
    event_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    client: HindsightMemoryClient | None = None,
    config: HindsightConfig | None = None,
) -> EvolutionOutcomeRetainResult:
    """Evaluate outcome admission and execute Hindsight retain only if approved."""

    admission = evaluate_evolution_outcome_admission(
        proposal,
        traces,
        admission_id=admission_id,
        approval_refs=approval_refs,
        user_id=user_id,
        scope=scope,
        event_id=event_id,
        metadata=metadata,
    )
    return await retain_admitted_evolution_outcome_in_hindsight(
        admission,
        client=client,
        config=config,
    )


__all__ = [
    "EvolutionOutcomeRetainResult",
    "evaluate_and_retain_evolution_outcome_in_hindsight",
    "retain_admitted_evolution_outcome_in_hindsight",
]
