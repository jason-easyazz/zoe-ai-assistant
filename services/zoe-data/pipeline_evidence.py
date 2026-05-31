"""Evidence-gated engineering pipeline state.

This is intentionally independent of the live Kanban adapter for now. Phase 2
defines the contract; the verify-phase integration can adopt it in a smaller PR.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PipelinePhase = Literal["implement", "verify", "review", "closeout", "retro"]
PipelineStatus = Literal["todo", "running", "blocked", "done"]
EvidenceKind = Literal["tool", "test", "validator", "pr", "greptile", "human", "log"]
TransitionOutcome = Literal[
    "start",
    "complete",
    "block",
    "request_changes",
    "verification_failed",
    "merge_blocked",
]

PHASE_ORDER: tuple[PipelinePhase, ...] = ("implement", "verify", "review", "closeout", "retro")

_REQUIRED_EVIDENCE: dict[PipelinePhase, set[EvidenceKind]] = {
    "implement": {"tool"},
    "verify": {"test", "validator"},
    "review": {"human"},
    "closeout": {"greptile"},
    "retro": {"log"},
}


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    summary: str = Field(min_length=1, max_length=500)
    command: str | None = Field(default=None, max_length=500)
    artifact: str | None = Field(default=None, max_length=1000)
    passed: bool | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _no_secret_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"api_key", "token", "password", "secret"}
        leaked = sorted(k for k in value if k.lower() in forbidden)
        if leaked:
            raise ValueError(f"Evidence metadata may not contain secret fields: {', '.join(leaked)}")
        return value


class TransitionRecord(BaseModel):
    from_phase: PipelinePhase
    to_phase: PipelinePhase
    outcome: TransitionOutcome
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str | None = None


class PipelineState(BaseModel):
    schema_version: int = 1
    task_ref: str
    phase: PipelinePhase = "implement"
    status: PipelineStatus = "todo"
    attempts: dict[PipelinePhase, int] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    history: list[TransitionRecord] = Field(default_factory=list)

    @field_validator("task_ref")
    @classmethod
    def _task_ref_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_ref is required")
        return value.strip()


def evidence_kinds(state: PipelineState) -> set[EvidenceKind]:
    return {item.kind for item in state.evidence if item.passed is not False}


def missing_required_evidence(state: PipelineState, phase: PipelinePhase | None = None) -> set[EvidenceKind]:
    selected = phase or state.phase
    return _REQUIRED_EVIDENCE.get(selected, set()) - evidence_kinds(state)


def can_complete_phase(state: PipelineState) -> bool:
    return not missing_required_evidence(state)


def with_evidence(state: PipelineState, *items: EvidenceItem) -> PipelineState:
    return state.model_copy(update={"evidence": [*state.evidence, *items]})


def transition(state: PipelineState, outcome: TransitionOutcome, *, reason: str | None = None) -> PipelineState:
    if outcome == "start":
        next_phase = state.phase
        next_status: PipelineStatus = "running"
    elif outcome == "block":
        next_phase = state.phase
        next_status = "blocked"
    elif outcome in {"request_changes", "verification_failed"}:
        next_phase = "implement"
        next_status = "todo"
    elif outcome == "merge_blocked":
        next_phase = "closeout"
        next_status = "blocked"
    elif outcome == "complete":
        if not can_complete_phase(state):
            missing = ", ".join(sorted(missing_required_evidence(state)))
            raise ValueError(f"{state.phase} is missing required evidence: {missing}")
        current_idx = PHASE_ORDER.index(state.phase)
        if current_idx == len(PHASE_ORDER) - 1:
            next_phase = state.phase
            next_status = "done"
        else:
            next_phase = PHASE_ORDER[current_idx + 1]
            next_status = "todo"
    else:
        raise ValueError(f"Unsupported outcome: {outcome}")

    attempts = dict(state.attempts)
    if next_status == "running":
        attempts[state.phase] = attempts.get(state.phase, 0) + 1
    history = [
        *state.history,
        TransitionRecord(from_phase=state.phase, to_phase=next_phase, outcome=outcome, reason=reason),
    ]
    return state.model_copy(update={"phase": next_phase, "status": next_status, "attempts": attempts, "history": history})

